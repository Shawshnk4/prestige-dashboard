"""Model data = frozen base history (data/base, vendor-clean, ends on the anchor date) + FYERS daily closes after it.

Stocks       new close = base close on the anchor date x (FYERS close / FYERS close on the anchor date), so the
             series continues the split/bonus-adjusted base history. data/ref/price_adjustments.csv can correct a
             corporate action that FYERS has not adjusted (factor applied to FYERS closes before the ex-date).
Nifty 100    TRI extended with the price index's daily returns (FYERS has no TRI); same for the Nifty 50 TRI.
India VIX    from FYERS.
Membership   the anchor-date Nifty 100 and Nifty 50 lists carried forward, then data/ref/membership_changes.csv
             (effective_date, index, symbol, action) applied from each effective date.
New symbols  (index entrants not in the base) use FYERS closes as they are, back to the first FYERS date.
Index rows in the FYERS table use the names NIFTY50, NIFTY100, INDIAVIX; the trading calendar after the anchor is the
set of dates with a NIFTY50 close."""
from __future__ import annotations
import os
from pathlib import Path
import numpy as np, pandas as pd

ROOT = Path(__file__).resolve().parent.parent / "data"
ANCHOR = pd.Timestamp("2026-10-23")
INDEXES = ("NIFTY50", "NIFTY100", "INDIAVIX")
ANCHOR_TOL = 0.02          # FYERS vs base close on the anchor date
JUMP_FLAG = 0.15           # daily move flagged for review


# Live runs use history from 2016 only, and only stocks that were in the Nifty 100 or Nifty 50 at some point since then.
# Indicator warm-up and every rolling window end well before mid-2023, where the 36-month weight window of the current
# books begins, so the live books, factor values and weights are identical to a full-history run (tests check this);
# it keeps a run inside 512 MB of memory. HISTORY_FROM= (empty) loads the full 2009-2026 history.
HISTORY_FROM = os.getenv("HISTORY_FROM", "2016-01-01") or None


class Base:
    """the frozen panels, loaded once per process (per history start)"""
    _cache = {}

    @classmethod
    def load(cls, start=None):
        key = start or "full"
        if key not in cls._cache:
            b = ROOT / "base"
            d = dict(close=pd.read_parquet(b / "close.parquet"), member=pd.read_parquet(b / "member100.parquet").astype(bool),
                     tri=pd.read_parquet(b / "nifty100.parquet"), vix=pd.read_parquet(b / "vix.parquet")["vix"],
                     member50=pd.read_parquet(b / "member50.parquet").astype(bool),
                     tri50=pd.read_parquet(b / "nifty50_tri.parquet")["nifty50_tri"])
            assert d["close"].index[-1] == ANCHOR
            if start is not None:
                t = pd.Timestamp(start)
                keep = d["member"].loc[t:].any() | d["member50"].reindex(columns=d["member"].columns).loc[t:].fillna(False).astype(bool).any()
                cols = [c for c in d["close"].columns if keep.get(c, False)]
                if "TMPV" in cols and "TATAMOTORS" not in cols and "TATAMOTORS" in d["close"].columns:
                    cols.append("TATAMOTORS")
                d["close"] = d["close"].loc[t:, cols]; d["member"] = d["member"].loc[t:, cols]
                d["tri"] = d["tri"].loc[t:]; d["vix"] = d["vix"].loc[t:]
                d["member50"] = d["member50"].loc[t:, [c for c in d["member50"].columns if c in cols]]
                d["tri50"] = d["tri50"].loc[t:]
            cls._cache = {key: d}                    # keep one version in memory
        return cls._cache[key]


def current_members(index="NIFTY100", asof=None) -> list:
    """members on `asof` (default: latest) after applying membership changes"""
    b = Base.load(HISTORY_FROM)
    m = (b["member"] if index == "NIFTY100" else b["member50"]).loc[ANCHOR]
    s = set(m[m].index)
    for _, r in _changes().iterrows():
        if r["index"] != index or (asof is not None and r.effective_date > pd.Timestamp(asof)):
            continue
        (s.add if r.action == "add" else s.discard)(r.symbol)
    return sorted(s)


def _changes():
    p = ROOT / "ref" / "membership_changes.csv"
    if not p.exists():
        return pd.DataFrame(columns=["effective_date", "index", "symbol", "action"])
    c = pd.read_csv(p, parse_dates=["effective_date"], comment="#")
    c["index"] = c["index"].str.upper().str.replace(" ", ""); c["action"] = c["action"].str.lower().str.strip()
    return c


def _adjustments():
    p = ROOT / "ref" / "price_adjustments.csv"
    if not p.exists():
        return pd.DataFrame(columns=["symbol", "ex_date", "factor"])
    return pd.read_csv(p, parse_dates=["ex_date"], comment="#")


def assemble(fyers: pd.DataFrame, history_from="default"):
    """fyers: long table [symbol, date, close] of FYERS daily closes (dates >= the anchor date for base symbols).
    history_from: first date of the history used (default HISTORY_FROM; None = full history).
    Returns (data dict in the research layout, extras dict, list of warnings)."""
    b = Base.load(HISTORY_FROM if history_from == "default" else history_from)
    warn = []
    F = fyers.copy(); F["date"] = pd.to_datetime(F["date"]).dt.normalize()
    F = F.pivot_table(index="date", columns="symbol", values="close", aggfunc="last").sort_index()
    for _, a in _adjustments().iterrows():
        if a.symbol in F:
            F.loc[F.index < a.ex_date, a.symbol] *= float(a.factor)
    if "NIFTY50" not in F or ANCHOR not in F.index:
        raise ValueError("FYERS data must include the NIFTY50 index from the anchor date 23-Oct-2026")
    NEW = F.index[(F.index > ANCHOR) & F["NIFTY50"].notna()]
    c0 = b["close"]
    cols = list(c0.columns)
    mem_syms = sorted(set(current_members("NIFTY100")) | set(current_members("NIFTY50")))
    new_syms = [s for s in mem_syms if s not in cols]
    cols_all = cols + new_syms
    newc = pd.DataFrame(np.nan, index=NEW, columns=cols_all)
    for s in mem_syms:
        if s not in F:
            warn.append(f"{s}: no FYERS data"); continue
        if s in cols:
            a, f0 = c0.at[ANCHOR, s], F.at[ANCHOR, s] if ANCHOR in F.index else np.nan
            if not (np.isfinite(a) and np.isfinite(f0)):
                warn.append(f"{s}: missing close on the anchor date; not extended"); continue
            if abs(f0 / a - 1) > ANCHOR_TOL:
                warn.append(f"{s}: FYERS anchor close differs from the base by {f0 / a - 1:+.1%}")
            newc[s] = a * F[s].reindex(NEW) / f0
        else:
            newc[s] = F[s].reindex(NEW)
    close = pd.concat([c0.reindex(columns=cols_all), newc])
    for s in new_syms:                       # entrants: their FYERS history before the anchor too
        h = F[s].dropna(); h = h[h.index <= ANCHOR]
        close.loc[close.index.isin(h.index), s] = h.reindex(close.index[close.index.isin(h.index)]).values
        warn.append(f"{s}: new index member, history from FYERS since {F[s].first_valid_index().date()} "
                    f"(check sector in data/ref/sectors.csv and style in data/ref/styles.csv)")
    # membership: anchor lists carried forward + changes
    def extend_member(M0, index, columns):
        M = pd.concat([M0.reindex(columns=columns, fill_value=False),
                       pd.DataFrame([M0.loc[ANCHOR].reindex(columns, fill_value=False).values] * len(NEW), index=NEW, columns=columns)])
        M = M.astype(bool)
        for _, r in _changes().iterrows():
            if r["index"] != index or r.symbol not in M: continue
            M.loc[M.index >= r.effective_date, r.symbol] = (r.action == "add")
        return M
    member = extend_member(b["member"], "NIFTY100", cols_all)
    c50 = list(b["member50"].columns) + [x for x in current_members("NIFTY50") if x not in b["member50"].columns]
    member50 = extend_member(b["member50"], "NIFTY50", c50)          # own columns, as the research membership file
    # indices
    t = b["tri"]; n100 = F["NIFTY100"].reindex(NEW) if "NIFTY100" in F else pd.Series(np.nan, index=NEW)
    if "NIFTY100" in F and ANCHOR in F.index and abs(F.at[ANCHOR, "NIFTY100"] / t.at[ANCHOR, "close"] - 1) > 1e-4:
        warn.append("NIFTY100: FYERS anchor close differs from the base")
    nt = pd.DataFrame(index=NEW, columns=t.columns, dtype=float)
    nt["close"] = n100.values
    nt["tri"] = t.at[ANCHOR, "tri"] * (n100 / t.at[ANCHOR, "close"]).values
    tri = pd.concat([t, nt])
    vix = pd.concat([b["vix"], F["INDIAVIX"].reindex(NEW) if "INDIAVIX" in F else pd.Series(np.nan, index=NEW)])
    vix.name = "close"
    t50 = b["tri50"]
    tri50 = pd.concat([t50, t50.loc[ANCHOR] * F["NIFTY50"].reindex(NEW) / F.at[ANCHOR, "NIFTY50"]])
    # checks
    nc = close.loc[NEW, [s for s in mem_syms if s in close]]
    for s in nc.columns:
        miss = nc[s].isna().sum()
        if miss: warn.append(f"{s}: {miss} missing session(s) after the anchor date")
    r = close.loc[close.index >= ANCHOR, nc.columns].pct_change(fill_method=None).loc[NEW]
    big = r.abs().stack(); big = big[big > JUMP_FLAG]
    jumps = [dict(symbol=s, date=str(d.date()), move=float(r.at[d, s])) for (d, s) in big.index]
    data = dict(close=close, member=member, tri=tri, vix=vix)
    extras = dict(member50=member50, tri50=tri50, new_dates=list(NEW), new_symbols=new_syms, jumps=jumps)
    return data, extras, warn
