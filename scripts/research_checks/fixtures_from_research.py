"""Build the FYERS long table the website would hold, from the research workspace's FYERS pull (23-Oct..25-Nov)."""
import pandas as pd
def fyers_long(closes="/home/claude/live/fyers_closes.csv", index="/home/claude/live/fyers_index.csv"):
    out = []
    for p in (closes, index):
        F = pd.read_csv(p).set_index("symbol")
        L = F.stack().rename("close").reset_index(); L.columns = ["symbol", "date", "close"]
        out.append(L)
    L = pd.concat(out); L["date"] = pd.to_datetime(L["date"])
    return L[L.symbol.isin(set(pd.read_csv(closes).symbol) | {"NIFTY50", "NIFTY100", "INDIAVIX"})]
