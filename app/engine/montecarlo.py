"""
Monte Carlo simulation over the observed trade R-multiples.

The backtest produces ONE realized sequence of trade outcomes. Monte Carlo
resamples those R-multiples many times - either by permuting their order
("shuffle") or by drawing with replacement ("bootstrap") - then re-sizes
each resampled path through the same dollar-sizing math `simulate_capital`
uses. Each account is a prop-firm CHALLENGE: it is followed trade by trade
and PAUSED at the first boundary it reaches - hitting the target return
(PASS) or breaching the drawdown limit (BLOW). Every reported return and
drawdown is measured at that stopped point, so no account runs past its own
+target% / -max_dd% boundary (accounts that reach neither boundary run the
full series).

Two independent resampling modes (both run when sample_mode="both"):

- "shuffle": random permutation of the observed R values. Keeps the exact
  outcome distribution but randomizes the ORDER, so it exposes sequence
  risk: how much do the max drawdown and the final return depend on the
  luck of ordering? (For fixed sizing the final return is order-invariant,
  so under "shuffle" the interesting quantity is the drawdown spread.)
- "bootstrap": draws N values WITH replacement from the observed R set, so
  some outcomes repeat and others vanish. Exposes sampling robustness: how
  sure are we that the observed edge isn't a lucky mix of trades?

Across iterations we report percentiles of the stopped final return % and of
the stopped max drawdown %, plus P(loss), the pass rate (= P(hitting the
target first)), the risk of ruin (= P(blowing first)) and a per-trade
histogram of how many trades each account ran before pausing, split by
outcome (passed / blown / ran all trades).

Pure post-process over closed trades (their R-multiples): it never re-runs
the backtest engine. Deterministic for a given seed via
`np.random.default_rng(seed)`.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import numpy as np

PERCENTILES = (5, 25, 50, 75, 95)


@dataclass
class MonteCarloVariant:
    """Outcome distribution of one resampling mode over the observed trades."""

    mode: str  # "shuffle" | "bootstrap"
    iterations: int
    seed: int | None
    initial_capital: float
    risk_pct: float
    sizing: str  # "fixed" | "compounding"
    target_pct: float
    max_dd_pct: float
    n_trades: int
    # The single observed path, stopped the same way, for reference.
    actual_final_return_pct: float
    actual_max_drawdown_pct: float
    # {p5, p25, p50, p75, p95} of final return % at the stopped point.
    final_return_percentiles: dict[str, float]
    # {p5, p25, p50, p75, p95} of max drawdown % along the stopped path.
    max_drawdown_percentiles: dict[str, float]
    # {p5, p25, p50, p75, p95} of trades-to-stop (first event, or the full
    # series when neither).
    trades_stop_percentiles: dict[str, float]
    mean_final_return_pct: float
    min_final_return_pct: float
    max_final_return_pct: float
    p_loss: float          # P(stopped final return < 0)
    # [[bin_center (trades), passed, blown, neither], ...] of trades taken per
    # account: how many trades each account ran before its first event (the
    # whole series when neither), and which outcome it hit.
    trades_histogram: list[list]
    # Account-level outcomes: each iteration is one simulated account, so
    # total_accounts == iterations. An account is followed trade by trade and
    # the FIRST event wins: it PASSES if it reaches the target return before
    # ever breaching the drawdown limit, it BLOWS if the drawdown limit is
    # breached before the target is reached — and it STOPS there (the account
    # is paused; it never trades past its boundary). Accounts that do neither
    # run the whole series and count in neither bucket. pass_rate = passed /
    # total. avg_trades_to_target / avg_trades_to_blow are the mean number of
    # trades it took among the accounts that passed / blew (None when none did).
    total_accounts: int
    passed_accounts: int
    blown_accounts: int
    pass_rate: float
    risk_of_ruin: float   # blown_accounts / total_accounts
    avg_trades_to_target: float | None
    avg_trades_to_blow: float | None


def _sample_matrix(
    rng: np.random.Generator,
    r_values: np.ndarray,
    iterations: int,
    mode: str,
) -> np.ndarray:
    """Build an (iterations, n) array of resampled R values for one mode."""
    n = r_values.shape[0]
    if mode == "shuffle":
        tile = np.broadcast_to(r_values.copy(), (iterations, n))
        return rng.permuted(tile, axis=1)
    # bootstrap: with replacement
    idx = rng.integers(0, n, size=(iterations, n))
    return r_values[idx]


def _equity_paths(
    r_matrix: np.ndarray,
    initial_capital: float,
    risk_pct: float,
    compounding: bool,
) -> np.ndarray:
    """Per-row account equity after each trade.

    Fixed sizing risks initial_capital * p on every trade (linear account).
    Compounding risks the CURRENT equity * p, so the account grows and
    shrinks multiplicatively: equity_n = equity_{n-1} * (1 + R_n * p).
    Equity is clamped at >= 0 so a catastrophic run can only wipe the account.
    """
    p = risk_pct / 100.0
    if compounding:
        factors = np.maximum(1.0 + p * r_matrix, 0.0)
        return initial_capital * np.cumprod(factors, axis=1)
    return initial_capital * (1.0 + p * np.cumsum(r_matrix, axis=1))


def _max_drawdown_pct(equity: np.ndarray) -> np.ndarray:
    """Per-row max peak-to-trough drawdown as % of the running peak."""
    peak = np.maximum.accumulate(equity, axis=1)
    dd = (peak - equity) / np.maximum(peak, 1e-12) * 100.0
    return dd.max(axis=1)


def _first_index(mask: np.ndarray) -> np.ndarray:
    """Per-row column index of the FIRST True, or -1 when a row has none."""
    has = mask.any(axis=1)
    return np.where(has, np.argmax(mask, axis=1), -1)


def _challenge_analysis(
    equity: np.ndarray,
    initial_capital: float,
    target_pct: float,
    max_dd_pct: float,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Prop-firm challenge analysis over an (accounts, trades) equity grid.

    Each account is followed trade by trade and the FIRST event wins: reach
    `target_pct` return (PASS) or breach `max_dd_pct` running drawdown (BLOW);
    on the same trade both would hold, the target wins. Accounts that do
    neither run the whole series. Everything reported is taken at the STOPPED
    point: a passing account is evaluated at exactly +target_pct (that is what
    the challenge pays) and a blown account at exactly -max_dd_pct drawdown
    (that is where it is halted) - no account shows a return past its boundary.

    Returns (passed, blown, event_i, final_return_pct, stopped_max_dd_pct)
    where `passed`/`blown` are boolean masks, `event_i` is the column index of
    the first event (last column when neither), `final_return_pct` is the
    return % at the stopped point, and `stopped_max_dd_pct` is the max
    drawdown % along the path up to that point.
    """
    ret_pct = (equity / initial_capital - 1.0) * 100.0
    peak = np.maximum.accumulate(equity, axis=1)
    dd_run = (peak - equity) / np.maximum(peak, 1e-12) * 100.0

    reach_i = _first_index(ret_pct >= target_pct)
    blow_i = _first_index(dd_run > max_dd_pct)
    has_reach, has_blow = reach_i >= 0, blow_i >= 0

    passed = has_reach & ((~has_blow) | (reach_i <= blow_i))
    blown = has_blow & ((~has_reach) | (blow_i < reach_i))

    rows, n = equity.shape[0], equity.shape[1]
    event_i = np.where(passed, reach_i, np.where(blown, blow_i, n - 1))

    final_return_pct = ret_pct[np.arange(rows), event_i]
    col = np.arange(n)
    dd_stopped = np.where(col[None, :] <= event_i[:, None], dd_run, 0.0)
    stopped_max_dd_pct = dd_stopped.max(axis=1)

    # Snap the paused outcome to its boundary: a passing account is sized at
    # exactly +target_pct (that is what the challenge pays) and a blown
    # account at exactly -max_dd_pct (that is where it is halted).
    final_return_pct = np.where(
        passed, target_pct, np.where(blown, -max_dd_pct, final_return_pct)
    )
    stopped_max_dd_pct = np.where(blown, max_dd_pct, stopped_max_dd_pct)
    return passed, blown, event_i, final_return_pct, stopped_max_dd_pct


def _percentile_dict(values: np.ndarray, ps=PERCENTILES) -> dict[str, float]:
    return {f"p{int(p)}": round(float(np.percentile(values, p)), 4) for p in ps}


def _outcome_histogram(
    event_i: np.ndarray,
    passed: np.ndarray,
    blown: np.ndarray,
) -> list[list]:
    """[[bin_center (trades), passed, blown, neither], ...] of trades taken.

    The first event (pass/blow) decides where an account stops; an account
    that does neither runs the whole series. One bin per trade (one trade per
    trading day), split into how many of the accounts landing in that bin
    passed, blew, or ran all trades. Trailing empty buckets are trimmed.
    """
    if event_i.size == 0:
        return []
    stop = event_i + 1
    n = int(stop.max())
    pass_c = np.bincount(stop[passed], minlength=n + 1)
    blow_c = np.bincount(stop[blown], minlength=n + 1)
    neit_c = np.bincount(stop[~passed & ~blown], minlength=n + 1)
    while n > 1 and not (pass_c[n] or blow_c[n] or neit_c[n]):
        n -= 1
    return [
        [i, int(pass_c[i]), int(blow_c[i]), int(neit_c[i])]
        for i in range(1, n + 1)
    ]


def _run_variant(
    r_values: np.ndarray,
    rng: np.random.Generator,
    mode: str,
    iterations: int,
    seed: int | None,
    initial_capital: float,
    risk_pct: float,
    compounding: bool,
    target_pct: float,
    max_dd_pct: float,
) -> MonteCarloVariant:
    sizing = "compounding" if compounding else "fixed"
    r_matrix = _sample_matrix(rng, r_values, iterations, mode)
    equity = _equity_paths(r_matrix, initial_capital, risk_pct, compounding)

    # Every stat below is measured at the first event (pass/blow) per account
    # — a passing account is stopped at its +target% hit, a blown account at
    # its drawdown breach. No account runs on past its boundary.
    passed, blown, event_i, final_return_pct, max_dd = _challenge_analysis(
        equity, initial_capital, target_pct, max_dd_pct
    )
    total = equity.shape[0]
    passed_count = int(passed.sum())
    blown_count = int(blown.sum())
    pass_rate = round(passed_count / total, 4) if total else 0.0
    avg_to_target = (
        round(float((event_i[passed] + 1).mean()), 2) if passed_count else None
    )
    avg_to_blow = (
        round(float((event_i[blown] + 1).mean()), 2) if blown_count else None
    )

    # The observed (single) path, stopped the same way, for direct comparison.
    obs = _equity_paths(
        r_values.reshape(1, -1), initial_capital, risk_pct, compounding
    )
    _, _, _, actual_final_pct, actual_dd_pct = _challenge_analysis(
        obs, initial_capital, target_pct, max_dd_pct
    )
    actual_final_return_pct = round(float(actual_final_pct[0]), 4)
    actual_max_drawdown_pct = round(float(actual_dd_pct[0]), 4)

    return MonteCarloVariant(
        mode=mode,
        iterations=int(iterations),
        seed=seed,
        initial_capital=round(initial_capital, 2),
        risk_pct=risk_pct,
        sizing=sizing,
        target_pct=target_pct,
        max_dd_pct=max_dd_pct,
        n_trades=int(r_values.shape[0]),
        actual_final_return_pct=actual_final_return_pct,
        actual_max_drawdown_pct=actual_max_drawdown_pct,
        final_return_percentiles=_percentile_dict(final_return_pct),
        max_drawdown_percentiles=_percentile_dict(max_dd),
        trades_stop_percentiles=_percentile_dict(event_i + 1),
        mean_final_return_pct=round(float(final_return_pct.mean()), 4),
        min_final_return_pct=round(float(final_return_pct.min()), 4),
        max_final_return_pct=round(float(final_return_pct.max()), 4),
        p_loss=round(float(np.mean(final_return_pct < 0.0)), 4),
        trades_histogram=_outcome_histogram(event_i, passed, blown),
        total_accounts=int(iterations),
        passed_accounts=passed_count,
        blown_accounts=blown_count,
        pass_rate=pass_rate,
        risk_of_ruin=round(blown_count / iterations, 4),
        avg_trades_to_target=avg_to_target,
        avg_trades_to_blow=avg_to_blow,
    )


def run_monte_carlo(
    trades,
    iterations: int = 1000,
    sample_mode: str = "bootstrap",
    seed: int | None = None,
    initial_capital: float = 10000.0,
    risk_pct: float = 2.0,
    sizing: str = "fixed",
    target_pct: float = 8.0,
    max_dd_pct: float = 10.0,
) -> dict[str, MonteCarloVariant]:
    """
    Run Monte Carlo simulations over a list of closed trades.

    `trades` must be a sequence of objects with an `r_multiple` attribute
    (the engine's `Trade`). Returns a dict keyed by the modes actually run
    ("shuffle" and/or "bootstrap"), each holding a `MonteCarloVariant`.
    """
    r_values = np.asarray([t.r_multiple for t in trades], dtype=float)
    if r_values.shape[0] == 0:
        raise ValueError("No trades to simulate — the backtest produced zero trades.")

    modes = ["shuffle", "bootstrap"] if sample_mode == "both" else [sample_mode]
    rng = np.random.default_rng(seed)
    compounding = sizing == "compounding"
    return {
        mode: _run_variant(
            r_values,
            rng,
            mode,
            iterations,
            seed,
            initial_capital,
            risk_pct,
            compounding,
            target_pct,
            max_dd_pct,
        )
        for mode in modes
    }