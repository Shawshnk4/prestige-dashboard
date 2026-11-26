"""Storage. Production: the Supabase Postgres database (DATABASE_URL = the Supabase connection string).
Local testing: DATABASE_URL=sqlite:///path/to/file.db. Tables are created on start if missing.
Row-level security is switched on for every table in Postgres with no policies, so the public (anon) key cannot read
or write them; only this server, connecting as the database owner, can."""
from __future__ import annotations
import json, os, sqlite3, threading
from contextlib import contextmanager
import pandas as pd

URL = os.getenv("DATABASE_URL", "sqlite:///./prestige_local.db")
IS_SQLITE = URL.startswith("sqlite")
_lock = threading.Lock()

SCHEMA_PG = """
create table if not exists fyers_daily (symbol text not null, d date not null, close double precision not null,
  fetched_at timestamptz not null default now(), primary key (symbol, d));
create table if not exists runs (id bigserial primary key, created_at timestamptz not null default now(), created_by text,
  data_end date, result jsonb);
create table if not exists portfolios (id bigserial primary key, created_at timestamptz not null default now(), created_by text,
  name text, schedule text, status text not null default 'current', capital double precision, hedge double precision,
  trade_date date, signal_date date, contract date, positions jsonb, closed_at timestamptz, closed_by text, exit_prices jsonb, note text);
create table if not exists app_state (key text primary key, value text, updated_at timestamptz not null default now());
alter table fyers_daily enable row level security;
alter table runs enable row level security;
alter table portfolios enable row level security;
alter table app_state enable row level security;
"""
SCHEMA_SQLITE = """
create table if not exists fyers_daily (symbol text not null, d text not null, close real not null,
  fetched_at text default current_timestamp, primary key (symbol, d));
create table if not exists runs (id integer primary key autoincrement, created_at text default current_timestamp, created_by text,
  data_end text, result text);
create table if not exists portfolios (id integer primary key autoincrement, created_at text default current_timestamp, created_by text,
  name text, schedule text, status text not null default 'current', capital real, hedge real, trade_date text, signal_date text,
  contract text, positions text, closed_at text, closed_by text, exit_prices text, note text);
create table if not exists app_state (key text primary key, value text, updated_at text default current_timestamp);
"""


@contextmanager
def conn():
    if IS_SQLITE:
        with _lock:
            c = sqlite3.connect(URL.replace("sqlite:///", ""))
            c.row_factory = sqlite3.Row
            try:
                yield c; c.commit()
            finally:
                c.close()
    else:
        import psycopg
        from psycopg.rows import dict_row
        with psycopg.connect(URL, row_factory=dict_row, autocommit=False, prepare_threshold=None) as c:
            yield c


def q(sql: str) -> str:
    return sql if IS_SQLITE else sql.replace("?", "%s")


def init():
    with conn() as c:
        if IS_SQLITE:
            c.executescript(SCHEMA_SQLITE)
        else:
            c.execute(SCHEMA_PG)


def _rows(cur):
    return [dict(r) for r in cur.fetchall()]


def execute(sql, params=()):
    with conn() as c:
        cur = c.execute(q(sql), params)
        try:
            return _rows(cur)
        except Exception:
            return []


def _json_in(x):
    return json.dumps(x, default=str)


def ist(x):
    """timestamps are stored in UTC; shown in IST"""
    if x is None:
        return None
    t = pd.Timestamp(x)
    t = t.tz_localize("UTC") if t.tzinfo is None else t
    return t.tz_convert("Asia/Kolkata").isoformat(timespec="seconds")


def _json_out(x):
    if x is None or isinstance(x, (dict, list)):
        return x
    return json.loads(x)


# ------------------------------------------------------------------ FYERS daily closes
def upsert_closes(df: pd.DataFrame):
    if df.empty:
        return 0
    rows = [(str(s), str(pd.Timestamp(d).date()), float(v)) for s, d, v in df[["symbol", "date", "close"]].itertuples(index=False)]
    sql = q("insert into fyers_daily (symbol, d, close) values (?, ?, ?) "
            "on conflict (symbol, d) do update set close = excluded.close, fetched_at = " + ("current_timestamp" if IS_SQLITE else "now()"))
    with conn() as c:
        if IS_SQLITE:
            c.executemany(sql, rows)
        else:
            with c.cursor() as cur:
                cur.executemany(sql, rows)
    return len(rows)


def load_closes(since="2000-01-01") -> pd.DataFrame:
    rows = execute("select symbol, d, close from fyers_daily where d >= ? order by d, symbol", (since,))
    df = pd.DataFrame(rows, columns=["symbol", "d", "close"]).rename(columns={"d": "date"})
    df["date"] = pd.to_datetime(df["date"])
    return df


def last_dates() -> dict:
    return {r["symbol"]: pd.Timestamp(r["m"]) for r in execute("select symbol, max(d) as m from fyers_daily group by symbol")}


# ------------------------------------------------------------------ app state (FYERS token, last run status)
def get_state(key):
    r = execute("select value, updated_at from app_state where key = ?", (key,))
    return (r[0]["value"], r[0]["updated_at"]) if r else (None, None)


def set_state(key, value):
    execute("insert into app_state (key, value) values (?, ?) on conflict (key) do update set value = excluded.value, "
            "updated_at = " + ("current_timestamp" if IS_SQLITE else "now()"), (key, value))


# ------------------------------------------------------------------ model runs
def save_run(result: dict, user: str):
    sql = "insert into runs (created_by, data_end, result) values (?, ?, ?)"
    execute(sql, (user, result["data_end"], _json_in(result)))


def latest_run():
    r = execute("select id, created_at, created_by, result from runs order by id desc limit 1")
    if not r:
        return None
    x = r[0]; res = _json_out(x["result"]); res["run_id"] = x["id"]; res["run_at"] = ist(x["created_at"]); res["run_by"] = x["created_by"]
    return res


# ------------------------------------------------------------------ portfolios
PCOLS = ["id", "created_at", "created_by", "name", "schedule", "status", "capital", "hedge", "trade_date", "signal_date",
         "contract", "positions", "closed_at", "closed_by", "exit_prices", "note"]


def _p(r):
    r = dict(r)
    for k in ("positions", "exit_prices"):
        r[k] = _json_out(r.get(k))
    for k in ("created_at", "closed_at"):
        r[k] = ist(r.get(k))
    for k in ("trade_date", "signal_date", "contract"):
        if r.get(k) is not None: r[k] = str(r[k])[:10]
    return r


def list_portfolios(limit=50):
    return [_p(r) for r in execute(f"select {', '.join(PCOLS)} from portfolios order by id desc limit {int(limit)}")]


def current_portfolio():
    r = execute(f"select {', '.join(PCOLS)} from portfolios where status = 'current' order by id desc limit 1")
    return _p(r[0]) if r else None


def close_portfolio(pid, user, exit_prices):
    now = "current_timestamp" if IS_SQLITE else "now()"
    execute(f"update portfolios set status = 'closed', closed_at = {now}, closed_by = ?, exit_prices = ? where id = ? and status = 'current'",
            (user, _json_in(exit_prices), int(pid)))


def save_portfolio(p: dict, user: str):
    sql = ("insert into portfolios (created_by, name, schedule, status, capital, hedge, trade_date, signal_date, contract, positions, note) "
           "values (?, ?, ?, 'current', ?, ?, ?, ?, ?, ?, ?)")
    execute(sql, (user, p.get("name"), p.get("schedule"), float(p["capital"]), float(p["hedge"]), p.get("trade_date"),
                  p.get("signal_date"), p.get("contract"), _json_in(p["positions"]), p.get("note")))
    return current_portfolio()
