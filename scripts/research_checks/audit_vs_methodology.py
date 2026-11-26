"""Independent audit: recompute the PRESTIGE book from the methodology document's formulas with fresh code (no research
functions), and compare with the production engine. Data: base history + FYERS closes to 25-Nov-2026 (tests/fixtures).
Usage: python scripts/research_checks/audit_vs_methodology.py [signal_date ...]"""
import json, os, sys
import numpy as np, pandas as pd
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
from prestige import data_store as DS, model as MD, calendar as CAL

F = pd.read_csv(os.path.join(ROOT, "tests/fixtures/fyers_closes_2026-10-23_to_11-25.csv"), parse_dates=["date"])
data, extras, _ = DS.assemble(F)
M = MD.Model(data, extras)
close, member = data["close"], data["member"].astype(bool)
dates, cols = close.index, close.columns
pos = pd.Series(np.arange(len(dates)), index=dates)
ret = close.pct_change(fill_method=None)
sector = pd.read_csv(os.path.join(ROOT, "data/ref/sectors.csv")).set_index("symbol")["sector"].reindex(cols)
P = json.load(open(os.path.join(ROOT, "data/model/params.json")))
T = json.load(open(os.path.join(ROOT, "data/model/score_tables.json")))
SIGS = [pd.Timestamp(x) for x in (sys.argv[1:] or ["2026-11-19", "2026-11-25"])]
nxt = lambda d: dates[min(pos[d] + 1, len(dates) - 1)]
report = []


def chk(name, a, b, tol=1e-9):
    a, b = pd.Series(a, dtype=float), pd.Series(b, dtype=float).reindex(a.index)
    d = (a - b).abs().max()
    ok = bool(d <= tol) and a.notna().sum() == b.notna().sum()
    report.append((name, ok, float(d)))
    print(f"  {'OK ' if ok else 'XX '} {name:58s} max |diff| {d:.2e}")
    return ok


def z(s):
    s = s.astype(float)
    return (s - s.mean()) / s.std()          # sample SD, as pandas


def ir(x, n):
    m = x.rolling(n, min_periods=int(n * 0.8)).mean(); sd = x.rolling(n, min_periods=int(n * 0.8)).std()
    return m / sd.where(sd > 1e-9) * np.sqrt(252)


# ---------------------------------------------------------------- MOM from the doc's recipe (section 3.1)
def rsi_wilder(x, n=14):
    out = np.full(len(x), np.nan); d = np.diff(x, prepend=np.nan)
    ag = al = None; buf = []
    for t in range(len(x)):
        if np.isnan(d[t]):
            continue
        g, l = max(d[t], 0), max(-d[t], 0)
        if ag is None:
            buf.append((g, l))
            if len(buf) == n:
                ag = np.mean([b[0] for b in buf]); al = np.mean([b[1] for b in buf])
                out[t] = 100 if al == 0 else 100 - 100 / (1 + ag / al)
            continue
        ag = (ag * (n - 1) + g) / n; al = (al * (n - 1) + l) / n
        out[t] = 100 if al == 0 else 100 - 100 / (1 + ag / al)
    return out


def ema(x, n):
    a = 2 / (n + 1); out = np.full(len(x), np.nan); e = None; k = 0
    for t in range(len(x)):
        if np.isnan(x[t]):
            if e is not None: out[t] = np.nan
            continue
        e = x[t] if e is None else a * x[t] + (1 - a) * e; k += 1
        out[t] = e if k >= n else np.nan
    return out


def setup_points(series, tf, asof_idx):
    """points for one stock on one bar index: band state + %B bucket + RSI zone + two flags"""
    p = P[tf]; tab = T[tf]; beta = dict(zip(tab["names"], tab["beta"]))
    r = rsi_wilder(series); S = ema(r, p["smooth_len"])
    Ss = pd.Series(S); mid = Ss.rolling(p["bb_len"]).mean(); sd = Ss.rolling(p["bb_len"]).std(ddof=0)
    up, lo = mid + p["bb_k"] * sd, mid - p["bb_k"] * sd
    pb = (Ss - lo) / (up - lo)
    slope = Ss - Ss.shift(p["slope_bars"])
    above = Ss > mid
    i = asof_idx
    if np.isnan(S[i]) or np.isnan(mid[i]) or np.isnan(slope[i]) or np.isnan(r[i]):
        return np.nan
    w = range(max(0, i - p["cross_bars"] + 1), i + 1)
    crossed_up = any(above[j] and (j == 0 or not above[j - 1]) for j in w)
    crossed_dn = any((not above[j]) and j > 0 and above[j - 1] for j in w)
    sl = slope[i]
    if above[i]:
        st = "UPCROSS" if sl > 0 and crossed_up else ("ABOVE_RISING" if sl > 0 else "ABOVE_FALLING")
    else:
        st = "DOWNCROSS" if sl < 0 and crossed_dn else ("BELOW_FALLING" if sl < 0 else "BELOW_RISING")
    b = pb[i]
    pbl = "<0" if b < 0 else "0-0.2" if b < .2 else "0.2-0.4" if b < .4 else "0.4-0.6" if b < .6 else "0.6-0.8" if b < .8 else "0.8-1" if b < 1 else ">1"
    e = P["edges" + tf]
    zi = int(np.searchsorted(e[1:-1], r[i], side="right"))
    zone = f"rsi:{int(e[zi])}-{int(min(e[zi + 1], 100))}"
    rb = p["reject_bars"]; win = pb[max(0, i - rb + 1): i + 1]
    rej = float(win.max() >= 0.95 and sl < 0 and b <= win.max() - p["reject_drop"])
    bnc = float(win.min() <= 0.05 and sl > 0 and b >= win.min() + p["reject_drop"])
    names = [n for n in tab["names"] if n.startswith("state:")]
    stn = {"UPCROSS": 0, "ABOVE_RISING": 1, "ABOVE_FALLING": 2, "DOWNCROSS": 3, "BELOW_FALLING": 4, "BELOW_RISING": 5}[st]
    return beta[names[stn]] + beta[f"pctb:{pbl}"] + beta[zone] + rej * beta["flag:rejected_upper"] + bnc * beta["flag:bounced_lower"]


def mom_on(d, completed_weeks_only=True, tc=None):
    el = member.loc[nxt(d)]
    names = list(el[el].index)
    dpts, wpts = {}, {}
    wk = close.loc[:d].index.to_period("W-FRI")
    for s in names:
        x = close[s].loc[:d].values
        dpts[s] = setup_points(x, "D", len(x) - 1)
        wc = close[s].loc[:d].groupby(wk).last()
        last_week_done = True
        if completed_weeks_only:
            # the week of d is complete only if d is that week's last trading session
            nxt_sess = tc.next_session(d) if tc is not None else nxt(d)
            last_week_done = nxt_sess.to_period("W-FRI") != d.to_period("W-FRI")
        wv = wc.values if last_week_done else wc.values[:-1]
        wpts[s] = setup_points(wv, "W", len(wv) - 1)
    Dz, Wz = z(pd.Series(dpts)), z(pd.Series(wpts))
    return z(0.6 * Wz + 0.4 * Dz).clip(-3, 3), pd.Series(dpts), pd.Series(wpts)


# ---------------------------------------------------------------- the other factors (sections 3.2-3.4)
def peer_loo():
    r = ret.where(member)
    out = pd.DataFrame(np.nan, index=dates, columns=cols)
    us, un = r.sum(axis=1), r.notna().sum(axis=1)
    for sec, g in sector.groupby(sector):
        g = list(g.index); ss, sn = r[g].sum(axis=1), r[g].notna().sum(axis=1)
        for c in g:
            own, isin = r[c].fillna(0), r[c].notna().astype(int)
            out[c] = np.where(sn - isin >= 2, (ss - own) / (sn - isin).replace(0, np.nan), (us - own) / (un - isin).replace(0, np.nan))
    return out


def run(d):
    print(f"\n=== signal {d.date()} ===")
    tr = nxt(d)
    el100 = member.loc[tr]; n100 = list(el100[el100].index)
    m50 = M.ELIG.loc[d]; n50 = list(m50[m50].index)
    # MOM
    mom, dp, wp = mom_on(d, True, M.tc)
    chk("MOM (doc recipe, completed weeks) vs engine, Nifty 100", mom, M.X["MOM"].loc[d, mom.index], 1e-8)
    mom_partial, _, _ = mom_on(d, False)
    if not np.allclose(mom.values, mom_partial.reindex(mom.index).values):
        print("     note: on this date the current week is incomplete; using it would change MOM by up to",
              f"{(mom - mom_partial.reindex(mom.index)).abs().max():.2f}")
    # IR1
    peer = peer_loo()
    act = (ret - peer).where(member | member.shift(-1, fill_value=False))
    raw = ir(act, 42).loc[d, n100]
    IR1_100 = (-1 * z(raw).clip(-3, 3))
    chk("IR1 vs engine (Nifty 100 stage)", IR1_100, M.X["IR1"].loc[d, n100])
    # SECTOR
    b50 = extras["tri50"].reindex(dates).ffill().pct_change()
    r100 = ret.where(member)
    secret = pd.DataFrame({s: r100[list(g.index)].mean(axis=1) for s, g in sector.groupby(sector)})
    seccnt = pd.DataFrame({s: r100[list(g.index)].notna().sum(axis=1) for s, g in sector.groupby(sector)})
    actS = secret.sub(b50, axis=0)
    secIR = (0.5 * ir(actS, 42) + 0.5 * ir(actS, 63)).loc[d]
    live = seccnt.loc[d] >= 2
    secMOM = pd.Series({s: M.X["MOM"].loc[d, [c for c in g.index if el100.get(c, False)]].mean() for s, g in sector.groupby(sector)})
    sscore = 0.5 * z(secIR[live]) + 0.5 * z(secMOM[live])
    st = pd.Series({c: sscore.get(sector[c], np.nan) for c in n100})
    SEC = z(st).fillna(0.0)
    chk("SECTOR vs engine (Nifty 100 stage)", SEC, M.X["SECTOR"].loc[d, n100])
    # IR1F: rolling regression on the equal-weight Nifty 100 and the style spread, fitted to the previous day
    ST = M.ST; SR = M.SR; EW = ret.where(member).mean(axis=1)
    res = {}
    for c in n50:
        y = ret[c]
        x2 = pd.Series(0.0, index=dates)
        for sty in SR.columns:
            msk = ST[c] == sty
            x2[msk] = (SR[sty] - EW).fillna(0)[msk]
        eps = pd.Series(np.nan, index=dates)
        t1 = pos[d]
        for t in range(t1 - 41 - 5, t1 + 1):
            ok = y.notna() & EW.notna()
            idx = np.arange(t - 252, t)                    # window ending the previous day
            wnd = [i for i in idx if ok.iloc[i]]
            if len(wnd) < 202 or not ok.iloc[t]:
                continue
            X = np.column_stack([np.ones(len(wnd)), EW.iloc[wnd].values, x2.iloc[wnd].values])
            if np.var(x2.iloc[wnd].values) < 1e-12:
                X = X[:, :2]
            b = np.linalg.lstsq(X, y.iloc[wnd].values, rcond=None)[0]
            pred = b[0] + b[1] * EW.iloc[t] + (b[2] * x2.iloc[t] if len(b) == 3 else 0)
            eps.iloc[t] = y.iloc[t] - pred
        mm = member[c] | member[c].shift(-1, fill_value=False)
        res[c] = ir(eps.where(mm), 42).loc[d]
    raw1f = pd.Series(res)
    chk("IR1F raw vs engine", raw1f, M.RAW_IR1F.loc[d, raw1f.index], 1e-6)
    # Nifty 50 standardisation (section 4.1), score, style adjustment, selection, weights
    Zs = M.zscores([d])
    Z = pd.DataFrame({"MOM": z(M.X["MOM"].loc[d, n50]).clip(-3, 3), "IR1": z(M.X["IR1"].loc[d, n50]).clip(-3, 3),
                      "SECTOR": z(M.X["SECTOR"].loc[d, n50]), "IR1F": (-1 * z(raw1f.reindex(n50))).clip(-3, 3)})
    for k in Z:
        chk(f"z-score across Nifty 50: {k}", Z[k], Zs[k].loc[d, n50], 1e-6)
    W, wd = M.weights_for(d)
    sc = sum(W[k] * Z[k] for k in Z).dropna()
    lab = M.ST.loc[d].reindex(sc.index).fillna("Unclassified")
    fin = sc - sc.groupby(lab).transform("mean")
    t, _ = M.book(d)
    chk("final score vs engine", fin, t["final"], 1e-6)
    L = set(fin.nlargest(15).index); S = set(fin.drop(list(L)).nsmallest(15).index)
    okb = L == set(t.index[t.side == "LONG"]) and S == set(t.index[t.side == "SHORT"])
    report.append(("book (15 long / 15 short) identical", okb, 0.0)); print(f"  {'OK ' if okb else 'XX '} book (15 long / 15 short) identical")
    vol = ret.loc[:d].tail(63).std() * np.sqrt(252)
    cnt = ret.loc[:d].tail(63).notna().sum()
    vol = vol.where(cnt >= 50)
    for side, names in (("LONG", L), ("SHORT", S)):
        v = vol[list(names)]; v = v.fillna(v.median()); w = (1 / v) / (1 / v).sum()
        chk(f"inverse 63-day vol weights, {side.lower()} leg", w, t.loc[list(names), "weight"], 1e-9)
    print(f"  ranked stocks: {len(fin)} | weights set on {wd.date()}: {W.round(4).to_dict()}")
    return t


# ---------------------------------------------------------------- weights (section 4.2): ICs, ICIR, 50% shrink
def weights_check():
    print("\n=== factor weights ===")
    F_ = M.SIG
    Zm = M.zscores(F_)
    cf = close.ffill(limit=10)
    ics = {}
    for k in Zm:
        out = {}
        for d in F_:
            i0 = pos[d] + 1; i1 = i0 + 21
            if i1 >= len(dates): out[d] = np.nan; continue
            y = (cf.iloc[i1] / cf.iloc[i0] - 1).where(M.ELIG.loc[d])
            s = Zm[k].loc[d]; ok = s.notna() & y.notna()
            out[d] = s[ok].rank().corr(y[ok].rank()) if ok.sum() >= 15 else np.nan
        ics[k] = pd.Series(out)
    ics = pd.DataFrame(ics)
    W = {}
    for i, d in enumerate(F_):
        h = ics.iloc[max(0, i - 2 - 36 + 1): max(0, i - 1)].dropna(how="all")
        if len(h) < 18:
            W[d] = pd.Series(0.25, index=ics.columns); continue
        raw = (h.mean() / h.std()).clip(lower=0).fillna(0)
        W[d] = 0.5 * raw / raw.sum() + 0.125 if raw.sum() > 0 else pd.Series(0.25, index=ics.columns)
    W = pd.DataFrame(W).T
    ok = np.allclose(W.values, M.W[W.columns].values, atol=1e-12)
    report.append(("walk-forward ICIR weights, every month", ok, float(np.abs(W.values - M.W[W.columns].values).max())))
    print(f"  {'OK ' if ok else 'XX '} walk-forward ICIR weights, every month (max |diff| {np.abs(W.values - M.W[W.columns].values).max():.1e})")
    print("  first month with track-record weights:", W.index[(W.round(6) != 0.25).any(axis=1)][0].date())
    print("  min / max weight ever:", round(float(W.values.min()), 4), round(float(W.values.max()), 4))
    print("  average weight 2011-2026:", W.mean().round(3).to_dict(), "| 2026-09:", W.loc["2026-09-25"].round(3).to_dict())
    return W


if __name__ == "__main__":
    print("dates: expiries", [x.date() for x in M.E[(M.E > "2026-10-01") & (M.E < "2027-01-31")]],
          "| Nov trade (E-1)", M.tc.prev_session(pd.Timestamp("2026-11-23")).date(), "| signal (E-2)", M.tc.shift(pd.Timestamp("2026-11-23"), -2).date())
    W = weights_check()
    for d in SIGS:
        run(d)
    bad = [r for r in report if not r[1]]
    print(f"\n{len(report) - len(bad)}/{len(report)} checks agree" + ("" if not bad else f"; differences: {[b[0] for b in bad]}"))
