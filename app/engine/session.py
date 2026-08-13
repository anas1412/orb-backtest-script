"""
Session and opening-range computation.

Everything here operates in UTC. `session_open_utc` is expressed as an
"HH:MM" string (e.g. "00:00") and applied to every day present in the
dataset (minus skipped weekend days).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import time as dt_time, timedelta

import pandas as pd


@dataclass
class SessionRange:
    day: pd.Timestamp              # calendar day (UTC date, tz-naive date marker)
    session_open: pd.Timestamp     # UTC timestamp of session open
    range_end: pd.Timestamp        # UTC timestamp when the opening-range window closes
    search_deadline: pd.Timestamp  # UTC timestamp after which we stop looking for a breakout
    range_high: float | None
    range_low: float | None
    range_mid: float | None
    range_size: float | None
    bars_in_range: int


def parse_hhmm(hhmm: str) -> dt_time:
    hh, mm = hhmm.strip().split(":")
    return dt_time(hour=int(hh), minute=int(mm))


def get_trading_days(df: pd.DataFrame, skip_weekends: bool = True) -> list[pd.Timestamp]:
    """Return sorted unique UTC calendar dates present in the data, weekends optionally excluded."""
    dates = pd.Series(df["timestamp"].dt.normalize().unique()).sort_values()
    if skip_weekends:
        dates = dates[dates.dt.dayofweek < 5]  # Mon=0 ... Fri=4
    return list(dates)


def compute_session_range(
    df: pd.DataFrame,
    day: pd.Timestamp,
    session_open_utc: str = "00:00",
    range_minutes: int = 15,
    breakout_search_minutes: int = 60,
) -> SessionRange:
    """
    For a given UTC calendar day, compute the opening-range high/low from
    the first `range_minutes` minutes after `session_open_utc`, and the
    deadline (in UTC) after which breakout search stops.
    """
    t = parse_hhmm(session_open_utc)
    session_open = pd.Timestamp(
        year=day.year, month=day.month, day=day.day,
        hour=t.hour, minute=t.minute, tz="UTC",
    )
    range_end = session_open + timedelta(minutes=range_minutes)
    search_deadline = session_open + timedelta(minutes=breakout_search_minutes)

    mask = (df["timestamp"] >= session_open) & (df["timestamp"] < range_end)
    window = df.loc[mask]

    if len(window) == 0:
        return SessionRange(
            day=day, session_open=session_open, range_end=range_end,
            search_deadline=search_deadline,
            range_high=None, range_low=None, range_mid=None, range_size=None,
            bars_in_range=0,
        )

    range_high = float(window["high"].max())
    range_low = float(window["low"].min())
    range_mid = (range_high + range_low) / 2.0
    range_size = range_high - range_low

    return SessionRange(
        day=day, session_open=session_open, range_end=range_end,
        search_deadline=search_deadline,
        range_high=range_high, range_low=range_low,
        range_mid=range_mid, range_size=range_size,
        bars_in_range=len(window),
    )
