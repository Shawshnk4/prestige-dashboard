"""Check (research workspace only): the production engine, fed the FYERS closes the research used, reproduces
the research data panels, factor weights, the 189 historical monthly books, the official 20-Nov book, the October book
and the 25-Nov preview exactly."""
import pickle, sys, time
import numpy as np, pandas as pd
sys.path.insert(0, "/home/claude/prestige-dashboard"); sys.path.insert(0, "/home/claude/prestige-dashboard/scripts/research_checks")
from prestige import data_store as DS, model as MD
from fixtures_from_research import fyers_long

t0 = time.time()
data, extras, warn = DS.assemble(fyers_long(), history_from=None)
print(f"assembled in {time.time() - t0:.1f}s | warnings: {warn} | jumps flagged: {len(extras['jumps'])}")
R = pickle.load(open("/home/claude/data/data_v2.pkl", "rb"))
eq = lambda a, b: a.shape == b.shape and np.allclose(a.astype(float).fillna(-999).values, b.astype(float).fillna(-999).values, rtol=0, atol=1e-9)
print("close  =", eq(data["close"], R["close"]), "| member =", eq(data["member"], R["member"]),
      "| tri =", eq(data["tri"][["tri", "close"]], R["tri"][["tri", "close"]]), "| vix =", np.allclose(data["vix"].fillna(-1).values, R["vix"].fillna(-1).values))
m50 = pd.read_csv("/home/claude/n50data/membership_daily.csv", parse_dates=["date"]).set_index("date").astype(bool)
t50 = pd.read_csv("/home/claude/n50data/nifty50_tri_daily.csv", parse_dates=["date"]).set_index("date")["nifty50_tri"]
print("member50 =", eq(extras["member50"][m50.columns], m50), "| tri50 =", np.allclose(extras["tri50"].values, t50.values))

t1 = time.time()
M = MD.Model(data, extras, start=None)
print(f"model built in {time.time() - t1:.1f}s")
X = pickle.load(open("/home/claude/data/v3_daily.pkl", "rb"))
for k in ("MOM", "IR1"):
    print(k, "factor =", eq(M.X[k], X[k]))
L = pickle.load(open("/home/claude/live/prestige_live.pkl", "rb"))
Wh = L["W_hist"]; common = Wh.index.intersection(M.W.index)
print("monthly weights equal on", len(common), "of", len(Wh), "research dates:", np.allclose(Wh.loc[common].values, M.W.loc[common].values, atol=1e-12),
      "| missing:", [d.date() for d in Wh.index.difference(M.W.index)])
OLD = pickle.load(open("/home/claude/snapshot_2026-10-23/old/sl/sector_n50.pkl", "rb"))["books"]["V1"]
ok = 0; bad = []
for d, (Lg, Sh) in OLD.items():
    if d > pd.Timestamp("2026-09-25"): continue
    t, _ = M.book(d)
    if set(t.index[t.side == "LONG"]) == set(Lg) and set(t.index[t.side == "SHORT"]) == set(Sh): ok += 1
    else: bad.append(d.date())
print(f"historical books identical: {ok}/{ok + len(bad)}", bad[:5])
for nm, sig, ref in (("official 20-Nov", L["sig_nov"], L["OFF"]), ("October", L["sig_oct"], L["OCT"])):
    t, W = M.book(sig)
    cols = ["MOM", "IR1", "SECTOR", "IR1F", "composite", "final", "weight", "vol63"]
    same = list(t.index) == list(ref.index) and (t.side == ref.side).all() and np.allclose(t[cols].astype(float).fillna(-9).values, ref[cols].astype(float).fillna(-9).values, atol=1e-10)
    print(nm, "book identical (order, sides, factor values, weights):", same, "| weights", W.round(3).to_dict())
t, W = M.book(M.END, M.weights_for(L["sig_nov"])[0])
ref = L["PRE"]
print("preview 25-Nov identical:", list(t.index) == list(ref.index) and (t.side == ref.side).all() and np.allclose(t.final.values, ref.final.values, atol=1e-10))
for sch in ("monthly", "biweekly", "weekly"):
    v = M.schedule_view(sch)
    print(sch, "| held from", v["held"]["rebalance"].date(), v["held"]["kind"], "| next", v["next"]["rebalance"].date(), "signal", v["next"]["signal"].date(),
          "| next ready:", v["next_book"] is not None, "| held contract", v["held_contract"].date(), "| next contract", v["next_contract"].date())
print("expiries ahead:", [d.date() for d in M.E[M.E > M.END]])
M2 = MD.Model(data, extras)          # production default: factors from 2019
common = M2.W.index[M2.W.index >= "2023-01-01"]
print("production start=2019: weights equal since 2023:", np.allclose(M2.W.loc[common].values, M.W.loc[common].values, atol=1e-12))
for sig in (L["sig_nov"], L["sig_oct"], M.END):
    a, _ = M.book(sig); b, _ = M2.book(sig)
    print("  book", sig.date(), "identical:", list(a.index) == list(b.index) and np.allclose(a.final.values, b.final.values, atol=1e-12))
print(f"total {time.time() - t0:.1f}s")
