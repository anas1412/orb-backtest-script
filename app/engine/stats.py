"""Summary statistics computed from a list of closed trades."""
from __future__ import annotations

from dataclasses import dataclass, asdict

import numpy as np
import pandas as pd

from .backtester import BacktestResult


@dataclass
class Stats:
    total_trades: int
    wins: int
    losses: int
    timeouts: int
    win_rate: float
    total_r: float
    average_r: float
    profit_factor: float | None
    max_drawdown_r: float
    expectancy_r: float
    longest_win_streak: int
    longest_loss_streak: int
    long_trades: int
    short_trades: int
    long_win_rate: float | None
    short_win_rate: float | None


def compute_stats(result: BacktestResult) -> Stats:
    trades = result.trades
    if len(trades) == 0:
        return Stats(
            total_trades=0, wins=0, losses=0, timeouts=0, win_rate=0.0,
            total_r=0.0, average_r=0.0, profit_factor=None, max_drawdown_r=0.0,
            expectancy_r=0.0, longest_win_streak=0, longest_loss_streak=0,
            long_trades=0, short_trades=0, long_win_rate=None, short_win_rate=None,
        )

    r_values = np.array([t.r_multiple for t in trades])
    wins = int((r_values > 0).sum())
    losses = int((r_values < 0).sum())
    timeouts_flat = int((r_values == 0).sum())

    total_r = float(r_values.sum())
    average_r = float(r_values.mean())
    win_rate = wins / len(trades)

    gross_win = float(r_values[r_values > 0].sum())
    gross_loss = float(-r_values[r_values < 0].sum())
    profit_factor = (gross_win / gross_loss) if gross_loss > 0 else None

    equity = np.cumsum(r_values)
    running_max = np.maximum.accumulate(equity)
    drawdown = running_max - equity
    max_dd = float(drawdown.max()) if len(drawdown) else 0.0

    expectancy = average_r  # equivalent definition given uniform 1-unit risk per trade

    # streaks
    longest_win_streak = longest_loss_streak = 0
    cur_win = cur_loss = 0
    for r in r_values:
        if r > 0:
            cur_win += 1
            cur_loss = 0
        elif r < 0:
            cur_loss += 1
            cur_win = 0
        else:
            cur_win = cur_loss = 0
        longest_win_streak = max(longest_win_streak, cur_win)
        longest_loss_streak = max(longest_loss_streak, cur_loss)

    long_trades = [t for t in trades if t.direction == "long"]
    short_trades = [t for t in trades if t.direction == "short"]
    long_win_rate = (
        sum(1 for t in long_trades if t.r_multiple > 0) / len(long_trades)
        if long_trades else None
    )
    short_win_rate = (
        sum(1 for t in short_trades if t.r_multiple > 0) / len(short_trades)
        if short_trades else None
    )

    return Stats(
        total_trades=len(trades),
        wins=wins,
        losses=losses,
        timeouts=timeouts_flat,
        win_rate=win_rate,
        total_r=total_r,
        average_r=average_r,
        profit_factor=profit_factor,
        max_drawdown_r=max_dd,
        expectancy_r=expectancy,
        longest_win_streak=longest_win_streak,
        longest_loss_streak=longest_loss_streak,
        long_trades=len(long_trades),
        short_trades=len(short_trades),
        long_win_rate=long_win_rate,
        short_win_rate=short_win_rate,
    )


def trades_to_dataframe(result: BacktestResult) -> pd.DataFrame:
    rows = []
    for t in result.trades:
        rows.append({
            "date": t.day.date().isoformat(),
            "session_open_utc": t.session_open.isoformat(),
            "range_high": round(t.range_high, 4),
            "range_low": round(t.range_low, 4),
            "range_mid": round(t.range_mid, 4),
            "range_size": round(t.range_size, 4),
            "breakout_time_utc": t.breakout_time.isoformat(),
            "direction": t.direction,
            "entry_mode": t.entry_mode,
            "entry_price": round(t.entry_price, 4),
            "entry_time_utc": t.entry_time.isoformat(),
            "sl_price": round(t.sl_price, 4),
            "tp_price": round(t.tp_price, 4),
            "sl_moved": t.sl_moved,
            # Final stop in force at exit: the moved stop when the half-TP
            # rule fired, otherwise the original stop. Always a number so the
            # JSON stream never carries NaN (json.dumps would emit a bare
            # "NaN" token, which is invalid JSON for browsers).
            "sl_final": round(t.sl_final, 4),
            "exit_price": round(t.exit_price, 4),
            "exit_time_utc": t.exit_time.isoformat(),
            "exit_reason": t.exit_reason,
            "r_multiple": round(t.r_multiple, 4),
        })
    return pd.DataFrame(rows)


def equity_curve(result: BacktestResult) -> list[dict]:
    curve = []
    running = 0.0
    for t in result.trades:
        running += t.r_multiple
        curve.append({"date": t.day.date().isoformat(), "cumulative_r": round(running, 4)})
    # Prepend the baseline: the curve starts at 0 R before any trade is taken.
    if curve:
        curve.insert(0, {"date": curve[0]["date"], "cumulative_r": 0.0})
    return curve
