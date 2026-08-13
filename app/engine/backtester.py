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


def _simulate_exit(df: pd.DataFrame, setup, params: Params) -> tuple[float, pd.Timestamp, ExitReason]:
    """Walk forward candle-by-candle from entry, checking SL/TP touches, then timeout."""
    deadline = setup.entry_time + timedelta(hours=params.session_max_hours)
    future = df.loc[
        (df["timestamp"] > setup.entry_time) & (df["timestamp"] <= deadline)
    ].sort_values("timestamp")

    for _, bar in future.iterrows():
        hi, lo = float(bar["high"]), float(bar["low"])
        if setup.direction == "long":
            hit_sl = lo <= setup.sl_price
            hit_tp = hi >= setup.tp_price
        else:
            hit_sl = hi >= setup.sl_price
            hit_tp = lo <= setup.tp_price

        # If both SL and TP could be touched in the same candle, assume the
        # worse outcome for the trader (SL first) -- a conservative, standard
        # backtesting convention absent tick-level data.
        if hit_sl and hit_tp:
            exit_price = _apply_costs(setup.sl_price, setup.direction, is_entry=False, params=params)
            return exit_price, bar["timestamp"], "sl"
        if hit_sl:
            exit_price = _apply_costs(setup.sl_price, setup.direction, is_entry=False, params=params)
            return exit_price, bar["timestamp"], "sl"
        if hit_tp:
            exit_price = _apply_costs(setup.tp_price, setup.direction, is_entry=False, params=params)
            return exit_price, bar["timestamp"], "tp"

    # Timeout: force-close at the last available price at/after the deadline,
    # or the last bar in the dataset if we ran out of data first.
    if len(future) > 0:
        last_bar = future.iloc[-1]
        exit_price = _apply_costs(float(last_bar["close"]), setup.direction, is_entry=False, params=params)
        return exit_price, last_bar["timestamp"], "timeout"

    # No future bars at all (end of dataset right at entry) -- exit at entry price, 0R.
    return setup.entry_price, setup.entry_time, "timeout"


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

        exit_price, exit_time, exit_reason = _simulate_exit(df, setup, params)

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
            exit_price=exit_price,
            exit_time=exit_time,
            exit_reason=exit_reason,
            r_multiple=r_multiple,
        ))

    return result
