"""Trading calendar, monthly expiries and rebalance schedules.

Expiries: the user's expiry file up to Oct-2025 (already moved to the previous trading day on holidays), then the last
Tuesday of each month, moved to the previous trading day when it is a holiday. Past dates use the actual trading
calendar (the dates in the price data); future dates use weekdays minus data/ref/holidays.csv.

Schedules, all anchored on the monthly deployment day E-1 (one trading day before the monthly expiry):
  monthly   E-1 only
  biweekly  E-1, E-1 + 10 sessions, then the next E-1
  weekly    E-1, E-1 + 5, + 10, + 15 sessions, then the next E-1
An in-cycle date that would fall on or after the next E-1 is skipped. Every rebalance trades at the close; its signal is
the close one session earlier."""
from __future__ import annotations
import json
from dataclasses import dataclass
from pathlib import Path
import pandas as pd

REF = Path(__file__).resolve().parent.parent / "data" / "ref"
SCHEDULES = {"monthly": [], "biweekly": [10], "weekly": [5, 10, 15]}
MONTHS = ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"]


def load_holidays() -> set:
    h = pd.read_csv(REF / "holidays.csv", parse_dates=["date"])
    return set(h["date"].dt.normalize())


def load_special_sessions() -> set:
    try:
        return {pd.Timestamp(x) for x in json.load(open(REF / "special_sessions.json"))["weekend_sessions"]}
    except FileNotFoundError:
        return set()


@dataclass
class TradingCalendar:
    past: pd.DatetimeIndex            # sessions in the data (ends at the latest close)
    holidays: set

    def __post_init__(self):
        self.end = self.past[-1]
        fut = pd.bdate_range(self.end + pd.Timedelta(days=1), self.end + pd.Timedelta(days=500))
        fut = fut[~fut.isin(list(self.holidays))]
        self.all = self.past.append(fut)
        self.pos = pd.Series(range(len(self.all)), index=self.all)

    def on_or_before(self, d):
        d = pd.Timestamp(d)
        return self.all[self.all <= d][-1]

    def shift(self, d, k):
        """session k sessions after (k > 0) or before (k < 0) the session d"""
        return self.all[self.pos.loc[pd.Timestamp(d)] + k]

    def next_session(self, d):
        return self.shift(d, 1)

    def prev_session(self, d):
        return self.shift(d, -1)

    def sessions_between(self, a, b):
        return int(self.pos.loc[pd.Timestamp(b)] - self.pos.loc[pd.Timestamp(a)])


def last_weekday(per: pd.Period, wd: int):
    days = pd.date_range(per.start_time, per.end_time.normalize(), freq="D")
    return days[days.weekday == wd][-1]


def expiries(tc: TradingCalendar, user_until="2025-10", months_ahead=3) -> pd.DatetimeIndex:
    """monthly F&O expiries from the first user-file expiry up to `months_ahead` months after the latest close"""
    U = pd.to_datetime(pd.read_csv(REF / "expiries_history.csv")["expiry"])
    cut = pd.Period(user_until, "M") + 1
    first = tc.all[2]                                   # history may start later than the expiry file
    out = [tc.on_or_before(e) for e in U if pd.Period(e, "M") < cut and e >= first]
    for per in pd.period_range(cut, pd.Period(tc.end, "M") + months_ahead, freq="M"):
        out.append(tc.on_or_before(last_weekday(per, 1)))
    return pd.DatetimeIndex(sorted(set(out)))


def rebalance_dates(tc: TradingCalendar, E: pd.DatetimeIndex, schedule: str) -> pd.DataFrame:
    """all rebalance days of a schedule with their signal day, cycle start (monthly E-1) and kind"""
    RM = pd.DatetimeIndex([tc.prev_session(e) for e in E])
    rows = []
    for a, b in zip(RM, list(RM[1:]) + [None]):
        rows.append((a, a, "monthly", 0))
        for s in SCHEDULES[schedule]:
            r = tc.shift(a, s)
            if b is not None and r >= b:
                continue
            if b is None and r > tc.all[-1]:
                continue
            rows.append((r, a, "in-cycle", s))
    df = pd.DataFrame(rows, columns=["rebalance", "cycle_start", "kind", "offset"])
    df["signal"] = [tc.prev_session(r) for r in df.rebalance]
    return df.sort_values("rebalance").reset_index(drop=True)


def contract_for(tc: TradingCalendar, E: pd.DatetimeIndex, trade_day) -> pd.Timestamp:
    """futures contract to hold after trading at the close of trade_day: the first monthly expiry after the next session"""
    nxt = tc.next_session(trade_day)
    return E[E > nxt][0]


def fut_symbol(symbol: str, expiry) -> str:
    e = pd.Timestamp(expiry)
    return f"NSE:{symbol}{e.strftime('%y')}{MONTHS[e.month - 1]}FUT"


def eq_symbol(symbol: str) -> str:
    return f"NSE:{symbol}-EQ"


INDEX_SYMBOLS = {"NIFTY50": "NSE:NIFTY50-INDEX", "NIFTY100": "NSE:NIFTY100-INDEX", "INDIAVIX": "NSE:INDIAVIX-INDEX"}
