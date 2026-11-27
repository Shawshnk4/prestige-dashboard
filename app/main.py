"""PRESTIGE dashboard server (FastAPI). Serves the single-page dashboard and its API.

Environment (Render -> Environment):
  DATABASE_URL        Supabase Postgres connection string (Transaction pooler URI, port 6543)
  SUPABASE_URL        https://<project>.supabase.co          (team login)
  SUPABASE_ANON_KEY   the project's anon/public key          (team login)
  ALLOWED_EMAILS      comma-separated team emails allowed in (empty = any user created in Supabase Auth)
  FYERS_*             see prestige/fyers.py
  CRON_SECRET         optional: lets a scheduler call POST /api/cron/run with header X-Cron-Secret
  QUOTE_TTL           seconds a live quote is reused (default 60)"""
from __future__ import annotations
import hashlib, os, threading, time
from pathlib import Path

import prestige  # noqa: F401  (reads .env and trims stray spaces / line breaks in settings first)
import httpx
import pandas as pd
from fastapi import Body, Depends, FastAPI, Header, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from prestige import calendar as CAL, data_store as DS, db, fyers as FY, runner

ROOT = Path(__file__).resolve().parent.parent
app = FastAPI(title="PRESTIGE", docs_url=None, redoc_url=None, openapi_url=None)
app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")

SUPABASE_URL = os.getenv("SUPABASE_URL", "").rstrip("/")
SUPABASE_ANON_KEY = os.getenv("SUPABASE_ANON_KEY", "")
ALLOWED = {e.strip().lower() for e in os.getenv("ALLOWED_EMAILS", "").split(",") if e.strip()}
AUTH_OFF = os.getenv("AUTH_DISABLED") == "1" and not os.getenv("RENDER")         # local testing only, never on Render
QUOTE_TTL = int(os.getenv("QUOTE_TTL", "60"))


@app.on_event("startup")
def _startup():
    try:
        db.init()
    except Exception as e:                      # keep the site up so the Status tab can show the reason
        STARTUP_ERROR["db"] = _clean(f"{type(e).__name__}: {e}")


STARTUP_ERROR: dict = {}


def _clean(msg: str) -> str:
    """error text safe to show: no connection-string passwords or keys"""
    import re
    msg = re.sub(r"postgres(ql)?://[^\s'\"]+", "postgresql://…", msg)
    msg = re.sub(r"(password|secret|token|key)=\S+", r"\1=…", msg, flags=re.I)
    msg = re.sub(r"sb_(publishable|secret)_[A-Za-z0-9_\-]+", r"sb_\1_…", msg)
    msg = re.sub(r"eyJ[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+\.?[A-Za-z0-9_\-]*", "eyJ…", msg)
    return msg[:400]


@app.exception_handler(Exception)
async def _any_error(request, exc):
    hint = ""
    t = f"{type(exc).__name__}: {exc}"
    if "psycopg" in type(exc).__module__ or "OperationalError" in t:
        hint = " (database: check DATABASE_URL on Render: Transaction pooler address, port 6543, your database password in place of [YOUR-PASSWORD])"
    elif "httpx" in type(exc).__module__:
        hint = " (sign-in check: SUPABASE_URL on Render must be your https://….supabase.co address)"
    return JSONResponse(status_code=500, content={"detail": _clean(t) + hint})


# ------------------------------------------------------------------ auth (Supabase Auth session token)
_auth_cache: dict = {}


def user(authorization: str | None = Header(default=None)) -> str:
    if AUTH_OFF:
        return "local"
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(401, "Sign in first")
    tok = authorization.split(" ", 1)[1]
    key = hashlib.sha256(tok.encode()).hexdigest()
    hit = _auth_cache.get(key)
    if hit and time.time() - hit[1] < 300:
        email = hit[0]
    else:
        if not SUPABASE_URL or not SUPABASE_ANON_KEY:
            raise HTTPException(500, "SUPABASE_URL / SUPABASE_ANON_KEY are not set on the server")
        r = httpx.get(f"{SUPABASE_URL}/auth/v1/user", headers={"apikey": SUPABASE_ANON_KEY, "Authorization": f"Bearer {tok}"}, timeout=10)
        if r.status_code != 200:
            raise HTTPException(401, "Session expired: sign in again")
        email = (r.json().get("email") or "").lower()
        _auth_cache[key] = (email, time.time())
    if ALLOWED and email not in ALLOWED:
        raise HTTPException(403, f"{email} is not on the PRESTIGE team list")
    return email


# ------------------------------------------------------------------ pages and config
@app.get("/")
def index():
    return FileResponse(ROOT / "static" / "index.html", headers={"Cache-Control": "no-cache"})


@app.get("/api/config")
def config():
    return dict(supabase_url=SUPABASE_URL, supabase_anon_key=SUPABASE_ANON_KEY, auth_disabled=AUTH_OFF)


@app.get("/api/status")
def status(u: str = Depends(user)):
    if STARTUP_ERROR.get("db"):
        raise HTTPException(500, "Database not reachable: " + STARTUP_ERROR["db"] +
                            " (check DATABASE_URL on Render: Transaction pooler address, port 6543, your database password)")
    lr = runner.latest()
    last = db.last_dates()
    hol = CAL.load_holidays()
    return dict(user=u, fyers_configured=FY.configured(), fyers_problems=FY.settings_problems(), fyers_token=FY.token_status(), run=_run_state(),
                latest_run=None if not lr else dict(data_end=lr.get("data_end"), run_at=lr.get("run_at"), run_by=lr.get("run_by"),
                                                     warnings=lr.get("warnings", []), jumps=lr.get("jumps", [])),
                stored=dict(symbols=len(last), last_date=str(max(last.values()).date()) if last else None,
                            stale=sorted(s for s, d in last.items() if last and d < max(last.values()))),
                expected_session=str(runner.last_complete_session(hol).date()),
                holidays_until=str(max(hol).date()), anchor=str(DS.ANCHOR.date()),
                members=dict(nifty100=len(DS.current_members("NIFTY100")), nifty50=len(DS.current_members("NIFTY50"))))


def _run_state():
    s = {k: v for k, v in runner.STATE.items() if k != "trace"}
    return s


# ------------------------------------------------------------------ model
@app.post("/api/run")
def run_model(body: dict = Body(default={}), u: str = Depends(user)):
    fetch = bool(body.get("fetch", True))
    if fetch and not FY.configured():
        raise HTTPException(400, "FYERS credentials are not set on the server (see README, step 4)")
    if not runner.start(u, fetch):
        raise HTTPException(409, "A run is already in progress")
    return _run_state()


@app.get("/api/run")
def run_state(u: str = Depends(user)):
    return _run_state()


@app.post("/api/cron/run")
def cron_run(x_cron_secret: str | None = Header(default=None)):
    sec = os.getenv("CRON_SECRET")
    if not sec or x_cron_secret != sec:
        raise HTTPException(403, "bad secret")
    started = runner.start("scheduler", True)
    return dict(started=started)


@app.get("/api/model")
def model(u: str = Depends(user)):
    r = runner.latest()
    if r is None:
        return JSONResponse({"no_run": True})        # not an error: nothing has been run yet
    return JSONResponse(r)


# ------------------------------------------------------------------ live prices (cash + futures), cached
_qcache: dict = {}
_qlock = threading.Lock()


def _quotes(fsyms):
    now = time.time()
    with _qlock:
        fresh = {s: _qcache[s][1] for s in fsyms if s in _qcache and now - _qcache[s][0] < QUOTE_TTL}
    miss = [s for s in fsyms if s not in fresh]
    if miss:
        got = FY.quotes(miss)
        with _qlock:
            for s, v in got.items():
                _qcache[s] = (now, v)
        fresh.update(got)
    return fresh


@app.post("/api/prices")
def prices(body: dict = Body(...), u: str = Depends(user)):
    syms = [s for s in body.get("symbols", []) if isinstance(s, str)][:120]
    contract = body.get("contract")
    want = {}
    for s in syms:
        want[CAL.eq_symbol(s)] = (s, "cash")
        if contract:
            want[CAL.fut_symbol(s, contract)] = (s, "fut")
    if body.get("index", True):
        want[CAL.INDEX_SYMBOLS["NIFTY50"]] = ("NIFTY50", "cash")
    try:
        q = _quotes(list(want))
    except Exception as e:
        raise HTTPException(502, f"FYERS quotes failed: {e}")
    out = {}
    for f, (s, kind) in want.items():
        if f in q:
            out.setdefault(s, {})[kind] = q[f]
    return dict(asof=pd.Timestamp.now(tz=FY.IST).isoformat(timespec="seconds"), contract=contract, prices=out,
                missing=[f for f in want if f not in q])


# ------------------------------------------------------------------ portfolios
@app.get("/api/portfolios")
def portfolios(u: str = Depends(user)):
    return dict(current=db.current_portfolio(), history=db.list_portfolios())


def _exit_prices(p):
    if not p or not p.get("positions"):
        return None
    syms = [x["symbol"] for x in p["positions"]]
    try:
        q = _quotes([CAL.fut_symbol(s, p["contract"]) for s in syms] + [CAL.eq_symbol(s) for s in syms])
    except Exception:
        return None
    return {s: dict(fut=(q.get(CAL.fut_symbol(s, p["contract"])) or {}).get("lp"), cash=(q.get(CAL.eq_symbol(s)) or {}).get("lp")) for s in syms}


@app.post("/api/portfolios")
def save_portfolio(body: dict = Body(...), u: str = Depends(user)):
    pos = body.get("positions") or []
    if not pos or not all({"symbol", "side", "weight", "lots", "lot_size"} <= set(x) for x in pos):
        raise HTTPException(400, "positions must list symbol, side, weight, lots, lot_size")
    for k in ("capital", "hedge", "contract"):
        if body.get(k) in (None, ""):
            raise HTTPException(400, f"{k} is required")
    cur = db.current_portfolio()
    if cur:
        db.close_portfolio(cur["id"], u, _exit_prices(cur))
    saved = db.save_portfolio(body, u)
    if FY.configured() and not FY.settings_problems():          # start collecting its futures closes for the graph
        def fetch():
            try:
                runner.update_portfolio_futures(runner.last_complete_session(CAL.load_holidays()), [saved])
            except Exception:
                pass
        threading.Thread(target=fetch, daemon=True).start()
    return saved


@app.post("/api/fut_close")
def fut_close(body: dict = Body(...), u: str = Depends(user)):
    """futures closes on a past date, to fill in entry prices when a portfolio was traded at that close"""
    syms = [s for s in body.get("symbols", []) if isinstance(s, str)][:60]
    if not syms or not body.get("contract") or not body.get("date"):
        raise HTTPException(400, "symbols, contract and date are required")
    try:
        got = runner.fut_closes_on(syms, body["contract"], body["date"])
    except Exception as e:
        raise HTTPException(502, f"FYERS history failed: {e}")
    return dict(date=body["date"], closes=got, missing=[s for s in syms if s not in got])


@app.get("/api/portfolios/{pid}/series")
def portfolio_series(pid: int, u: str = Depends(user)):
    """daily P&L of a saved portfolio from its futures closes, and the Nifty 50 TRI over the same days"""
    p = db.get_portfolio(pid)
    if not p:
        raise HTTPException(404, "no such portfolio")
    fs = runner.portfolio_fut_symbols(p)
    start = pd.Timestamp(p["trade_date"])
    end = pd.Timestamp(str(p["closed_at"])[:10]) if p.get("closed_at") else None
    F = db.load_fut(fs, start)
    piv = F.pivot_table(index="date", columns="symbol", values="close", aggfunc="last").sort_index() if len(F) else pd.DataFrame()
    if len(piv):
        piv = piv[(piv.index >= start) & ((piv.index <= end) if end is not None else True)].ffill()
    res = runner.latest() or {}
    b = pd.Series(res.get("bench", {}), dtype=float); b.index = pd.to_datetime(b.index)
    out = dict(id=p["id"], start=str(start.date()), capital=p["capital"], hedge=p["hedge"], bench_note=res.get("bench_note"),
               dates=[], pnl=[], long_pnl=[], short_pnl=[], ret=[], bench=[], missing=[])
    if not len(piv):
        out["pending"] = True
        return out
    pos = {CAL.fut_symbol(x["symbol"], p["contract"]): x for x in p["positions"]}
    out["missing"] = [x["symbol"] for f, x in pos.items() if f not in piv.columns]
    L = pd.Series(0.0, index=piv.index); S = pd.Series(0.0, index=piv.index)
    for f, x in pos.items():
        if f not in piv.columns:
            continue
        sign = 1 if x["side"] == "LONG" else -1
        v = sign * x["lots"] * x["lot_size"] * (piv[f] - float(x["entry_fut"]))
        if sign > 0: L = L + v
        else: S = S + v
    base = b[b.index <= start]
    bser = (b.reindex(piv.index, method="ffill") / base.iloc[-1] - 1) if len(base) else pd.Series(float("nan"), index=piv.index)
    cap = float(p["capital"])
    out.update(dates=[str(d.date()) for d in piv.index], pnl=[round(float(x), 2) for x in L + S],
               long_pnl=[round(float(x), 2) for x in L], short_pnl=[round(float(x), 2) for x in S],
               ret=[round(float(x) / cap, 6) for x in L + S], bench=[None if pd.isna(x) else round(float(x), 6) for x in bser],
               bench_last=float(b.iloc[-1]) if len(b) else None, bench_last_date=str(b.index[-1].date()) if len(b) else None)
    return out


@app.post("/api/portfolios/{pid}/close")
def close_portfolio(pid: int, u: str = Depends(user)):
    cur = db.current_portfolio()
    if not cur or cur["id"] != pid:
        raise HTTPException(404, "not the current portfolio")
    db.close_portfolio(pid, u, _exit_prices(cur))
    return dict(closed=pid)
