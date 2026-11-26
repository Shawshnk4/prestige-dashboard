"""
Core engine: data prep, indicators, momentum state machine, IR factors,
equal-weight sector indices, sector strength, market regime.

Conventions
-----------
* Panels are pandas DataFrames: index = trading dates, columns = symbols.
* Weekly bars are built from daily closes (last trading day of each W-FRI week)
  and mapped back to daily "as-of" so a mid-week date only sees completed weeks
  -> no look-ahead.
* RSI = Wilder (SMA seed, then 1/n recursion), BB std = population (ddof=0)
  to match TradingView / most charting tools.
"""
from __future__ import annotations
import numpy as np
import pandas as pd

from config import SYMBOL_TO_SECTOR, MIN_SECTOR_SIZE, INDEX_SYMBOL, MomentumParams

STATE_NAMES = ["UPCROSS", "ABOVE_RISING", "ABOVE_FALLING",
               "DNCROSS", "BELOW_FALLING", "BELOW_RISING"]
PCTB_EDGES = [-np.inf, 0.0, 0.2, 0.4, 0.6, 0.8, 1.0, np.inf]
PCTB_LABELS = ["<0", "0-0.2", "0.2-0.4", "0.4-0.6", "0.6-0.8", "0.8-1", ">1"]
RSI_EDGES_FINE = list(range(0, 101, 5))


# ==========================================================================
# DATA
# ==========================================================================
def load_prices(path: str):
    """Accepts long (date,symbol,close[,o,h,l,v]) or wide (date + one column per symbol)."""
    raw = pd.read_csv(path)
    raw.columns = [c.strip().lower().replace("﻿", "") for c in raw.columns]
    dcol = next(c for c in raw.columns if c in ("date", "datetime", "timestamp", "time"))
    raw[dcol] = pd.to_datetime(raw[dcol], dayfirst=False, errors="coerce")
    raw = raw.dropna(subset=[dcol])
    if "symbol" in raw.columns:
        ccol = "adj close" if "adj close" in raw.columns else "close"
        close = raw.pivot_table(index=dcol, columns="symbol", values=ccol, aggfunc="last")
    else:
        close = raw.set_index(dcol).apply(pd.to_numeric, errors="coerce")
        close.columns = [c.upper() for c in close.columns]
    close.index = pd.DatetimeIndex(close.index).normalize()
    close = close.sort_index()
    close.columns = [str(c).upper().strip() for c in close.columns]
    return close


def clean_prices(close: pd.DataFrame, jump=0.45, max_ffill=5):
    """Forward-fill short gaps inside a stock's life; back-adjust |1d move|>jump
    (unadjusted split/bonus/demerger artefacts). Returns cleaned panel + log."""
    close = close.copy()
    log = []
    for c in close.columns:
        s = close[c]
        first, last = s.first_valid_index(), s.last_valid_index()
        if first is None:
            continue
        seg = s.loc[first:last].ffill(limit=max_ffill)
        r = seg.pct_change(fill_method=None)
        bad = r[(r.abs() > jump)]
        for d, v in bad.items():
            factor = 1.0 + v
            seg.loc[:d - pd.Timedelta(days=1)] *= factor     # back-adjust history
            log.append({"symbol": c, "date": d.date(), "move": round(v, 4), "action": "back-adjusted"})
        close.loc[first:last, c] = seg
    return close, pd.DataFrame(log)


def split_index(close: pd.DataFrame):
    idx_col = INDEX_SYMBOL if INDEX_SYMBOL in close.columns else None
    index = close[idx_col].dropna() if idx_col else None
    stocks = close.drop(columns=[c for c in close.columns if c in (INDEX_SYMBOL, "NIFTY50")])
    return stocks, index


def to_weekly(close: pd.DataFrame):
    """Weekly close = last valid close of each W-FRI week, indexed by the
    actual last trading date of that week."""
    wk = close.index.to_period("W-FRI")
    last_date = pd.Series(close.index, index=close.index).groupby(wk).last()
    wclose = close.groupby(wk).last()
    wclose.index = pd.DatetimeIndex(last_date.values)
    return wclose


def weekly_asof(wpanel: pd.DataFrame, daily_index: pd.DatetimeIndex):
    """Map weekly values to daily dates using only completed weeks."""
    return wpanel.reindex(daily_index.union(wpanel.index)).ffill().reindex(daily_index)


def month_end_dates(index: pd.DatetimeIndex):
    s = pd.Series(index, index=index)
    return pd.DatetimeIndex(s.groupby(index.to_period("M")).last().values)


# ==========================================================================
# INDICATORS
# ==========================================================================
def rsi_wilder(close: pd.DataFrame, n: int, gap_reset: int = 10) -> pd.DataFrame:
    """Wilder RSI. State re-seeds (SMA of the next n changes) whenever a stock's data has a
    hole longer than `gap_reset` bars, so nothing carries across years-long absences."""
    x = close.values.astype(float)
    T, N = x.shape
    out = np.full((T, N), np.nan)
    d = np.diff(x, axis=0, prepend=np.nan)
    g = np.where(d > 0, d, 0.0)
    l = np.where(d < 0, -d, 0.0)
    valid = ~np.isnan(d)
    for j in range(N):
        vj = np.flatnonzero(valid[:, j])
        if len(vj) <= n:
            continue
        k = 0
        while k < len(vj):
            # find end of this contiguous segment
            e_ = k
            while e_ + 1 < len(vj) and vj[e_ + 1] - vj[e_] <= gap_reset:
                e_ += 1
            seg = vj[k:e_ + 1]
            if len(seg) > n:
                ag = g[seg[:n], j].mean()
                al = l[seg[:n], j].mean()
                out[seg[n - 1], j] = 100.0 if al == 0 else 100 - 100 / (1 + ag / al)
                for t in seg[n:]:
                    ag = (ag * (n - 1) + g[t, j]) / n
                    al = (al * (n - 1) + l[t, j]) / n
                    out[t, j] = 100.0 if al == 0 else 100 - 100 / (1 + ag / al)
            k = e_ + 1
    return pd.DataFrame(out, index=close.index, columns=close.columns)


def smooth(df: pd.DataFrame, n: int, kind: str = "EMA"):
    if kind.upper() == "EMA":
        return df.ewm(span=n, adjust=False, min_periods=n).mean()
    return df.rolling(n, min_periods=n).mean()


def bollinger(s: pd.DataFrame, n: int, k: float):
    mid = s.rolling(n, min_periods=n).mean()
    sd = s.rolling(n, min_periods=n).std(ddof=0)
    return mid, mid + k * sd, mid - k * sd


# ==========================================================================
# MOMENTUM FEATURES  (EMA(RSI) inside its own Bollinger Band)
# ==========================================================================
def momentum_features(close: pd.DataFrame, p: MomentumParams, rsi_cache: dict | None = None,
                      touch_hi=0.95, touch_lo=0.05):
    """Returns dict of panels:
       rsi, S (smoothed RSI), mid, up, lo, pctb, slope, state (0..5),
       rej_up (rejected from upper band -> long penalty), rej_dn (bounced off lower -> short penalty)
    """
    key = (p.rsi_len, id(close))
    if rsi_cache is not None and key in rsi_cache:
        rsi = rsi_cache[key]
    else:
        rsi = rsi_wilder(close, p.rsi_len)
        if rsi_cache is not None:
            rsi_cache[key] = rsi
    S = smooth(rsi, p.smooth_len, p.smooth_type)
    mid, up, lo = bollinger(S, p.bb_len, p.bb_k)
    width = (up - lo).where((up - lo) > 1e-6)
    pctb = (S - lo) / width
    slope = S - S.shift(p.slope_bars)

    above = (S > mid)
    valid = S.notna() & mid.notna()
    up_x = (above & ~above.shift(1, fill_value=False)).astype(float)
    dn_x = (~above & above.shift(1, fill_value=False)).astype(float)
    fresh_up = up_x.rolling(p.cross_bars, min_periods=1).max() > 0
    fresh_dn = dn_x.rolling(p.cross_bars, min_periods=1).max() > 0

    st = np.full(S.shape, np.nan)
    a, sl = above.values, slope.values
    fu, fd = fresh_up.values, fresh_dn.values
    st[a & (sl > 0) & fu] = 0
    st[a & (sl > 0) & ~fu] = 1
    st[a & (sl <= 0)] = 2
    st[~a & (sl < 0) & fd] = 3
    st[~a & (sl < 0) & ~fd] = 4
    st[~a & (sl >= 0)] = 5
    st[~valid.values | np.isnan(sl)] = np.nan
    state = pd.DataFrame(st, index=S.index, columns=S.columns)

    pk = pctb.rolling(p.reject_bars, min_periods=1).max()
    tr = pctb.rolling(p.reject_bars, min_periods=1).min()
    rej_up = ((pk >= touch_hi) & (slope < 0) & (pctb <= pk - p.reject_drop)).astype(float).where(valid)
    rej_dn = ((tr <= touch_lo) & (slope > 0) & (pctb >= tr + p.reject_drop)).astype(float).where(valid)

    return dict(rsi=rsi, S=S, mid=mid, up=up, lo=lo, pctb=pctb, slope=slope,
                state=state, rej_up=rej_up, rej_dn=rej_dn)


def bin_index(values: np.ndarray, edges) -> np.ndarray:
    """Return bin id (0..len(edges)-2) or -1 for NaN."""
    b = np.digitize(values, edges[1:-1], right=False).astype(float)
    b[np.isnan(values)] = np.nan
    return b


# ==========================================================================
# RETURNS, SECTORS, IR
# ==========================================================================
def daily_returns(close):
    return close.pct_change(fill_method=None)


def sector_members(columns):
    secs = {}
    for c in columns:
        secs.setdefault(SYMBOL_TO_SECTOR.get(c, "OTHER"), []).append(c)
    return secs


def sector_ew_returns(ret: pd.DataFrame):
    secs = sector_members(ret.columns)
    return pd.DataFrame({s: ret[m].mean(axis=1, skipna=True) for s, m in secs.items()})


def peer_returns_loo(ret: pd.DataFrame):
    """Leave-one-out equal-weight sector return for each stock.
    Small sectors (< MIN_SECTOR_SIZE live members incl. self) fall back to LOO EW universe."""
    secs = sector_members(ret.columns)
    uni_sum, uni_cnt = ret.sum(axis=1, skipna=True), ret.notna().sum(axis=1)
    peer = pd.DataFrame(index=ret.index, columns=ret.columns, dtype=float)
    for s, m in secs.items():
        ssum, scnt = ret[m].sum(axis=1, skipna=True), ret[m].notna().sum(axis=1)
        for c in m:
            own = ret[c].fillna(0)
            sec_loo = (ssum - own) / (scnt - ret[c].notna()).replace(0, np.nan)
            uni_loo = (uni_sum - own) / (uni_cnt - ret[c].notna()).replace(0, np.nan)
            use_sec = scnt >= MIN_SECTOR_SIZE
            peer[c] = np.where(use_sec, sec_loo, uni_loo)
    return peer


def info_ratio(active: pd.DataFrame, lb: int):
    m = active.rolling(lb, min_periods=int(lb * 0.8)).mean()
    s = active.rolling(lb, min_periods=int(lb * 0.8)).std()
    return (m / s.where(s > 1e-9)) * np.sqrt(252)


def ir_blend(active, lb2, lb3, w2):
    return w2 * info_ratio(active, lb2) + (1 - w2) * info_ratio(active, lb3)


def benchmark_returns(ret, index_close, kind="EW"):
    if kind == "INDEX" and index_close is not None:
        return index_close.reindex(ret.index).pct_change(fill_method=None)
    return ret.mean(axis=1, skipna=True)


def ir1_panel(ret, lb2, lb3, w2):
    active = ret - peer_returns_loo(ret)
    return ir_blend(active, lb2, lb3, w2)


def ir2_panel(ret, bench, lb2, lb3, w2):
    sec = sector_ew_returns(ret)
    active = sec.sub(bench, axis=0)
    return ir_blend(active, lb2, lb3, w2)


def sector_breadth(flag: pd.DataFrame):
    """% of live sector members where flag is True (flag NaN = not live)."""
    secs = sector_members(flag.columns)
    return pd.DataFrame({s: flag[m].mean(axis=1, skipna=True) for s, m in secs.items()})


def broadcast_sector(sec_panel: pd.DataFrame, columns):
    return pd.DataFrame({c: sec_panel[SYMBOL_TO_SECTOR.get(c, "OTHER")] for c in columns
                         if SYMBOL_TO_SECTOR.get(c, "OTHER") in sec_panel.columns})


def cs_z(df: pd.DataFrame):
    mu = df.mean(axis=1)
    sd = df.std(axis=1).replace(0, np.nan)
    return df.sub(mu, axis=0).div(sd, axis=0)


def z_control(z: pd.DataFrame, cap=2.0, mode="cap"):
    if mode == "fold":   # beyond the cap the score mean-reverts (extension penalty)
        a = z.abs()
        return np.sign(z) * np.where(a <= cap, a, np.maximum(2 * cap - a, 0))
    return z.clip(-cap, cap)


# ==========================================================================
# REGIME  (trend + weekly A/D breadth + volatility)
# ==========================================================================
REGIME_ZONES = [("STRONG_BULL", 0.50), ("BULL", 0.15), ("NEUTRAL", -0.15),
                ("BEAR", -0.50), ("STRONG_BEAR", -np.inf)]


def regime_components(index_close: pd.Series, stocks_close: pd.DataFrame):
    idx = index_close.dropna()
    e21 = idx.ewm(span=21, adjust=False).mean()
    e50 = idx.ewm(span=50, adjust=False).mean()
    trend = (np.sign(idx - e21) + np.sign(idx - e50) + np.sign(e21 - e50)) / 3.0

    # weekly A/D on the Nifty100 universe
    w = to_weekly(stocks_close)
    wr = w.pct_change(fill_method=None)
    adv, dec = (wr > 0).sum(axis=1), (wr < 0).sum(axis=1)
    ad = (adv - dec) / (adv + dec).replace(0, np.nan)
    ad_s = ad.ewm(span=4, adjust=False).mean()
    pct_above = (w > w.rolling(20, min_periods=15).mean()).where(w.notna()).mean(axis=1)
    breadth_w = 0.5 * (ad_s / 0.30).clip(-1, 1) + 0.5 * ((pct_above - 0.5) * 2).clip(-1, 1)
    breadth = weekly_asof(breadth_w.to_frame("b"), idx.index)["b"]
    ad_daily = weekly_asof(pd.DataFrame({"ad": ad, "ad_ema4": ad_s, "pct_above_20w": pct_above}), idx.index)

    # volatility: 20d realised vol percentile vs trailing 2y (low vol = +1)
    rv = idx.pct_change().rolling(20).std() * np.sqrt(252)
    pct = rv.rolling(504, min_periods=126).apply(lambda a: (a[:-1] < a[-1]).mean() if len(a) > 1 else np.nan,
                                                  raw=True)
    vol = 1 - 2 * pct

    comp = pd.DataFrame({"trend": trend, "breadth": breadth, "vol": vol,
                         "close": idx, "ema21": e21, "ema50": e50, "rv20": rv})
    return comp.join(ad_daily)


def regime_score(comp: pd.DataFrame, w=(0.4, 0.4, 0.2)):
    return w[0] * comp["trend"] + w[1] * comp["breadth"] + w[2] * comp["vol"]


def regime_zone(score: float):
    if pd.isna(score):
        return None
    for name, th in REGIME_ZONES:
        if score >= th:
            return name
    return "STRONG_BEAR"
