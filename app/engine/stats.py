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
    win_rate_no_be: float | None  # wins / (wins + losses), ignores 0R breakevens
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
            win_rate_no_be=None, total_r=0.0, average_r=0.0, profit_factor=None, max_drawdown_r=0.0,
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
    decided = wins + losses
    win_rate_no_be = (wins / decided) if decided > 0 else None

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
        win_rate_no_be=win_rate_no_be,
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
            # Final stop in force at exit: the last ladder step's stop when
            # the ladder advanced, otherwise the original stop. Always a
            # number so the
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


@dataclass
class CapitalSim:
    final_equity: float
    total_pnl: float
    total_return_pct: float
    max_drawdown_usd: float
    max_drawdown_pct: float
    profit_factor_usd: float | None
    expectancy_usd: float
    avg_pnl: float
    # Per-trade dollar P&L, aligned with the ORIGINAL trade order (the order
    # they appear in BacktestResult.trades), not the internal sort order.
    pnl: list[float]
    curve: list[dict]  # [{"date", "equity"}] including the initial baseline


def simulate_capital(
    trades,
    initial_capital: float,
    risk_pct: float,
    compounding: bool,
) -> CapitalSim:
    """
    Simulate a bankroll over the closed trades.

    Each trade risks `risk_pct` of a capital base. "Fixed" sizes every trade
    off the INITIAL capital (constant dollar risk), so the account is
    linear in R: equity = initial + initial*risk_pct*sum(R). "Compounding"
    sizes each trade off the CURRENT equity, so the account grows and
    shrinks multiplicatively: equity_n = equity_{n-1} * (1 + R_n*risk_pct).

    Trades are simulated in exit-time order (defensive; the engine already
    produces them chronologically), but the returned `pnl` list preserves the
    input order so callers can align it 1:1 with the trade list.
    """
    ordered = sorted(enumerate(trades), key=lambda it: it[1].exit_time)

    risk_fraction = risk_pct / 100.0
    equity = float(initial_capital)
    peak = equity
    max_dd_usd = 0.0
    max_dd_pct = 0.0
    gross_win = gross_loss = 0.0
    pnl_by_index: dict[int, float] = {}
    curve = []

    for idx, t in ordered:
        risk_amount = equity * risk_fraction if compounding else initial_capital * risk_fraction
        pnl = t.r_multiple * risk_amount
        pnl_by_index[idx] = round(pnl, 2)
        equity += pnl
        if pnl > 0:
            gross_win += pnl
        elif pnl < 0:
            gross_loss += -pnl
        peak = max(peak, equity)
        dd_usd = peak - equity
        if dd_usd > max_dd_usd:
            max_dd_usd = dd_usd
            max_dd_pct = (dd_usd / peak * 100.0) if peak > 0 else 0.0
        curve.append({"date": t.day.date().isoformat(), "equity": round(equity, 2)})

    if curve:
        curve.insert(0, {"date": curve[0]["date"], "equity": round(initial_capital, 2)})

    pnl = [pnl_by_index.get(i, 0.0) for i in range(len(trades))]
    total_pnl = round(equity - initial_capital, 2)
    n = len(pnl)
    return CapitalSim(
        final_equity=round(equity, 2),
        total_pnl=total_pnl,
        total_return_pct=round(total_pnl / initial_capital * 100.0, 2) if initial_capital > 0 else 0.0,
        max_drawdown_usd=round(max_dd_usd, 2),
        max_drawdown_pct=round(max_dd_pct, 2),
        profit_factor_usd=(round(gross_win / gross_loss, 4) if gross_loss > 0 else None),
        expectancy_usd=round(sum(pnl) / n, 2) if n > 0 else 0.0,
        avg_pnl=round(sum(pnl) / n, 2) if n > 0 else 0.0,
        pnl=pnl,
        curve=curve,
    )
