"""
Main backtest loop: iterates trading days, applies strategy, simulates
trade outcomes (SL/TP/timeout), and collects results.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta
from typing import Callable, Literal

import pandas as pd

from .session import compute_session_range, get_trading_days
from .strategy import Params, build_trade_setup, find_breakout, _apply_costs

ExitReason = Literal["tp", "sl", "timeout", "no_breakout", "no_fill"]


@dataclass
class Trade:
    day: pd.Timestamp
    session_open: pd.Timestamp
    range_high: float
    range_low: float
    range_mid: float
    range_size: float
    breakout_time: pd.Timestamp
    direction: str
    entry_mode: str
    entry_price: float
    entry_time: pd.Timestamp
    sl_price: float
    tp_price: float
    risk_distance: float
    sl_moved: bool
    sl_final: float
    exit_price: float
    exit_time: pd.Timestamp
    exit_reason: ExitReason
    r_multiple: float


@dataclass
class NoTradeDay:
    day: pd.Timestamp
    reason: str  # "no_range_data" | "no_breakout" | "no_fill"


@dataclass
class BacktestResult:
    trades: list[Trade] = field(default_factory=list)
    no_trade_days: list[NoTradeDay] = field(default_factory=list)
    params: Params | None = None


def _ladder_levels(setup, params: Params) -> list[tuple[float, float]]:
    """
    Stop-management levels for the stop ladder.

    Returns a sorted list of (trigger_price, sl_price) pairs, one per ladder
    step, or an empty list when the ladder is disabled. The trigger is the
    price meaning the trade has reached `trigger_R` of the ORIGINAL risk
    distance from entry; the moved stop sits at `sl_R_from_entry` risk
    distances from entry (0.0 = breakeven). All levels are computed once from
    entry, so a moved stop never becomes the reference for later steps.
    Mirrors automatically for shorts.
    """
    steps = []
    for trigger_r, sl_r in sorted(params.sl_ladder):
        if setup.direction == "long":
            trigger = setup.entry_price + trigger_r * setup.risk_distance
            moved = setup.entry_price + sl_r * setup.risk_distance
        else:
            trigger = setup.entry_price - trigger_r * setup.risk_distance
            moved = setup.entry_price - sl_r * setup.risk_distance
        steps.append((trigger, moved))
    return steps


def _simulate_exit(df: pd.DataFrame, setup, params: Params) -> tuple[float, pd.Timestamp, ExitReason, bool, float]:
    """Walk forward candle-by-candle from entry, checking SL/TP touches, then timeout.

    When `params.sl_ladder` is non-empty, the stop moves each time price
    reaches a step's trigger. Each moved stop only takes effect from the NEXT
    bar after the trigger bar (conservative: a single bar that touches both
    the trigger and the old stop resolves to the old stop, consistent with
    the same-bar SL/TP convention). Steps advance monotonically, so a bar
    racing across several triggers applies the highest reached step directly.
    """
    deadline = setup.entry_time + timedelta(hours=params.session_max_hours)
    future = df.loc[
        (df["timestamp"] > setup.entry_time) & (df["timestamp"] <= deadline)
    ].sort_values("timestamp")

    ladder = _ladder_levels(setup, params)
    current_sl = setup.sl_price
    sl_moved = False

    for _, bar in future.iterrows():
        hi, lo = float(bar["high"]), float(bar["low"])

        # Evaluate THIS bar against the stop that was in force at its open.
        # A bar that also touches the trigger is checked against the OLD stop:
        # without tick data, hitting the trigger first and then the old stop is
        # ambiguous, so assume the worse outcome (consistent with the
        # same-bar SL/TP convention).
        if setup.direction == "long":
            hit_sl = lo <= current_sl
            hit_tp = hi >= setup.tp_price
        else:
            hit_sl = hi >= current_sl
            hit_tp = lo <= setup.tp_price

        # If both SL and TP could be touched in the same candle, assume the
        # worse outcome for the trader (SL first) -- a conservative, standard
        # backtesting convention absent tick-level data.
        if hit_sl and hit_tp:
            exit_price = _apply_costs(current_sl, setup.direction, is_entry=False, params=params)
            return exit_price, bar["timestamp"], "sl", sl_moved, current_sl
        if hit_sl:
            exit_price = _apply_costs(current_sl, setup.direction, is_entry=False, params=params)
            return exit_price, bar["timestamp"], "sl", sl_moved, current_sl
        if hit_tp:
            exit_price = _apply_costs(setup.tp_price, setup.direction, is_entry=False, params=params)
            return exit_price, bar["timestamp"], "tp", sl_moved, current_sl

        # Stop-management: ladder triggers. The moved stop only takes effect
        # from the NEXT bar. Steps are ordered by trigger, so a bar racing
        # across several triggers advances through all of them and the highest
        # reached step's stop wins (monotonic, so the result is unambiguous).
        while ladder:
            trigger, moved_sl = ladder[0]
            touched = hi >= trigger if setup.direction == "long" else lo <= trigger
            if not touched:
                break
            sl_moved = True
            current_sl = moved_sl
            ladder.pop(0)

    # Timeout: force-close at the last available price at/after the deadline,
    # or the last bar in the dataset if we ran out of data first.
    if len(future) > 0:
        last_bar = future.iloc[-1]
        exit_price = _apply_costs(float(last_bar["close"]), setup.direction, is_entry=False, params=params)
        return exit_price, last_bar["timestamp"], "timeout", sl_moved, current_sl

    # No future bars at all (end of dataset right at entry) -- exit at entry price, 0R.
    return setup.entry_price, setup.entry_time, "timeout", sl_moved, current_sl


def run_backtest(
    df: pd.DataFrame,
    params: Params,
    progress_cb: Callable[[int, int], None] | None = None,
) -> BacktestResult:
    """
    Run the backtest over every trading day.

    progress_cb(done, total) is invoked once per day when provided; total is
    the number of trading days found in the window. Used by the API layer to
    stream live progress to the UI. The engine itself never blocks on it.
    """
    result = BacktestResult(params=params)
    days = get_trading_days(df, skip_weekends=params.skip_weekends)
    total = len(days)

    for i, day in enumerate(days):
        if progress_cb is not None:
            progress_cb(i, total)
        srange = compute_session_range(
            df, day,
            session_open_utc=params.session_open_utc,
            range_minutes=params.range_minutes,
            breakout_search_minutes=params.breakout_search_minutes,
        )
        if srange.range_high is None:
            result.no_trade_days.append(NoTradeDay(day, "no_range_data"))
            continue

        signal = find_breakout(df, srange)
        if signal is None:
            result.no_trade_days.append(NoTradeDay(day, "no_breakout"))
            continue

        setup = build_trade_setup(df, srange, signal, params)
        if setup is None:
            result.no_trade_days.append(NoTradeDay(day, "no_fill"))
            continue

        exit_price, exit_time, exit_reason, sl_moved, sl_final = _simulate_exit(df, setup, params)

        pnl_price = (
            exit_price - setup.entry_price
            if setup.direction == "long"
            else setup.entry_price - exit_price
        )
        r_multiple = pnl_price / setup.risk_distance if setup.risk_distance != 0 else 0.0

        result.trades.append(Trade(
            day=day,
            session_open=srange.session_open,
            range_high=srange.range_high,
            range_low=srange.range_low,
            range_mid=srange.range_mid,
            range_size=srange.range_size,
            breakout_time=signal.breakout_bar_time,
            direction=setup.direction,
            entry_mode=setup.entry_mode,
            entry_price=setup.entry_price,
            entry_time=setup.entry_time,
            sl_price=setup.sl_price,
            tp_price=setup.tp_price,
            risk_distance=setup.risk_distance,
            sl_moved=sl_moved,
            sl_final=sl_final,
            exit_price=exit_price,
            exit_time=exit_time,
            exit_reason=exit_reason,
            r_multiple=r_multiple,
        ))

    return result
