"""Run the model: bring FYERS closes up to the last completed session, assemble the data, compute every schedule's
books, store the result. One run at a time; progress is readable while it runs."""
from __future__ import annotations
import math, os, threading, time, traceback
from datetime import datetime
import numpy as np, pandas as pd

from . import calendar as CAL, data_store as DS, db, fyers as FY
from .model import Model, FACTORS

CLOSE_READY = (16, 0)            # IST time after which today's official close is taken from FYERS
STATE = dict(running=False, progress="", step=0, steps=0, started=None, finished=None, error=None)
_lock = threading.Lock()
LATEST = {"result": None}
# first rebalance of the live (FYERS-data) period: the October book traded at the close of 26-Oct-2026
LIVE_START = pd.Timestamp(os.getenv("LIVE_START", "2026-10-26"))


def now_ist():
    return datetime.now(FY.IST)


def last_complete_session(holidays, now=None):
    now = now or now_ist()
    today = pd.Timestamp(now.date())
    is_session = lambda d: d.weekday() < 5 and d not in holidays
    if is_session(today) and (now.hour, now.minute) >= CLOSE_READY:
        return today
    d = today - pd.Timedelta(days=1)
    while not is_session(d):
        d -= pd.Timedelta(days=1)
    return d


def _p(msg, step=None):
    STATE["progress"] = msg
    if step is not None: STATE["step"] = step


def update_data(holidays):
    """fetch missing FYERS daily closes; returns (rows written, warnings)"""
    target = last_complete_session(holidays)
    syms = sorted(set(DS.current_members("NIFTY100")) | set(DS.current_members("NIFTY50")))
    base_cols = set(DS.Base.load(DS.HISTORY_FROM)["close"].columns)
    todo = [("index", s, CAL.INDEX_SYMBOLS[s]) for s in DS.INDEXES] + [("stock", s, CAL.eq_symbol(s)) for s in syms]
    last = db.last_dates()
    written, warn = 0, []
    # the Nifty 50 index first: its latest candle is the last real session (guards against a holiday missing from the list)
    if last.get("NIFTY50") is None or last["NIFTY50"] < target:
        STATE["steps"] = 1; _p("Fetching FYERS closes: NIFTY50", 1)
        start = DS.ANCHOR if last.get("NIFTY50") is None else last["NIFTY50"] - pd.Timedelta(days=10)
        h = FY.history(CAL.INDEX_SYMBOLS["NIFTY50"], start, target); h = h[h.date <= target]
        h["symbol"] = "NIFTY50"; written += db.upsert_closes(h)
        l50 = h.date.max() if len(h) else last.get("NIFTY50")
        if l50 is not None and l50 < target:
            warn.append(f"FYERS has no close for {target.date()} yet: run again later (if it was a market holiday, add it to data/ref/holidays.csv)")
            target = l50
    todo = [t for t in todo if t[1] != "NIFTY50"]
    need = [(k, s, f) for k, s, f in todo if last.get(s) is None or last[s] < target]
    STATE["steps"] = len(need)
    for i, (kind, s, fsym) in enumerate(need, 1):
        _p(f"Fetching FYERS closes {i}/{len(need)}: {s}", i)
        if last.get(s) is not None:
            start = last[s] - pd.Timedelta(days=10)
        elif kind == "stock" and s not in base_cols:
            start = pd.Timestamp(now_ist().date()) - pd.DateOffset(years=5)          # new index member: full FYERS history
        else:
            start = DS.ANCHOR
        try:
            h = FY.history(fsym, start, target)
        except Exception as e:                                                       # one symbol must not stop the run
            warn.append(f"{s}: FYERS history failed ({e})"); continue
        h = h[h.date <= target]
        if h.empty:
            warn.append(f"{s}: no FYERS candles from {start.date()} to {target.date()}"); continue
        h["symbol"] = s
        written += db.upsert_closes(h)
    warn += update_portfolio_futures(target)
    return written, warn, target


def portfolio_fut_symbols(p):
    return {CAL.fut_symbol(x["symbol"], p["contract"]): x["symbol"] for x in (p.get("positions") or [])}


def update_portfolio_futures(target, portfolios=None):
    """daily closes of the futures contracts held by saved portfolios (current, and closed ones still missing data)"""
    warn = []
    ports = portfolios if portfolios is not None else [p for p in db.list_portfolios(200)]
    last = db.last_dates("fut_daily")
    todo = []
    for p in ports:
        start = pd.Timestamp(p["trade_date"]) - pd.Timedelta(days=5)
        end = min(target, pd.Timestamp(p["contract"]))
        if p.get("closed_at"):
            end = min(end, pd.Timestamp(str(p["closed_at"])[:10]))
        for f in portfolio_fut_symbols(p):
            if last.get(f) is None or last[f] < end:
                todo.append((f, start if last.get(f) is None else last[f] - pd.Timedelta(days=5), end))
    seen = {}
    for f, a, b in todo:                               # one request per contract
        a0, b0 = seen.get(f, (a, b)); seen[f] = (min(a, a0), max(b, b0))
    for i, (f, (a, b)) in enumerate(seen.items(), 1):
        STATE["steps"] = len(seen); _p(f"Fetching futures closes for saved portfolios {i}/{len(seen)}", i)
        try:
            h = FY.history(f, a, b)
        except Exception as e:
            warn.append(f"{f}: futures history failed ({e})"); continue
        h = h[h.date <= b]
        if len(h):
            h["symbol"] = f; db.upsert_fut(h)
    return warn


def fut_closes_on(symbols, contract, day):
    """futures closes of `symbols` (contract expiring on `contract`) at the close of `day`; fetched from FYERS if missing"""
    day = pd.Timestamp(day)
    fs = {CAL.fut_symbol(s, contract): s for s in symbols}
    have = db.load_fut(fs, day - pd.Timedelta(days=7))
    have = have[have.date <= day]
    out, miss = {}, []
    for f, s in fs.items():
        g = have[(have.symbol == f) & (have.date == day)]
        if len(g): out[s] = float(g.close.iloc[-1])
        else: miss.append(f)
    for f in miss:
        try:
            h = FY.history(f, day - pd.Timedelta(days=7), day)
        except Exception:
            continue
        if len(h):
            h["symbol"] = f; db.upsert_fut(h)
            g = h[h.date == day]
            if len(g): out[fs[f]] = float(g.close.iloc[-1])
    return out


# ------------------------------------------------------------------ live track record of the model's rebalances
def track_record(M: Model, extras, sch):
    """every rebalance of a schedule from LIVE_START: the book (weights in force), held to the next rebalance (or the
    latest close), buy-and-hold within the period. Leg returns from adjusted closes; Nifty 50 TRI over the same days."""
    R = CAL.rebalance_dates(M.tc, M.E, sch)
    R = R[(R.rebalance >= LIVE_START) & (R.rebalance <= M.END)].reset_index(drop=True)
    cal = M.cal
    close = M.ctx.close.ffill(limit=10)
    tri = extras["tri50"].reindex(cal).ffill()
    out = []
    ds = lambda x: str(pd.Timestamp(x).date())
    for i, row in R.iterrows():
        t0 = row.rebalance
        t1 = R.rebalance.iloc[i + 1] if i + 1 < len(R) else M.END
        book, W = M.book(row.signal, M.weights_for(M.tc.prev_session(row.cycle_start))[0])
        days = cal[(cal >= t0) & (cal <= t1)]
        legs, hits = {}, {}
        for side in ("LONG", "SHORT"):
            b = book[book.side == side]
            P = close.loc[days, b.index]
            rel = P.div(P.iloc[0]) - 1
            legs[side] = (rel * b.weight.values).sum(axis=1)
            end_move = rel.iloc[-1]
            hits[side] = float((end_move > 0).mean() if side == "LONG" else (end_move < 0).mean())
        bench = tri.loc[days] / tri.loc[t0] - 1
        out.append(dict(rebalance=ds(t0), signal=ds(row.signal), kind=row.kind, offset=int(row.offset), end=ds(t1),
                        open=bool(i == len(R) - 1), dates=[ds(d) for d in days],
                        long=[round(float(x), 6) for x in legs["LONG"]], short=[round(float(x), 6) for x in legs["SHORT"]],
                        bench=[round(float(x), 6) for x in bench], hit_long=hits["LONG"], hit_short=hits["SHORT"],
                        longs=list(book.index[book.side == "LONG"]), shorts=list(book.index[book.side == "SHORT"]),
                        weights={k: round(float(W[k]), 4) for k in FACTORS}))
    return out


def _book_rows(t):
    cols = ["side", "rank", "final", "composite", "style_avg", "style", "sector", *FACTORS, "weight", "vol63"]
    out = []
    for sym, r in t[cols].iterrows():
        d = {"symbol": sym}
        for c in cols:
            v = r[c]
            if isinstance(v, (float, np.floating)):
                v = None if not math.isfinite(float(v)) else round(float(v), 6)
            elif isinstance(v, (np.integer,)):
                v = int(v)
            d[c] = v
        out.append(d)
    return out


def _lots(contract, universe):
    L = pd.read_csv(CAL.REF / "lot_sizes.csv")
    L = L[L.symbol.isin(universe)]
    key = pd.Timestamp(contract).strftime("%Y-%m")
    cur = L[L.contract == key].set_index("symbol")["lot_size"]
    latest = L.sort_values("contract").groupby("symbol").last()
    out, fallback = {}, []
    for s in latest.index:
        if s in cur.index:
            out[s] = int(cur[s])
        else:
            out[s] = int(latest.at[s, "lot_size"]); fallback.append(s)
    return out, fallback, key


def serialize(M: Model, extras, warn, raw: pd.DataFrame):
    END = M.END
    ds = lambda x: None if x is None or (isinstance(x, float) and math.isnan(x)) else str(pd.Timestamp(x).date())
    res = dict(data_end=ds(END), model="PRESTIGE", warnings=list(warn), jumps=extras["jumps"], new_symbols=extras["new_symbols"],
               new_dates=[ds(d) for d in extras["new_dates"]], schedules={}, lots={})
    W = M.W.tail(14)
    res["weights_history"] = [dict(signal=ds(d), **{k: round(float(W.at[d, k]), 4) for k in FACTORS}) for d in W.index]
    res["expiries"] = [ds(e) for e in M.E[M.E > END - pd.Timedelta(days=40)]]
    contracts = set()
    for sch in CAL.SCHEDULES:
        v = M.schedule_view(sch)
        def leg(row, book, Wt, contract):
            if row is None: return None
            r = dict(rebalance=ds(row["rebalance"]), signal=ds(row["signal"]), kind=row["kind"], offset=int(row["offset"]),
                     cycle_start=ds(row["cycle_start"]), contract=ds(contract), fut_code=pd.Timestamp(contract).strftime("%y") + CAL.MONTHS[pd.Timestamp(contract).month - 1])
            if book is not None:
                r["weights"] = {k: round(float(Wt[k]), 4) for k in FACTORS}
                r["book"] = _book_rows(book[book.side != ""])
            else:
                r["book"] = None
            return r
        contracts |= {v["held_contract"], v["next_contract"], CAL.contract_for(M.tc, M.E, M.tc.next_session(END))}
        res["schedules"][sch] = dict(
            held=leg(v["held"], v["held_book"], v["held_W"], v["held_contract"]),
            next=leg(v["next"], v["next_book"], v.get("next_W"), v["next_contract"]),
            upcoming=[dict(rebalance=ds(r.rebalance), signal=ds(r.signal), kind=r.kind, offset=int(r.offset)) for r in v["upcoming"].itertuples()])
        if sch == "monthly":
            td = M.tc.next_session(END)                        # trading on the latest close's view: next session's close
            res["latest"] = dict(signal=ds(END), trade_day=ds(td), contract=ds(CAL.contract_for(M.tc, M.E, td)),
                                 weights={k: round(float(v["latest_W"][k]), 4) for k in FACTORS},
                                 weights_set_on=ds(M.weights_for(END)[1]), rankings=_book_rows(v["latest_book"]))
    uni = set(DS.current_members("NIFTY50")) | {x["symbol"] for x in res["latest"]["rankings"]}
    for c in contracts:
        lots, fb, key = _lots(c, uni)
        miss = sorted(uni - set(lots))
        if miss:
            res["warnings"].append(f"No lot size at all for {', '.join(miss)} (add them to data/ref/lot_sizes.csv)")
        res["lots"][ds(c)] = dict(month=key, lots=lots, fallback=fb)
        if fb:
            res["warnings"].append(f"Lot sizes for the {key} contract are not in data/ref/lot_sizes.csv yet for {len(fb)} "
                                   f"Nifty 50 stocks; the latest known lot sizes are used until NSE publishes them")
    # raw FYERS closes (unadjusted, what the market quotes) on the dates the UI needs as reference prices
    need = {END} | {pd.Timestamp(res["schedules"][s][k]["rebalance"]) for s in res["schedules"] for k in ("held",) if res["schedules"][s][k]}
    prev = M.tc.prev_session(END); need.add(prev)
    R = raw[raw.date.isin(list(need))]
    res["raw_close"] = {ds(d): {s: float(c) for s, c in zip(g.symbol, g.close)} for d, g in R.groupby("date")}
    res["prev_session"] = ds(prev)
    hol = CAL.load_holidays()
    if max(hol) < END + pd.Timedelta(days=60):
        res["warnings"].append("data/ref/holidays.csv ends within 60 days: add the next NSE trading-holiday list so expiry and "
                               "rebalance dates stay right")
    res["sectors_ok"] = True
    res["live_start"] = str(LIVE_START.date())
    res["track"] = {sch: track_record(M, extras, sch) for sch in CAL.SCHEDULES}
    t50 = extras["tri50"]
    t50 = t50[t50.index >= DS.ANCHOR - pd.Timedelta(days=10)]
    res["bench"] = {ds(d): round(float(v), 4) for d, v in t50.items()}
    res["bench_note"] = ("Nifty 50 TRI to 23-Oct-2026; after that extended with Nifty 50 price returns from FYERS "
                         "(dividends, about 0.1% a month, not included)")
    return res


def run(user="system", fetch=True):
    """full run; returns the result dict (raises on failure)"""
    holidays = CAL.load_holidays()
    warn = []
    if fetch:
        _p("Logging in to FYERS"); FY.client()
        n, w, target = update_data(holidays); warn += w
        _p(f"FYERS data up to {target.date()} ({n} rows written)")
    _p("Assembling data")
    raw = db.load_closes()
    data, extras, w2 = DS.assemble(raw)
    warn += w2
    _p("Computing factors and books")
    M = Model(data, extras, holidays)
    res = serialize(M, extras, warn, raw)
    res["run_by"] = user
    db.save_run(res, user)
    LATEST["result"] = db.latest_run()
    return LATEST["result"]


def start(user, fetch=True):
    with _lock:
        if STATE["running"]:
            return False
        STATE.update(running=True, error=None, started=time.time(), finished=None, progress="Starting", step=0, steps=0)

    def job():
        try:
            run(user, fetch)
            _p("Done")
        except Exception as e:
            STATE["error"] = f"{type(e).__name__}: {e}"
            STATE["trace"] = traceback.format_exc()[-2000:]
        finally:
            STATE.update(running=False, finished=time.time())
    threading.Thread(target=job, daemon=True).start()
    return True


def latest():
    if LATEST["result"] is None:
        LATEST["result"] = db.latest_run()
    return LATEST["result"]
