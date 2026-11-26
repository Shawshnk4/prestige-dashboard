"""
Core of the real-data model (v2): point-in-time universe, indicators, calibrated
momentum tables, IR factors on equal-weight sectors, sector strength, regime 0-100.
"""
from __future__ import annotations
import itertools, pickle
from dataclasses import dataclass, replace, field
replace = replace
import numpy as np, pandas as pd

import engine as E
from sectors import SYMBOL_TO_SECTOR

D = "./"   # research data folder (unused in production: Ctx receives its data)
STATE_NAMES = E.STATE_NAMES
PCTB_EDGES, PCTB_LABELS = E.PCTB_EDGES, E.PCTB_LABELS
RSI_EDGES_GRID = [0, 20, 25, 30, 35, 40, 45, 50, 55, 60, 65, 70, 75, 80, 101]


@dataclass
class TFParams:
    rsi_len: int = 14
    smooth_type: str = "EMA"
    smooth_len: int = 9
    bb_len: int = 20
    bb_k: float = 1.5
    slope_bars: int = 3
    cross_bars: int = 5
    reject_bars: int = 5
    reject_drop: float = 0.15

    def label(self):
        return f"RSI({self.rsi_len}) {self.smooth_type}({self.smooth_len}) BB({self.bb_len},{self.bb_k})"


USER_D = TFParams(14, "EMA", 9, 20, 1.5, 3, 5, 5)
USER_W = TFParams(14, "EMA", 9, 20, 1.5, 2, 3, 3)


# ==========================================================================
# Context
# ==========================================================================
class Ctx:
    def __init__(self, is_end="2018-12-31", fwd_days=21, lag=1, start="2010-12-01", data=None):
        # PRESTIGE production: the panels are passed in by prestige.data_store (research: read from data_v2.pkl)
        d = data if data is not None else pickle.load(open(D + "data_v2.pkl", "rb"))
        self.close = d["close"]
        self.member = d["member"]
        self.tri = d["tri"]["tri"].reindex(self.close.index).ffill()
        self.px = d["tri"]["close"].reindex(self.close.index).ffill()
        self.vix = d["vix"].reindex(self.close.index).ffill()
        self.dates = self.close.index
        self.cols = self.close.columns
        self.sector_of = pd.Series({c: SYMBOL_TO_SECTOR[c] for c in self.cols})
        self.ret = self.close.pct_change(fill_method=None)
        self.wclose = E.to_weekly(self.close)
        self.pos = pd.Series(np.arange(len(self.dates)), index=self.dates)
        self.wpos_of_day = np.searchsorted(self.wclose.index.values, self.dates.values, side="right") - 1
        self.is_end = pd.Timestamp(is_end)
        self.fwd_days, self.lag = fwd_days, lag
        me = pd.Series(self.dates, index=self.dates).groupby(self.dates.to_period("M")).last()
        self.me = pd.DatetimeIndex(me.values)
        self.me = self.me[self.me >= pd.Timestamp(start)]
        # eligibility at formation t = member on entry day (t + lag)
        p = np.clip(self.pos.values + lag, 0, len(self.dates) - 1)
        self.elig = pd.DataFrame(self.member.values[p], index=self.dates, columns=self.cols)
        self.fwd, self.fwd_raw = self._fwd(fwd_days, lag)
        self._rsi_cache = {}
        # research sample dates: weekly + month ends
        self.samples = self.wclose.index.union(self.me)
        self.samples = self.samples[(self.samples >= pd.Timestamp(start)) &
                                    (self.samples <= self.dates[-1 - fwd_days - lag])]

    def _fwd(self, h, lag):
        c = self.close.values
        T = len(self.dates)
        i0 = np.arange(T) + lag
        i1 = i0 + h
        out = np.full(c.shape, np.nan)
        ok = i1 < T
        out[ok] = c[i1[ok]] / c[i0[ok]] - 1
        raw = pd.DataFrame(out, index=self.dates, columns=self.cols).where(self.elig)
        return raw.sub(raw.mean(axis=1), axis=0), raw

    def at(self, panel, dates, weekly=False):
        if weekly:
            wp = self.wpos_of_day[self.pos.loc[dates].values]
            arr = panel.values[np.clip(wp, 0, None)].astype(float)
            arr[wp < 0] = np.nan
            return arr
        return panel.loc[dates].values.astype(float)

    def feats(self, p: TFParams, weekly: bool):
        src = self.wclose if weekly else self.close
        key = (weekly, p.rsi_len)
        if key not in self._rsi_cache:
            self._rsi_cache[key] = E.rsi_wilder(src, p.rsi_len)
        mp = E.MomentumParams(p.rsi_len, p.smooth_len, p.smooth_type, p.bb_len, p.bb_k, p.slope_bars,
                              p.cross_bars, p.reject_bars, p.reject_drop)
        return E.momentum_features(src, mp, rsi_cache={(p.rsi_len, id(src)): self._rsi_cache[key]})

    def elig_at(self, dates):
        return self.elig.loc[dates].values


# ==========================================================================
# Calibrated score tables (ridge on one-hot features)
# ==========================================================================
GROUPS = ("state", "pctb", "rsi", "flag")


def design(ctx: Ctx, f, dates, weekly, rsi_edges):
    st = ctx.at(f["state"], dates, weekly)
    pb = E.bin_index(ctx.at(f["pctb"], dates, weekly), PCTB_EDGES)
    rb = E.bin_index(ctx.at(f["rsi"], dates, weekly), [-np.inf] + list(rsi_edges[1:-1]) + [np.inf])
    ru = ctx.at(f["rej_up"], dates, weekly)
    rd = ctx.at(f["rej_dn"], dates, weekly)
    nS, nP, nR = 6, len(PCTB_EDGES) - 1, len(rsi_edges) - 1
    valid = ~(np.isnan(st) | np.isnan(pb) | np.isnan(rb) | np.isnan(ru)) & ctx.elig_at(dates)
    X = np.zeros(st.shape + (nS + nP + nR + 2,), dtype=np.float32)
    ii = np.where(valid)
    X[ii + (st[ii].astype(int),)] = 1
    X[ii + (nS + pb[ii].astype(int),)] = 1
    X[ii + (nS + nP + rb[ii].astype(int),)] = 1
    X[..., -2] = np.nan_to_num(ru)
    X[..., -1] = np.nan_to_num(rd)
    names = ([f"state:{s}" for s in STATE_NAMES] + [f"pctb:{l}" for l in PCTB_LABELS] +
             [f"rsi:{int(rsi_edges[i])}-{int(min(rsi_edges[i + 1], 100))}" for i in range(nR)] +
             ["flag:rejected_upper", "flag:bounced_lower"])
    grp = np.array([n.split(":")[0] for n in names])
    return X, valid, names, grp


def zrows(y):
    mu = np.nanmean(y, axis=1, keepdims=True)
    sd = np.nanstd(y, axis=1, keepdims=True)
    sd[sd == 0] = np.nan
    return (y - mu) / sd


def ridge(X, y, lam=0.002):
    n = len(y)
    return np.linalg.solve(X.T @ X + lam * n * np.eye(X.shape[1]), X.T @ y)


def rank_ic_rows(a, b, min_n=15):
    out = np.full(len(a), np.nan)
    for i in range(len(a)):
        m = ~(np.isnan(a[i]) | np.isnan(b[i]))
        if m.sum() >= min_n:
            out[i] = pd.Series(a[i][m]).rank().corr(pd.Series(b[i][m]).rank())
    return out


def ic_stats(ic, overlap=1.0):
    ic = np.asarray(ic, float)
    ic = ic[~np.isnan(ic)]
    if len(ic) < 6:
        return dict(ic_mean=np.nan, icir=np.nan, t=np.nan, hit=np.nan, n=len(ic))
    m, s = ic.mean(), ic.std(ddof=1)
    return dict(ic_mean=m, ic_std=s, icir=m / s * np.sqrt(12) if s > 0 else np.nan,
                t=m / s * np.sqrt(len(ic) / overlap) if s > 0 else np.nan, hit=(ic > 0).mean(), n=len(ic))


class ScoreTable:
    """score = X @ beta, beta split by group so TECH (state+pctb+flag) and RSI parts can be read separately.
    In-sample dates use the opposite-year-parity fit (honest); out-of-sample uses the full IS fit."""

    def __init__(self, beta, beta_par, names, grp, rsi_edges, is_end, cv=True):
        self.beta, self.beta_par, self.names, self.grp = beta, beta_par, names, grp
        self.rsi_edges, self.is_end, self.cv = rsi_edges, pd.Timestamp(is_end), cv

    def parts(self, ctx, f, dates, weekly):
        X, valid, _, grp = design(ctx, f, dates, weekly, self.rsi_edges)
        dates = pd.DatetimeIndex(dates)
        out = {}
        for part, gset in [("TECH", ("state", "pctb", "flag")), ("RSI", ("rsi",)), ("ALL", GROUPS)]:
            m = np.isin(grp, gset).astype(np.float32)
            s = np.empty(X.shape[:2])
            for i, d in enumerate(dates):
                b = self.beta_par[1 - d.year % 2] if (self.cv and d <= self.is_end) else self.beta
                s[i] = X[i] @ (b * m)
            s[~valid] = np.nan
            out[part] = pd.DataFrame(s, index=dates, columns=ctx.cols)
        return out

    def table(self):
        t = pd.DataFrame({"feature": self.names, "group": self.grp, "coef": self.beta})
        t["points_0_100"] = np.nan
        for g in GROUPS:
            m = t.group == g
            if g == "flag":
                t.loc[m, "points_0_100"] = t.loc[m, "coef"] / (t.coef.abs().max() or 1) * 50
            else:
                c = t.loc[m, "coef"]
                rng = c.max() - c.min()
                t.loc[m, "points_0_100"] = (c - c.min()) / rng * 100 if rng > 0 else 50
        return t


def fit_table(ctx, f, weekly, dates, rsi_edges, cv=True):
    X, valid, names, grp = design(ctx, f, dates, weekly, rsi_edges)
    y = zrows(ctx.fwd.loc[dates].values)
    ok = valid & ~np.isnan(y)
    beta = ridge(X[ok], y[ok])
    yrs = pd.DatetimeIndex(dates).year.values
    bp = {}
    for par in (0, 1):
        m = ok & (yrs % 2 == par)[:, None]
        bp[par] = ridge(X[m], y[m]) if m.sum() > 500 else beta
    return ScoreTable(beta, bp, names, grp, rsi_edges, ctx.is_end, cv)


def oof_ic(ctx, f, weekly, rsi_edges):
    """Out-of-fold monthly IC of the calibrated table (IS only): fit on even years, test odd, and v.v."""
    smp = ctx.samples[ctx.samples <= ctx.is_end]
    X, valid, _, _ = design(ctx, f, smp, weekly, rsi_edges)
    y = zrows(ctx.fwd.loc[smp].values)
    ok = valid & ~np.isnan(y)
    yrs = pd.DatetimeIndex(smp).year.values
    pred = np.full(y.shape, np.nan)
    for par in (0, 1):
        tr = ok & (yrs % 2 == par)[:, None]
        b = ridge(X[tr], y[tr])
        te = (yrs % 2 != par)
        p = X[te] @ b
        p[~valid[te]] = np.nan
        pred[te] = p
    me = ctx.me[(ctx.me <= ctx.is_end) & ctx.me.isin(smp)]
    rows = np.searchsorted(smp.values, me.values)
    return pd.Series(rank_ic_rows(pred[rows], ctx.fwd.loc[me].values), index=me)


# ==========================================================================
# Parameter grid
# ==========================================================================
GRID = dict(rsi_len=[7, 9, 11, 14, 21, 28], smooth_type=["EMA", "SMA"], smooth_len=[5, 7, 9, 12],
            bb_len=[14, 20, 26], bb_k=[1.0, 1.5, 2.0])


def grid_search(ctx, weekly, base: TFParams, log=print):
    rows, ics = [], {}
    combos = list(itertools.product(*GRID.values()))
    for i, (n, t, m, L, k) in enumerate(combos):
        p = replace(base, rsi_len=n, smooth_type=t, smooth_len=m, bb_len=L, bb_k=k)
        ic = oof_ic(ctx, ctx.feats(p, weekly), weekly, RSI_EDGES_GRID)
        ics[(n, t, m, L, k)] = ic
        rows.append(dict(rsi_len=n, smooth_type=t, smooth_len=m, bb_len=L, bb_k=k, **ic_stats(ic.values)))
        if (i + 1) % 72 == 0:
            log(f"   {'W' if weekly else 'D'} grid {i + 1}/{len(combos)}")
    g = pd.DataFrame(rows)
    keys = list(GRID)
    lvl = {k: {v: j for j, v in enumerate(GRID[k])} for k in keys}
    coord = np.array([[lvl[k][r[k]] for k in keys] for _, r in g.iterrows()])
    g["ic_plateau"] = [np.nanmean(g.loc[np.all(np.abs(coord - coord[j]) <= 1, axis=1), "ic_mean"])
                       for j in range(len(g))]
    # paired test vs the user's default
    ukey = (base.rsi_len, base.smooth_type, base.smooth_len, base.bb_len, base.bb_k)
    uic = ics[ukey]
    tvals = []
    for key in ics:
        d = (ics[key] - uic).dropna()
        tvals.append(d.mean() / d.std(ddof=1) * np.sqrt(len(d)) if len(d) > 6 and d.std() > 0 else 0.0)
    g["t_vs_user"] = tvals
    g["is_user"] = [tuple(r[k] for k in keys) == ukey for _, r in g.iterrows()]
    g = g.sort_values("ic_plateau", ascending=False).reset_index(drop=True)
    g.insert(0, "rank", np.arange(1, len(g) + 1))
    best = g.iloc[0]
    # selection rule: plateau winner, adopted only if its raw IC beats the user's default with t >= 2
    if best.t_vs_user >= 2.0:
        chosen = replace(base, rsi_len=int(best.rsi_len), smooth_type=best.smooth_type,
                         smooth_len=int(best.smooth_len), bb_len=int(best.bb_len), bb_k=float(best.bb_k))
        why = f"plateau winner beats your default (paired t = {best.t_vs_user:.2f} >= 2)"
    else:
        # fall back to the best combo that IS significantly better, else the user's default
        sig = g[g.t_vs_user >= 2.0]
        if len(sig):
            b2 = sig.iloc[0]
            chosen = replace(base, rsi_len=int(b2.rsi_len), smooth_type=b2.smooth_type,
                             smooth_len=int(b2.smooth_len), bb_len=int(b2.bb_len), bb_k=float(b2.bb_k))
            why = f"highest-plateau combo with paired t >= 2 vs default (t = {b2.t_vs_user:.2f})"
        else:
            chosen = base
            why = "no combination beats your default with t >= 2 -> default retained"
    marg = {k: g.groupby(k)["ic_mean"].mean() for k in keys}
    return chosen, why, g, marg


# ==========================================================================
# RSI zones
# ==========================================================================
def rsi_bins(ctx, rsi_panel, weekly, dates, edges, overlap=4.0):
    r = ctx.at(rsi_panel, dates, weekly)
    r[~ctx.elig_at(dates)] = np.nan
    yr = ctx.fwd.loc[dates].values
    y = zrows(yr)
    b = E.bin_index(r, [-np.inf] + list(edges[1:-1]) + [np.inf])
    tot = (~np.isnan(b) & ~np.isnan(y)).sum()
    rows = []
    for k in range(len(edges) - 1):
        m = (b == k) & ~np.isnan(y)
        n = int(m.sum())
        if n == 0:
            rows.append(dict(lo=edges[k], hi=min(edges[k + 1], 100), n=0, share=0.0))
            continue
        pdv = np.array([np.nanmean(y[i][m[i]]) for i in range(len(dates)) if m[i].any()])
        se = pdv.std(ddof=1) / np.sqrt(max(len(pdv) / overlap, 1)) if len(pdv) > 2 else np.nan
        rows.append(dict(lo=edges[k], hi=min(edges[k + 1], 100), n=n, share=n / tot, mean_z=np.nanmean(y[m]),
                         mean_xs_ret=np.nanmean(yr[m]), hit=(yr[m] > 0).mean(),
                         t_stat=pdv.mean() / se if se and se > 0 else np.nan))
    return pd.DataFrame(rows)


def jenks(means, w, k):
    n = len(means)
    x, w = np.asarray(means, float), np.asarray(w, float)
    cw, cx, cxx = np.cumsum(w), np.cumsum(w * x), np.cumsum(w * x * x)

    def sse(i, j):
        W = cw[j] - (cw[i - 1] if i else 0)
        S = cx[j] - (cx[i - 1] if i else 0)
        Q = cxx[j] - (cxx[i - 1] if i else 0)
        return Q - S * S / W if W > 0 else 0.0
    Dm = np.full((k + 1, n), np.inf)
    B = np.zeros((k + 1, n), int)
    for j in range(n):
        Dm[1, j] = sse(0, j)
    for g in range(2, k + 1):
        for j in range(g - 1, n):
            for i in range(g - 1, j + 1):
                v = Dm[g - 1, i - 1] + sse(i, j)
                if v < Dm[g, j]:
                    Dm[g, j], B[g, j] = v, i
    cuts, j = [], n - 1
    for g in range(k, 1, -1):
        cuts.append(B[g, j])
        j = B[g, j] - 1
    return sorted(cuts)


def derive_zones(ctx, rsi_panel, weekly, dates, k=5, min_share=0.005):
    bins = rsi_bins(ctx, rsi_panel, weekly, dates, list(range(0, 101, 5)) + [101])
    t = bins[bins.n > 0].reset_index(drop=True).copy()
    while len(t) > k and t.share.iloc[0] < min_share:
        t.loc[1, "lo"] = t.loc[0, "lo"]; t.loc[1, "n"] += t.loc[0, "n"]; t = t.iloc[1:].reset_index(drop=True)
    while len(t) > k and t.share.iloc[-1] < min_share:
        t.loc[len(t) - 2, "hi"] = t.loc[len(t) - 1, "hi"]; t.loc[len(t) - 2, "n"] += t.loc[len(t) - 1, "n"]
        t = t.iloc[:-1].reset_index(drop=True)
    cuts = jenks(t.mean_z.values, t.n.values, k)
    edges = [0] + [float(t.lo.iloc[c]) for c in cuts] + [101]
    z = rsi_bins(ctx, rsi_panel, weekly, dates, edges)
    labels = []
    for i, r in z.iterrows():
        if i == len(z) - 1:      # top zone: negative next-month excess = overbought, positive = strong momentum
            labels.append("OVERBOUGHT" if r.mean_z < 0 else "STRONG_MOMENTUM")
        elif i == 0:             # bottom zone: positive = oversold bounce, negative = capitulation
            labels.append("OVERSOLD_BOUNCE" if r.mean_z > 0 else "CAPITULATION")
        elif abs(r.t_stat) < 1.5:
            labels.append("NEUTRAL")
        else:
            labels.append("BULL" if r.mean_z > 0 else "BEAR")
    z["label"] = labels
    return z, edges, bins


# ==========================================================================
# IR on point-in-time equal-weight sectors
# ==========================================================================
def sector_returns(ctx, member_panel=None):
    m = ctx.member if member_panel is None else member_panel
    r = ctx.ret.where(m)
    out, cnt = {}, {}
    for s, names in ctx.sector_of.groupby(ctx.sector_of).groups.items():
        out[s] = r[list(names)].mean(axis=1, skipna=True)
        cnt[s] = r[list(names)].notna().sum(axis=1)
    return pd.DataFrame(out), pd.DataFrame(cnt)


def peer_loo(ctx):
    """LOO EW sector return for every stock (peers = members only); sectors < 3 live -> LOO EW universe."""
    r = ctx.ret.where(ctx.member)
    uni_s, uni_n = r.sum(axis=1), r.notna().sum(axis=1)
    peer = pd.DataFrame(np.nan, index=ctx.dates, columns=ctx.cols)
    for s, names in ctx.sector_of.groupby(ctx.sector_of).groups.items():
        names = list(names)
        ss, sn = r[names].sum(axis=1), r[names].notna().sum(axis=1)
        for c in names:
            own = r[c].fillna(0)
            isin = r[c].notna().astype(int)
            sec = (ss - own) / (sn - isin).replace(0, np.nan)
            uni = (uni_s - own) / (uni_n - isin).replace(0, np.nan)
            peer[c] = np.where((sn - isin) >= 2, sec, uni)
    return peer


def ir(active, lb):
    m = active.rolling(lb, min_periods=int(lb * 0.8)).mean()
    s = active.rolling(lb, min_periods=int(lb * 0.8)).std()
    return m / s.where(s > 1e-9) * np.sqrt(252)


def blend(active, w2, lb2=42, lb3=63):
    return w2 * ir(active, lb2) + (1 - w2) * ir(active, lb3)


def cs_z(df):
    return df.sub(df.mean(axis=1), axis=0).div(df.std(axis=1).replace(0, np.nan), axis=0)


def broadcast(sec_panel, ctx):
    return pd.DataFrame({c: sec_panel[ctx.sector_of[c]] for c in ctx.cols}, index=sec_panel.index)


# ==========================================================================
# Regime 0-100
# ==========================================================================
def pct_rank(s, win=756, minp=252):
    return s.rolling(win, min_periods=minp).apply(lambda a: (a[:-1] < a[-1]).mean() + 0.5 * (a[:-1] == a[-1]).mean(),
                                                 raw=True)


def regime_components(ctx):
    px = ctx.px
    e21, e50 = px.ewm(span=21, adjust=False).mean(), px.ewm(span=50, adjust=False).mean()
    trend_raw = (np.sign(px - e21) + np.sign(px - e50) + np.sign(e21 - e50)) / 3
    trend = trend_raw.rolling(5, min_periods=1).mean()
    # breadth on point-in-time members, weekly
    wm = E.to_weekly(ctx.member.astype(float)).astype(bool)
    wc = ctx.wclose.where(wm)
    wr = ctx.wclose.pct_change(fill_method=None).where(wm)
    adv, dec = (wr > 0).sum(axis=1), (wr < 0).sum(axis=1)
    ad = (adv - dec) / (adv + dec).replace(0, np.nan)
    ad4 = ad.ewm(span=4, adjust=False).mean()
    ma20 = ctx.wclose.rolling(20, min_periods=15).mean()
    above = (ctx.wclose > ma20).where(wm & ma20.notna())
    pct_above = above.mean(axis=1)
    breadth_w = 0.5 * (2 * ad4).clip(-1, 1) + 0.5 * (2 * pct_above - 1)
    breadth = E.weekly_asof(breadth_w.to_frame("b"), ctx.dates)["b"]
    # volatility: realised (20d) and India VIX, each as percentile of trailing 3y; low vol = +1
    rv = px.pct_change().rolling(20).std() * np.sqrt(252)
    rv_p, vix_p = pct_rank(rv), pct_rank(ctx.vix)
    comp = pd.DataFrame({"px": px, "ema21": e21, "ema50": e50, "trend": trend,
                         "ad_ema4": E.weekly_asof(ad4.to_frame("a"), ctx.dates)["a"],
                         "pct_above_20w": E.weekly_asof(pct_above.to_frame("p"), ctx.dates)["p"],
                         "breadth": breadth, "rv20": rv, "vix": ctx.vix, "rv_pct": rv_p, "vix_pct": vix_p})
    return comp


def vol_component(comp, w_vix):
    return 1 - 2 * ((1 - w_vix) * comp["rv_pct"] + w_vix * comp["vix_pct"])


def regime_score(comp, wt, wb, wv, w_vix):
    v = vol_component(comp, w_vix)
    s = wt * comp["trend"] + wb * comp["breadth"] + wv * v
    return (50 + 50 * s).clip(0, 100)


ZONES = [("STRONG RISK-OFF", 0, 20), ("RISK-OFF", 20, 40), ("NEUTRAL", 40, 60), ("RISK-ON", 60, 80),
         ("STRONG RISK-ON", 80, 100.01)]


def zone_of(x):
    if pd.isna(x):
        return None
    for n, lo, hi in ZONES:
        if lo <= x < hi:
            return n
    return ZONES[-1][0]
