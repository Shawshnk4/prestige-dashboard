"""
Nifty100 Technical + IR Long/Short model  —  configuration.

All tunable numbers live here. Values marked [GRID] are searched by research.py;
the defaults below are the user's discretionary starting point and are
overwritten by the research output (params_selected.json) when it exists.
"""
from dataclasses import dataclass, field, asdict
import json, os

# --------------------------------------------------------------------------
# Sector map — equal-weight sector indices are built from these buckets.
# NSE-industry based, merged so every bucket (except Telecom) has >= 3 names.
# Sectors with < MIN_SECTOR_SIZE members fall back to the EW universe for IR1.
# --------------------------------------------------------------------------
SECTORS = {
    "PVT_BANKS":   ["HDFCBANK", "ICICIBANK", "KOTAKBANK", "AXISBANK"],
    "PSU_FIN":     ["SBIN", "BANKBARODA", "PNB", "CANBK", "UNIONBANK", "PFC", "IRFC"],
    "NBFC":        ["BAJFINANCE", "BAJAJFINSV", "BAJAJHLDNG", "SHRIRAMFIN", "CHOLAFIN",
                    "MUTHOOTFIN", "JIOFIN", "TATACAP"],
    "CAPMKT_INS":  ["BSE", "HDFCAMC", "HDFCLIFE", "SBILIFE"],
    "IT":          ["INFY", "TCS", "HCLTECH", "WIPRO", "TECHM", "LTM"],
    "AUTO":        ["M&M", "MARUTI", "TMPV", "TMCV", "BAJAJ-AUTO", "EICHERMOT", "TVSMOTOR",
                    "HYUNDAI", "MOTHERSON", "BOSCHLTD"],
    "PHARMA_HC":   ["SUNPHARMA", "DRREDDY", "CIPLA", "DIVISLAB", "TORNTPHARM", "ZYDUSLIFE",
                    "APOLLOHOSP", "MAXHEALTH"],
    "FMCG":        ["HINDUNILVR", "ITC", "NESTLEIND", "BRITANNIA", "TATACONSUM", "GODREJCP", "VBL"],
    "CONS_DISC":   ["TITAN", "TRENT", "DMART", "ETERNAL", "ASIANPAINT", "PIDILITIND", "INDIGO"],
    "METALS_MIN":  ["TATASTEEL", "JSWSTEEL", "HINDALCO", "JINDALSTEL", "VEDL", "HINDZINC",
                    "VAML", "COALINDIA"],
    "OIL_GAS":     ["RELIANCE", "ONGC", "BPCL", "IOC", "GAIL"],
    "POWER":       ["NTPC", "POWERGRID", "TATAPOWER", "ADANIPOWER", "ADANIGREEN", "ADANIENSOL"],
    "CAP_GOODS":   ["LT", "ABB", "SIEMENS", "CGPOWER", "POWERINDIA", "ENRIN", "CUMMINSIND", "POLYCAB"],
    "DEFENCE":     ["BEL", "HAL", "MAZDOCK", "SOLARINDS"],
    "INFRA_MAT":   ["ULTRACEMCO", "AMBUJACEM", "GRASIM", "ADANIPORTS", "ADANIENT", "DLF"],
    "TELECOM":     ["BHARTIARTL", "IDEA"],
}
SYMBOL_TO_SECTOR = {s: sec for sec, names in SECTORS.items() for s in names}
MIN_SECTOR_SIZE = 3
INDEX_SYMBOL = "NIFTY100"


@dataclass
class MomentumParams:
    rsi_len: int = 14          # [GRID] Wilder RSI length
    smooth_len: int = 9        # [GRID] smoothing of RSI
    smooth_type: str = "EMA"   # [GRID] EMA | SMA
    bb_len: int = 20           # [GRID] BB length on smoothed RSI
    bb_k: float = 1.5          # [GRID] BB width (std multiples)
    slope_bars: int = 3        # bars used to define rising / falling
    cross_bars: int = 3        # a mid-band cross is "fresh" for this many bars
    reject_bars: int = 5       # look-back for an upper/lower band touch (daily MR flag)
    reject_drop: float = 0.15  # %B retreat from the touch that confirms rejection


@dataclass
class ModelConfig:
    # timing
    rebalance: str = "M"               # month-end formation
    entry_lag_days: int = 1            # deploy next session (first week of month)
    fwd_days: int = 21                 # 1-month holding (used for research targets)
    # IR lookbacks (trading days) — blend of 2M and 3M  [GRID: blend weight]
    lb_2m: int = 42
    lb_3m: int = 63
    ir1_w2m: float = 0.5               # weight on 2M IR for stock-vs-sector
    ir2_w2m: float = 0.5               # weight on 2M IR for sector-vs-benchmark
    ir_bench: str = "EW"               # "EW" (equal-weight universe) | "INDEX"
    # momentum
    daily: MomentumParams = field(default_factory=lambda: MomentumParams(slope_bars=3, cross_bars=5, reject_bars=5))
    weekly: MomentumParams = field(default_factory=lambda: MomentumParams(slope_bars=2, cross_bars=3, reject_bars=3))
    w_weekly: float = 0.6              # [GRID] weekly share of momentum score
    z_cap: float = 2.0                 # momentum z-score control (winsorise)
    z_mode: str = "cap"                # "cap" | "fold" (fold = mean-revert beyond cap)
    # sector score
    sector_2nd: str = "breadth_mom"    # [GRID] breadth_mom | breadth_dma | sec_mom
    w_sector_ir2: float = 0.6          # IR2 share in sector score
    # portfolio
    n_long: int = 20
    n_short: int = 20
    cost_bps_per_side: float = 7.5     # futures: brokerage + STT + impact, one way
    # walk-forward weights
    ic_window: int = 36                # months of trailing IC
    ic_min: int = 18
    ic_shrink: float = 0.5             # 0 = pure IC weights, 1 = equal weights
    # research split
    is_end: str = "2021-12-31"
    start: str = "2015-01-01"

    def to_json(self, path):
        with open(path, "w") as f:
            json.dump(asdict(self), f, indent=2)

    @staticmethod
    def from_json(path):
        with open(path) as f:
            d = json.load(f)
        d["daily"] = MomentumParams(**d["daily"])
        d["weekly"] = MomentumParams(**d["weekly"])
        return ModelConfig(**d)


def load_config(path="params_selected.json"):
    return ModelConfig.from_json(path) if os.path.exists(path) else ModelConfig()
