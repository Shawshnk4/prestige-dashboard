"""PRESTIGE production model. Reproduces the research chain exactly (v3_daily -> f5_styles -> f7_expiry -> n50e1_run ->
n50mb_run variant A, with the corrected SECTOR factor), on the data assembled by data_store.

Model    Nifty 50 point-in-time universe, 15 long / 15 short.
         MOM     RSI(14) -> EMA(9) -> Bollinger(20, 1.5) setup on daily and weekly bars, calibrated score tables
                 (fitted on 2011-18 and frozen), 60% weekly / 40% daily, capped at +-3
         IR1     stock vs its sector peers, information ratio over 42 days, contrarian (sign -1)
         SECTOR  equal-weight Nifty 100 sector baskets vs the Nifty 50 TRI (IR 42/63 days) blended 50/50 with the
                 sector's average MOM
         IR1F    residual of each stock's daily return on the equal-weight universe and its style index over the
                 last 252 days, blended IR, contrarian
         Weights walk-forward ICIR: 36 monthly ICs, 2-cycle lag, negatives -> 0, 50% shrink to equal
         Book    score minus its style-group average; top 15 long, bottom 15 short; inverse 63-day-vol weights
Timing   signal at the close one session before the rebalance; trade at the rebalance close."""
from __future__ import annotations
import json
from dataclasses import dataclass, field
from pathlib import Path
import numpy as np, pandas as pd

from .research import v2_core as C, sectors as SECS
from .research import f5_styles as STY
from . import calendar as CAL

ROOT = Path(__file__).resolve().parent.parent / "data"
STYLES = ["Quality Defensive", "Growth Defensive", "Value Core", "Value Cyclical", "Growth Cyclical", "Growth Speculative"]
FACTORS = ["MOM", "IR1", "SECTOR", "IR1F"]


def _params():
    return json.load(open(ROOT / "model" / "params.json"))


def _tables():
    T = json.load(open(ROOT / "model" / "score_tables.json"))
    out = {}
    for k, t in T.items():
        out[k] = C.ScoreTable(np.array(t["beta"]), {int(p): np.array(v) for p, v in t["beta_par"].items()}, t["names"],
                              np.array(t["grp"]), t["rsi_edges"], t["is_end"], t["cv"])
    return out


def _tf(d):
    return C.TFParams(d["rsi_len"], d["smooth_type"], d["smooth_len"], d["bb_len"], d["bb_k"], d["slope_bars"],
                      d["cross_bars"], d["reject_bars"], d["reject_drop"])


def _sector_map():
    s = pd.read_csv(ROOT / "ref" / "sectors.csv").set_index("symbol")["sector"]
    SECS.SYMBOL_TO_SECTOR.update(s.to_dict())          # same dict object v2_core imported


def wf_weights(ics, window=36, min_obs=18, shrink=0.5, lag=2):
    k = ics.shape[1]
    W = pd.DataFrame(np.nan, index=ics.index, columns=ics.columns)
    for i in range(len(ics)):
        h = ics.iloc[max(0, i - lag - window + 1): max(0, i - lag + 1)].dropna(how="all")
        if len(h) < min_obs:
            w = np.ones(k) / k
        else:
            x = (h.mean() / h.std(ddof=1)).clip(lower=0).fillna(0).values
            w = x / x.sum() if x.sum() > 0 else np.ones(k) / k
            w = (1 - shrink) * w + shrink / k
        W.iloc[i] = w
    return W


@dataclass
class Model:
    data: dict
    extras: dict
    holidays: set = field(default_factory=CAL.load_holidays)
    # factor values are computed from this date on (row-wise identical to a full-history run; the walk-forward weights
    # need 38 monthly ICs, so any start at least ~3.5 years back gives the same current weights and books).
    # None = full history (used by the reproduction test).
    start: str | None = "2019-01-01"

    def __post_init__(self):
        _sector_map()
        pr = self.pr = _params()
        ctx = self.ctx = C.Ctx(data=self.data)
        for c in ctx.cols:
            if c not in SECS.SYMBOL_TO_SECTOR:
                raise KeyError(f"{c}: no sector in data/ref/sectors.csv")
        self.cal = ctx.dates; self.END = self.cal[-1]
        self.tc = CAL.TradingCalendar(self.cal, self.holidays)
        self._completed_weeks_only()
        self._factors(pr)
        self._styles()
        self.RAW_IR1F = self._resid_raw(pr["ir1f"]["window"])
        r = ctx.ret
        self.VOL = r.rolling(pr["book"]["vol_window"], min_periods=pr["book"]["vol_min"]).std() * np.sqrt(252)
        self.cf = ctx.close.ffill(limit=10)
        self._universe()
        self.E = CAL.expiries(self.tc)
        self._weights()

    # ------------------------------------------------------------------ weekly bars: completed weeks only
    def _completed_weeks_only(self):
        """The weekly chart uses completed weeks only (methodology 3.1). In history every week is complete; at the end
        of the data the current week is still open unless the latest close is that week's last session, so its bar is
        dropped (otherwise a mid-week close would be read as a weekly close)."""
        ctx = self.ctx
        self.week_open = self.tc.next_session(self.END).to_period("W-FRI") == self.END.to_period("W-FRI")
        if self.week_open:
            ctx.wclose = ctx.wclose.iloc[:-1]
            ctx.wpos_of_day = np.searchsorted(ctx.wclose.index.values, ctx.dates.values, side="right") - 1
            ctx._rsi_cache = {}

    # ------------------------------------------------------------------ factors (v3_daily + corrected SECTOR)
    def _factors(self, pr):
        ctx = self.ctx
        pD, pW = _tf(pr["D"]), _tf(pr["W"])
        fD, fW = ctx.feats(pD, False), ctx.feats(pW, True)
        tab = _tables()
        DD = ctx.dates[ctx.dates >= ctx.me[0]]
        if self.start is not None:
            DD = DD[DD >= pd.Timestamp(self.start)]
        ELIG = ctx.elig.loc[DD]
        zc = lambda df: C.cs_z(df.where(ELIG.reindex(df.index)))
        pDd = tab["D"].parts(ctx, fD, DD, False)
        pWd = tab["W"].parts(ctx, fW, DD, True)
        for k in pDd:
            pDd[k] = pDd[k].where(ELIG); pWd[k] = pWd[k].where(ELIG)
        w = pr["w_weekly"]
        blendDW = lambda a, b: zc(w * zc(a) + (1 - w) * zc(b))
        MOM = blendDW(pWd["ALL"], pDd["ALL"]).clip(-pr["z_cap"], pr["z_cap"])
        peer = C.peer_loo(ctx)
        act1 = (ctx.ret - peer).where(ctx.member | ctx.member.shift(-1, fill_value=False))
        IR1 = pr["ir1_sign"] * zc(C.blend(act1, pr["w_ir1_2m"]).loc[DD]).clip(-3, 3)
        # corrected SECTOR: equal-weight Nifty 100 sector baskets vs the Nifty 50 TRI
        B50 = self.extras["tri50"].reindex(ctx.dates).ffill().pct_change()
        r = ctx.ret.where(ctx.member)
        groups = ctx.sector_of.groupby(ctx.sector_of).groups
        sec_ret = pd.DataFrame({s: r[list(n)].mean(axis=1, skipna=True) for s, n in groups.items()})
        sec_cnt = pd.DataFrame({s: r[list(n)].notna().sum(axis=1) for s, n in groups.items()})
        IR2S = C.blend(sec_ret.sub(B50, axis=0), pr["w_ir2_2m"]).loc[DD]
        ok = sec_cnt.loc[DD] >= pr["sector_min_live"]
        m = MOM.where(ELIG)
        SECMOM = pd.DataFrame({s: m[list(n)].mean(axis=1) for s, n in groups.items()})
        SECSCORE = pr["w_sec_ir2"] * C.cs_z(IR2S.where(ok)) + (1 - pr["w_sec_ir2"]) * C.cs_z(SECMOM.where(ok))
        S = C.cs_z(C.broadcast(SECSCORE, ctx).where(ELIG))
        SECTOR = S.where(S.notna() | ~ELIG.astype(bool), 0.0)
        self.X = dict(MOM=MOM, IR1=IR1, SECTOR=SECTOR)
        self.SECSCORE, self.IR2S, self.SECMOM = SECSCORE, IR2S, SECMOM

    # ------------------------------------------------------------------ styles (f5_styles, classification lagged a year)
    def _styles(self):
        ctx = self.ctx
        cls = pd.read_csv(ROOT / "ref" / "styles.csv")
        st, _ = STY.panels(ctx, cls, 1)
        sr, _ = STY.ew_index(ctx, st, STYLES)
        self.ST, self.SR = st, sr
        self.EW = ctx.ret.where(ctx.member).mean(axis=1)

    def _resid_raw(self, window):
        ctx = self.ctx; STd, SR, EW = self.ST, self.SR, self.EW
        r = ctx.ret
        x1 = pd.DataFrame(np.repeat(EW.values[:, None], len(ctx.cols), axis=1), index=ctx.dates, columns=ctx.cols)
        x2 = pd.DataFrame(0.0, index=ctx.dates, columns=ctx.cols)
        for s in STYLES:
            x2 = x2.where(STd != s, np.repeat((SR[s] - EW).fillna(0).values[:, None], len(ctx.cols), axis=1))
        x2 = x2.where(STd.notna(), 0.0)
        ok = r.notna() & x1.notna(); y, a, b = r.where(ok), x1.where(ok), x2.where(ok)
        roll = lambda df: df.rolling(window, min_periods=int(window * 0.8)).sum()
        n = roll(ok.astype(float)); Sy, Sa, Sb = roll(y), roll(a), roll(b)
        caa = roll(a * a) - Sa * Sa / n; cbb = roll(b * b) - Sb * Sb / n; cab = roll(a * b) - Sa * Sb / n
        cay = roll(a * y) - Sa * Sy / n; cby = roll(b * y) - Sb * Sy / n
        det = caa * cbb - cab * cab; hs = (cbb > 1e-10) & (det.abs() > 1e-14)
        b1 = ((cay * cbb - cby * cab) / det).where(hs, cay / caa); b2 = ((cby * caa - cay * cab) / det).where(hs, 0.0)
        a0 = (Sy - b1 * Sa - b2 * Sb) / n
        eps = (r - (a0.shift(1) + b1.shift(1) * x1 + b2.shift(1) * x2)).where(ctx.member | ctx.member.shift(-1, fill_value=False))
        return C.blend(eps, 1.0)

    # ------------------------------------------------------------------ Nifty 50 universe (point in time)
    def _universe(self):
        ctx = self.ctx
        M = self.extras["member50"].copy()
        if "TMPV" in M.columns and "TATAMOTORS" not in M.columns:
            M["TATAMOTORS"] = M["TMPV"]
        M50 = M.reindex(index=ctx.dates, columns=list(ctx.cols)).fillna(False).astype(bool)
        if "TMPV" in ctx.cols and "TATAMOTORS" in ctx.cols:
            tm = ctx.close["TMPV"].notna()
            M50["TMPV"] &= tm
            M50["TATAMOTORS"] &= ~tm & ctx.close["TATAMOTORS"].notna()
        self.M50 = M50
        p = np.clip(ctx.pos.values + 1, 0, len(ctx.dates) - 1)
        self.ELIG = pd.DataFrame(M50.values[p], index=ctx.dates, columns=M50.columns)       # member on the entry day

    # ------------------------------------------------------------------ z-scores, ICs and monthly weights
    def zscores(self, dates):
        dates = pd.DatetimeIndex(dates)
        EL = self.ELIG.loc[dates]
        at_ = lambda p: p.reindex(dates).where(EL)
        return {"MOM": C.cs_z(at_(self.X["MOM"])).clip(-3, 3), "IR1": C.cs_z(at_(self.X["IR1"])).clip(-3, 3),
                "SECTOR": C.cs_z(at_(self.X["SECTOR"])), "IR1F": (-1.0 * C.cs_z(at_(self.RAW_IR1F))).clip(-3, 3)}

    def _weights(self):
        p = self.pr["icir"]
        RM = pd.DatetimeIndex([self.tc.prev_session(e) for e in self.E])
        sig = pd.DatetimeIndex([self.tc.prev_session(r) for r in RM])
        keep = (sig >= self.X["MOM"].index[0]) & (sig <= self.END)
        self.RM, self.SIG = RM[keep], sig[keep]                     # monthly trade days and their signal days
        F = self.SIG
        Z = self.zscores(F)
        c = self.cf.values; T = len(self.cal); i0 = np.arange(T) + 1; i1 = i0 + p["fwd_days"]
        raw = np.full(c.shape, np.nan); ok_ = i1 < T; raw[ok_] = c[i1[ok_]] / c[i0[ok_]] - 1
        fwd = pd.DataFrame(raw, index=self.cal, columns=self.ctx.cols).reindex(F).where(self.ELIG.reindex(F))
        Y = fwd.sub(fwd.mean(axis=1), axis=0)
        ics = {}
        for k in FACTORS:
            s = Z[k]; ok = s.notna() & Y.notna()
            ic = s.where(ok).rank(axis=1).corrwith(Y.where(ok).rank(axis=1), axis=1)
            ics[k] = ic.where(ok.sum(axis=1) >= 15)
        self.ICS = pd.DataFrame(ics)[FACTORS]
        self.W = wf_weights(self.ICS, p["window"], p["min_obs"], p["shrink"], p["lag"])

    def weights_for(self, signal):
        """factor weights in force for a signal day: those set at the latest monthly signal on or before it"""
        w = self.W.loc[self.W.index <= pd.Timestamp(signal)]
        return w.iloc[-1], w.index[-1]

    # ------------------------------------------------------------------ book on a signal day
    def book(self, signal, W=None, n=None):
        n = n or self.pr["book"]["n"]
        d = pd.Timestamp(signal)
        if W is None:
            W, _ = self.weights_for(d)
        Z = pd.DataFrame({k: v.loc[d] for k, v in self.zscores([d]).items()})
        s = sum(float(W[k]) * Z[k] for k in FACTORS)                    # NaN if any factor is missing (as research)
        s = s.where(self.ELIG.loc[d] & Z["MOM"].notna()).dropna()
        if len(s) < 2 * n:
            raise ValueError(f"only {len(s)} stocks scored on {d.date()}")
        lab = self.ST.loc[d].reindex(s.index).where(self.ELIG.loc[d].reindex(s.index)).fillna("Unclassified")
        adj = s.groupby(lab.values).transform("mean"); s2 = s - adj
        t = Z.reindex(s.index).copy()
        t["composite"] = s; t["style"] = lab; t["style_avg"] = adj; t["final"] = s2
        t["sector"] = self.ctx.sector_of.reindex(t.index)
        L = list(s2.nlargest(n).index); S = list(s2.drop(L).nsmallest(n).index)
        t = t.sort_values("final", ascending=False); t["rank"] = np.arange(1, len(t) + 1)
        t["side"] = np.where(t.index.isin(L), "LONG", np.where(t.index.isin(S), "SHORT", ""))
        v = self.VOL.loc[d, t.index]
        t["weight"] = np.nan
        for names in (L, S):
            vv = v[names]; w = 1 / vv.fillna(vv.median()); t.loc[names, "weight"] = (w / w.sum()).values
        t["vol63"] = v
        return t, W

    # ------------------------------------------------------------------ schedule view
    def schedule_view(self, schedule="monthly"):
        R = CAL.rebalance_dates(self.tc, self.E, schedule)
        END = self.END
        done = R[R.rebalance <= END]
        held = done.iloc[-1]
        nxt = R[R.rebalance > END].iloc[0]
        upcoming = R[(R.rebalance > END)].head(6)
        out = dict(schedule=schedule, end=END, held=held.to_dict(), next=nxt.to_dict(), upcoming=upcoming)
        out["held_book"], out["held_W"] = self.book(held.signal, self.weights_for(self._w_signal(held))[0])
        if nxt.signal <= END:
            out["next_book"], out["next_W"] = self.book(nxt.signal, self.weights_for(self._w_signal(nxt))[0])
        else:
            out["next_book"] = None
        out["latest_book"], out["latest_W"] = self.book(END)              # latest close, newest monthly weights
        out["held_contract"] = CAL.contract_for(self.tc, self.E, held.rebalance)
        out["next_contract"] = CAL.contract_for(self.tc, self.E, nxt.rebalance)
        return out

    def _w_signal(self, row):
        """in-cycle rebalances keep the weights of their cycle's monthly signal"""
        return self.tc.prev_session(row.cycle_start)
