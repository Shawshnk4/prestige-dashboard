"""Export the frozen PRESTIGE base data, parameters and reference tables from the research workspace into the
website repository (data/). Base panels end on the anchor date 23-Oct-2026 (the last vendor-clean close);
everything after that comes from FYERS at run time."""
import contextlib, io, json, os, pickle, sys
import numpy as np, pandas as pd

REPO = "/home/claude/prestige-dashboard/data"
T0 = pd.Timestamp("2026-10-23")
for p in ("/home/claude/n50e1", "/home/claude/inst", "/home/claude/lsmodel"): sys.path.insert(0, p)
_cwd = os.getcwd()
with contextlib.redirect_stdout(io.StringIO()):
    os.chdir("/home/claude/lsmodel")
    import v2_core as C
    import sectors as SECS
    import n50e1_run as B
os.chdir(_cwd)

# ------------------------------------------------------------------ base panels (<= 23-Oct)
d = pickle.load(open("/home/claude/snapshot_2026-10-23/old/data/data_v2.pkl", "rb"))
assert d["close"].index[-1] == T0
d["close"].to_parquet(f"{REPO}/base/close.parquet")
d["member"].astype(bool).to_parquet(f"{REPO}/base/member100.parquet")
d["tri"].to_parquet(f"{REPO}/base/nifty100.parquet")
d["vix"].to_frame("vix").to_parquet(f"{REPO}/base/vix.parquet")
m50 = pd.read_csv("/home/claude/n50data/membership_daily.csv", parse_dates=["date"]).set_index("date").loc[:T0].astype(bool)
m50.to_parquet(f"{REPO}/base/member50.parquet")
t50 = pd.read_csv("/home/claude/n50data/nifty50_tri_daily.csv", parse_dates=["date"]).set_index("date").loc[:T0]
t50[["nifty50_tri"]].to_parquet(f"{REPO}/base/nifty50_tri.parquet")
print("base:", d["close"].shape, "members100 on T0:", int(d["member"].loc[T0].sum()), "| members50:", int(m50.loc[T0].sum()),
      "| N50 TRI", t50.index[0].date(), "->", t50.index[-1].date())

# ------------------------------------------------------------------ frozen parameters and calibrated score tables
P = pickle.load(open("/home/claude/data/v2_pack.pkl", "rb")); pr = P["params"]
tf = lambda p: dict(rsi_len=p.rsi_len, smooth_type=p.smooth_type, smooth_len=p.smooth_len, bb_len=p.bb_len, bb_k=p.bb_k,
                    slope_bars=p.slope_bars, cross_bars=p.cross_bars, reject_bars=p.reject_bars, reject_drop=p.reject_drop)
params = dict(D=tf(pr["D"]), W=tf(pr["W"]), w_weekly=pr["w_weekly"], z_cap=pr["z_cap"], w_ir1_2m=pr["w_ir1_2m"],
              ir1_sign=pr["ir1_sign"], w_ir2_2m=pr["w_ir2_2m"], w_sec_ir2=pr["w_sec_ir2"],
              edgesD=list(map(float, P["edgesD"])), edgesW=list(map(float, P["edgesW"])), is_end="2018-12-31",
              icir=dict(window=36, min_obs=18, shrink=0.5, lag=2, fwd_days=21), ir1f=dict(window=252),
              book=dict(n=15, vol_window=63, vol_min=50), sector_min_live=2,
              note="Frozen on 2011-18 data in the PRESTIGE research; do not edit.")
json.dump(params, open(f"{REPO}/model/params.json", "w"), indent=2)

# score tables: refit exactly as v3_daily does (in-sample <= 2018, unchanged by later data) and store the coefficients
ctx = C.Ctx()
fD, fW = ctx.feats(pr["D"], False), ctx.feats(pr["W"], True)
IS_SMP = ctx.samples[ctx.samples <= ctx.is_end]
tabs = {}
for nm, f, wk, ed in (("D", fD, False, P["edgesD"]), ("W", fW, True, P["edgesW"])):
    t = C.fit_table(ctx, f, wk, IS_SMP, ed)
    tabs[nm] = dict(beta=t.beta.tolist(), beta_par={str(k): v.tolist() for k, v in t.beta_par.items()}, names=list(map(str, t.names)),
                    grp=list(map(str, t.grp)), rsi_edges=list(map(float, t.rsi_edges)), is_end=str(t.is_end.date()), cv=bool(t.cv))
    ref = P["tab" + nm]
    if hasattr(ref, "beta"):
        print(f"table {nm}: equals the research pack:", np.allclose(ref.beta, t.beta) and all(np.allclose(ref.beta_par[k], t.beta_par[k]) for k in (0, 1)))
json.dump(tabs, open(f"{REPO}/model/score_tables.json", "w"))

# ------------------------------------------------------------------ reference tables
S5 = pickle.load(open("/home/claude/data/f5_styles.pkl", "rb"))
S5["cls"].to_csv(f"{REPO}/ref/styles.csv", index=False)
pd.Series(SECS.SYMBOL_TO_SECTOR, name="sector").rename_axis("symbol").to_csv(f"{REPO}/ref/sectors.csv")
E_, fixes = B.expiry_calendar()
uf = E_[E_.source == "user file"]
uf.reset_index()[["expiry"]].assign(expiry=lambda x: x.expiry.dt.date).to_csv(f"{REPO}/ref/expiries_history.csv", index=False)
print("user-file expiries:", len(uf), uf.index[0].date(), "->", uf.index[-1].date(), "| rule-based after:", (E_.source != "user file").sum())
L = pd.read_csv("/home/claude/data/fo_mktlots_2026-10.csv"); L.columns = [c.strip() for c in L.columns]
L["SYMBOL"] = L["SYMBOL"].str.strip()
rows = []
for col, mon in (("OCT-26", "2026-10"), ("NOV-26", "2026-11"), ("DEC-26", "2026-12")):
    for s, v in zip(L.SYMBOL, pd.to_numeric(L[col], errors="coerce")):
        if np.isfinite(v): rows.append((s, mon, int(v)))
pd.DataFrame(rows, columns=["symbol", "contract", "lot_size"]).to_csv(f"{REPO}/ref/lot_sizes.csv", index=False)
# trading holidays on weekdays, from the trading calendar (2024-2026) + known future NSE holidays
cal = ctx.dates
wd = pd.bdate_range("2024-01-01", cal[-1])
hol = sorted(set(wd) - set(cal))
known = [("2026-11-24", "Guru Nanak Jayanti"), ("2026-12-25", "Christmas"), ("2027-01-26", "Republic Day")]
H = pd.DataFrame([(h.date(), "from trading calendar") for h in hol] + [(pd.Timestamp(a).date(), b) for a, b in known], columns=["date", "name"])
H.drop_duplicates("date").sort_values("date").to_csv(f"{REPO}/ref/holidays.csv", index=False)
spec = [str(x.date()) for x in cal[(cal.weekday >= 5) & (cal >= "2024-01-01")]]
json.dump(dict(weekend_sessions=spec), open(f"{REPO}/ref/special_sessions.json", "w"))
print("holidays:", len(H), "| weekend sessions (muhurat etc.) since 2024:", spec)
print("saved")
