"""Offline check that this deployment reproduces the verified PRESTIGE book.
Feeds the FYERS daily closes of 23-Oct..25-Nov-2026 (tests/fixtures) through the production pipeline and compares the
official November book (signal 19-Nov, trade 20-Nov) with the research result. No FYERS login or database needed.
Run: python tests/smoke_test.py"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pandas as pd
from prestige import data_store as DS, model as MD

HERE = os.path.dirname(os.path.abspath(__file__))
LONG = ["ADANIENT", "BAJAJ-AUTO", "AXISBANK", "TMPV", "GRASIM", "TITAN", "SHRIRAMFIN", "HINDALCO", "NESTLEIND", "SUNPHARMA",
        "APOLLOHOSP", "HDFCBANK", "ONGC", "EICHERMOT", "HCLTECH"]
SHORT = ["SBIN", "INDIGO", "RELIANCE", "HINDUNILVR", "BHARTIARTL", "LT", "TCS", "JSWSTEEL", "MARUTI", "DRREDDY", "TATACONSUM",
         "M&M", "BEL", "ULTRACEMCO", "ITC"]
W = {"MOM": 0.125, "IR1": 0.2703, "SECTOR": 0.1727, "IR1F": 0.4320}

F = pd.read_csv(os.path.join(HERE, "fixtures", "fyers_closes_2026-10-23_to_11-25.csv"), parse_dates=["date"])
data, extras, warn = DS.assemble(F)
M = MD.Model(data, extras)
t, w = M.book("2026-11-19")
okL = list(t.index[t.side == "LONG"]) == LONG
okS = sorted(t.index[t.side == "SHORT"]) == sorted(SHORT)
okW = all(abs(float(w[k]) - v) < 5e-4 for k, v in W.items())
print("data to", M.END.date(), "| warnings:", warn or "none")
print("long book (in rank order) matches:", okL, "| short book matches:", okS, "| factor weights match:", okW)
assert okL and okS and okW, "the deployment does not reproduce the verified book"
print("OK")
