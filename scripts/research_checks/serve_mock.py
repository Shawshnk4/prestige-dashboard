"""Local preview with FYERS replaced by the prices saved in the research workspace (26-Nov ~13:10 IST).
Research workspace only. Usage: DATABASE_URL=sqlite:///... AUTH_DISABLED=1 python scripts/research_checks/serve_mock.py [port]"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
import pandas as pd
from prestige import fyers as FY

Q = pd.concat([pd.read_csv(f"/home/claude/live/fyers_quotes_{i}.csv") for i in (1, 2)]).set_index("symbol")
FUT = pd.read_csv("/home/claude/live/fyers_dec_fut.csv").set_index("symbol")


def fake_quotes(fsyms):
    out = {}
    for f in fsyms:
        s = f.split(":", 1)[1]
        if s.endswith("-EQ") and s[:-3] in Q.index:
            r = Q.loc[s[:-3]]; out[f] = dict(lp=float(r.lp), prev_close=float(r.prev_close), chp=100 * (r.lp / r.prev_close - 1), time=None)
        elif s.endswith("26DECFUT") and s[:-8] in FUT.index:
            r = FUT.loc[s[:-8]]; out[f] = dict(lp=float(r.fut_ltp), prev_close=float(r.fut_prev_close), chp=100 * (r.fut_ltp / r.fut_prev_close - 1), time=None)
        elif s == "NIFTY50-INDEX":
            out[f] = dict(lp=25861.4, prev_close=25819.25, chp=0.16, time=None)
    return out


FIX = pd.read_csv(os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                               "tests/fixtures/fyers_closes_2026-10-23_to_11-25.csv"), parse_dates=["date"])


def fake_history(fsym, start, end):
    """futures: the stock's cash closes x today's futures/cash ratio (test data only)"""
    s = fsym.split(":", 1)[1]
    if s.endswith("FUT"):
        sym = s[:-8]
        ratio = float(FUT.loc[sym, "fut_ltp"] / Q.loc[sym, "lp"]) if sym in FUT.index and sym in Q.index else 1.003
        g = FIX[(FIX.symbol == sym) & (FIX.date >= pd.Timestamp(start)) & (FIX.date <= pd.Timestamp(end))]
        return pd.DataFrame({"date": g.date.values, "close": (g.close * ratio).round(2).values})
    return pd.DataFrame(columns=["date", "close"])


FY.quotes = fake_quotes
FY.history = fake_history
FY.configured = lambda: True
FY.settings_problems = lambda: []
import app.main as M   # noqa: E402
M.FY.quotes = fake_quotes
import uvicorn  # noqa: E402
uvicorn.run(M.app, host="127.0.0.1", port=int(sys.argv[1]) if len(sys.argv) > 1 else 8077, log_level="warning")
