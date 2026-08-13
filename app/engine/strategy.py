"""
Strategy logic: breakout detection, entry (market/limit), SL/TP calculation.

All rules here follow plan.md exactly:
- Breakout = M1 candle CLOSE beyond range_high/range_low, first one wins.
- Search window: from range_end until search_deadline (range_end + breakout_search_minutes).
- Entry:
    market -> fills at the OPEN of the candle immediately following the breakout candle.
    limit  -> resting order at the broken boundary, fills if price returns to it before
              search_deadline; otherwise expires unfilled (no trade).
- SL is anchored at the range midpoint, offset by sl_pct_of_range * range_size back
  toward the entry side (0.5 = exactly at midpoint, 1.0 = opposite boundary).
- TP = entry +/- (tp_rr * risk_distance), where risk_distance = |entry - SL|.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import pandas as pd

from .session import SessionRange

Direction = Literal["long", "short"]
EntryMode = Literal["market", "limit"]


@dataclass
class Params:
    session_open_utc: str = "00:00"
    range_minutes: int = 15
    breakout_search_minutes: int = 44
    entry_mode: EntryMode = "market"
    sl_pct_of_range: float = 0.5
    tp_rr: float = 2.0
    spread_pips: float = 0.0
    slippage_pips: float = 0.0
    session_max_hours: float = 20.0
    skip_weekends: bool = True
    # Stop-management: a ladder of (trigger_R, sl_R_from_entry) steps. R is the
    # ORIGINAL risk distance (entry to initial SL), fixed for the whole trade:
    # a moved stop never re-anchors later steps. When price reaches
    # trigger_R, the SL moves to sl_R_from_entry (negative = still below entry,
    # 0.0 = breakeven, positive = locked profit). Steps apply in order, each
    # move takes effect from the next candle on. Empty tuple = no moves.
    sl_ladder: tuple[tuple[float, float], ...] = ((0.5, -0.5),)
    pip_size: float = 0.01  # gold: 1 pip = $0.01 by common retail convention


@dataclass
class BreakoutSignal:
    direction: Direction
    breakout_bar_time: pd.Timestamp
    breakout_bar_close: float


@dataclass
class TradeSetup:
    direction: Direction
    entry_price: float
    entry_time: pd.Timestamp
    sl_price: float
    tp_price: float
    risk_distance: float
    entry_mode: EntryMode


def find_breakout(df: pd.DataFrame, srange: SessionRange) -> BreakoutSignal | None:
    """Scan M1 candles after the range window for the first close beyond range bounds."""
    if srange.range_high is None:
        return None

    mask = (df["timestamp"] >= srange.range_end) & (df["timestamp"] < srange.search_deadline)
    window = df.loc[mask].sort_values("timestamp")

    for _, bar in window.iterrows():
        if bar["close"] > srange.range_high:
            return BreakoutSignal("long", bar["timestamp"], float(bar["close"]))
        if bar["close"] < srange.range_low:
            return BreakoutSignal("short", bar["timestamp"], float(bar["close"]))
    return None


def _apply_costs(price: float, direction: Direction, is_entry: bool, params: Params) -> float:
    """Apply spread/slippage in the adverse direction for the trader (default 0)."""
    cost = (params.spread_pips + params.slippage_pips) * params.pip_size
    if cost == 0:
        return price
    if is_entry:
        return price + cost if direction == "long" else price - cost
    else:
        # Exit costs also adverse: selling a long exits lower, covering a short exits higher.
        return price - cost if direction == "long" else price + cost


def compute_sl_tp(entry_price: float, direction: Direction, srange: SessionRange, params: Params):
    """
    SL anchored at range midpoint, adjustable via sl_pct_of_range:
      sl_pct_of_range = 0.5 -> SL at exact midpoint
      sl_pct_of_range = 1.0 -> SL at the opposite range boundary
      sl_pct_of_range = 0.0 -> SL at the broken boundary itself (no room)
    Implemented as an offset from the midpoint toward the broken boundary,
    scaled by (sl_pct_of_range - 0.5) * range_size, so 0.5 always lands
    exactly on the midpoint regardless of entry price.
    """
    mid = srange.range_mid
    size = srange.range_size
    # offset grows from 0 (at pct=0.5, SL sits exactly on the midpoint) up to
    # size/2 (at pct=1.0, SL sits on the boundary opposite the breakout side).
    offset = (params.sl_pct_of_range - 0.5) * size

    if direction == "long":
        # Breakout was upward through range_high, so the SL sits BELOW the
        # midpoint, moving further down (toward/through range_low) as
        # sl_pct_of_range increases.
        sl_price = mid - offset
        risk_distance = entry_price - sl_price
    else:
        # Breakout was downward through range_low, so the SL sits ABOVE the
        # midpoint, moving further up (toward/through range_high) as
        # sl_pct_of_range increases.
        sl_price = mid + offset
        risk_distance = sl_price - entry_price

    tp_price = (
        entry_price + params.tp_rr * risk_distance
        if direction == "long"
        else entry_price - params.tp_rr * risk_distance
    )

    return sl_price, tp_price, risk_distance


def build_trade_setup(
    df: pd.DataFrame,
    srange: SessionRange,
    signal: BreakoutSignal,
    params: Params,
) -> TradeSetup | None:
    direction = signal.direction

    if params.entry_mode == "market":
        # Fill at the open of the next bar after the breakout candle.
        after = df.loc[df["timestamp"] > signal.breakout_bar_time].sort_values("timestamp")
        if len(after) == 0:
            return None
        next_bar = after.iloc[0]
        if next_bar["timestamp"] >= srange.search_deadline + pd.Timedelta(minutes=1):
            # Allow the fill bar itself even if it's the deadline bar; only bail
            # if we've run far past it (defensive, shouldn't normally trigger).
            pass
        raw_entry = float(next_bar["open"])
        entry_time = next_bar["timestamp"]

    elif params.entry_mode == "limit":
        # Resting limit at the broken boundary; must be touched before search_deadline.
        boundary = srange.range_high if direction == "long" else srange.range_low
        after = df.loc[
            (df["timestamp"] > signal.breakout_bar_time)
            & (df["timestamp"] < srange.search_deadline)
        ].sort_values("timestamp")

        fill_time = None
        for _, bar in after.iterrows():
            touched = (bar["low"] <= boundary <= bar["high"])
            if touched:
                fill_time = bar["timestamp"]
                break
        if fill_time is None:
            return None  # limit expired unfilled
        raw_entry = float(boundary)
        entry_time = fill_time
    else:
        raise ValueError(f"Unknown entry_mode: {params.entry_mode}")

    entry_price = _apply_costs(raw_entry, direction, is_entry=True, params=params)
    sl_price, tp_price, risk_distance = compute_sl_tp(entry_price, direction, srange, params)

    if risk_distance <= 0:
        return None  # degenerate setup (e.g. sl_pct_of_range = 0 with tight range)

    return TradeSetup(
        direction=direction,
        entry_price=entry_price,
        entry_time=entry_time,
        sl_price=sl_price,
        tp_price=tp_price,
        risk_distance=risk_distance,
        entry_mode=params.entry_mode,
    )
