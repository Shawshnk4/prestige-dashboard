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


FY.quotes = fake_quotes
import app.main as M   # noqa: E402
M.FY.quotes = fake_quotes
import uvicorn  # noqa: E402
uvicorn.run(M.app, host="127.0.0.1", port=int(sys.argv[1]) if len(sys.argv) > 1 else 8077, log_level="warning")
