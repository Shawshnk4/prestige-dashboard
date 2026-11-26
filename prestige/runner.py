"""Run the model: bring FYERS closes up to the last completed session, assemble the data, compute every schedule's
books, store the result. One run at a time; progress is readable while it runs."""
from __future__ import annotations
import math, threading, time, traceback
from datetime import datetime
import numpy as np, pandas as pd

from . import calendar as CAL, data_store as DS, db, fyers as FY
from .model import Model, FACTORS

CLOSE_READY = (16, 0)            # IST time after which today's official close is taken from FYERS
STATE = dict(running=False, progress="", step=0, steps=0, started=None, finished=None, error=None)
_lock = threading.Lock()
LATEST = {"result": None}


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
    base_cols = set(DS.Base.load()["close"].columns)
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
    return written, warn, target


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
