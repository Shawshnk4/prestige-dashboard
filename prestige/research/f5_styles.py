"""Factor (style) universe from the user's classification file -> point-in-time style panel and
equal-weight factor indices, built exactly like the equal-weight sector indices.

Classification year Y applies to every trading day of calendar year Y (same convention as the yearly
Nifty100 member lists). 2026 uses the 2025 classification (latest available). A second panel lags the
classification by one year (year Y uses Y-1) to test for look-ahead in the classification itself."""
import pickle, re
import numpy as np, pandas as pd
from openpyxl import load_workbook
import v2_core as C

SRC = C.D + "Stocks_with_their_factors.xlsx"          # your factor classification file
STYLES = ["Quality Defensive", "Growth Defensive", "Value Core", "Value Cyclical", "Growth Cyclical", "Growth Speculative"]
SIZES = ["LARGE CAP", "LARGE/MID CAP", "MID CAP"]
ALIAS = {"TMPV": "TATAMOTORS", "LTM": "LTIM"}     # audit 28-Oct-2026: symbols renamed in 2026


def parse(path=SRC):
    wb = load_workbook(path, data_only=True)
    rows = list(wb["Factors"].iter_rows(values_only=True))
    recs, i = [], 0
    while i < len(rows):
        if rows[i][1] == "STOCKS":
            yr, st, fa, ind, sz = rows[i][0], rows[i], rows[i + 1], rows[i + 2], rows[i + 3]
            for j in range(2, len(st)):
                if st[j] is not None:
                    recs.append(dict(year=int(yr), symbol=str(st[j]).strip(), style=str(fa[j]).strip(),
                                     industry=str(ind[j]).strip().upper(), size=str(sz[j]).strip().upper()))
            i += 4
        else:
            i += 1
    cls = pd.DataFrame(recs).drop_duplicates(["year", "symbol"])   # TATAMOTORS listed twice in 2022 (identical rows)
    # macro sheets (quarterly); values usable only after the quarter ends + publication lag (applied later)
    def q(ws, cols):
        r = list(ws.iter_rows(values_only=True))
        df = pd.DataFrame(r[1:], columns=r[0])
        df = df[df.iloc[:, 0].notna()]
        df["quarter"] = pd.PeriodIndex(df.iloc[:, 0].str.replace("-", ""), freq="Q")
        for c in cols:
            df[c] = pd.to_numeric(df[c], errors="coerce")
        return df
    yc = q(wb["10Y-2Y Yield curve"], ["10Y Yield (%)", "2Y Yield (%)", "10Y–2Y Spread (%)"])
    cpi = q(wb["CPI"], ["CPI YoY (%)"])
    cpi.loc[cpi["Actual / Estimate"].astype(str).str.lower() != "actual", "CPI YoY (%)"] = np.nan   # estimates not used
    return cls, yc, cpi


def panels(ctx, cls, lag_years=0):
    """Daily symbol->label panels for style and size (object dtype), NaN when unclassified / not a member."""
    years = ctx.dates.year
    st = pd.DataFrame(index=ctx.dates, columns=ctx.cols, dtype=object)
    sz = st.copy()
    last = cls.year.max()
    for y in np.unique(years):
        src_y = min(y - lag_years, last)
        if lag_years == 0 and y < cls.year.min():
            src_y = cls.year.min()          # 2010 warm-up uses the 2011 list, same convention as the member lists
        c = cls[cls.year == src_y].set_index("symbol")
        rows = years == y
        for s in c.index.intersection(ctx.cols):
            st.loc[rows, s] = c.at[s, "style"]
            sz.loc[rows, s] = c.at[s, "size"]
        for new, old in ALIAS.items():          # renamed symbols keep the label of their old symbol
            if new in ctx.cols and new not in c.index and old in c.index:
                st.loc[rows, new] = c.at[old, "style"]
                sz.loc[rows, new] = c.at[old, "size"]
    m = ctx.member
    return st.where(m), sz.where(m)


def ew_index(ctx, lab_panel, labels):
    r = ctx.ret.where(ctx.member)
    out, cnt = {}, {}
    for L in labels:
        mk = lab_panel == L
        out[L] = r.where(mk).mean(axis=1)
        cnt[L] = r.where(mk).notna().sum(axis=1)
    return pd.DataFrame(out), pd.DataFrame(cnt)


if __name__ == "__main__":
    ctx = C.Ctx()
    cls, yc, cpi = parse()
    out = dict(cls=cls, yc=yc, cpi=cpi)
    for lag in (0, 1):
        st, sz = panels(ctx, cls, lag)
        sr, sc = ew_index(ctx, st, STYLES)
        zr, zc = ew_index(ctx, sz, SIZES)
        out[f"style_panel_lag{lag}"], out[f"size_panel_lag{lag}"] = st, sz
        out[f"style_ret_lag{lag}"], out[f"style_cnt_lag{lag}"] = sr, sc
        out[f"size_ret_lag{lag}"], out[f"size_cnt_lag{lag}"] = zr, zc
    out["ew_universe"] = ctx.ret.where(ctx.member).mean(axis=1)
    pickle.dump(out, open(C.D + "f5_styles.pkl", "wb"))
    sc = out["style_cnt_lag0"]
    print(sc.groupby(sc.index.year).mean().round(1))
    print("classified share of members:", float((out["style_panel_lag0"].notna().sum(axis=1) / ctx.member.sum(axis=1)).loc["2011":].mean()))
    print("lag1 classified share:", float((out["style_panel_lag1"].notna().sum(axis=1) / ctx.member.sum(axis=1)).loc["2012":].mean()))
