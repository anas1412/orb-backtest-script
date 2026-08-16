"""Single source of truth for every tunable default in the app.

The engine (strategy.Params), the API models (BacktestParams, CapitalSimParams,
export endpoint) and the UI (index.html template + injected window.DEFAULTS)
all read their defaults from this one dict, so a default change lands
everywhere at once. Bounds/validation constraints still live with their models.
"""
from __future__ import annotations

# Full trading week (ISO: Mon=0 ... Fri=4). Sat/Sun are never traded.
ALL_WEEKDAYS: tuple[int, ...] = (0, 1, 2, 3, 4)

DEFAULTS: dict = {
    # --- strategy ---
    "session_open_utc": "00:00",
    "range_minutes": 15,
    "breakout_search_minutes": 14,
    "entry_mode": "market",
    "sl_pct_of_range": 0.5,
    "tp_rr": 2.0,
    "spread_pips": 0.0,
    "slippage_pips": 0.0,
    "session_max_hours": 1.0,
    "trading_days": ALL_WEEKDAYS,
    # Fixed Mon-Fri set the "All days" toggle means (independent of the default subset above).
    "all_weekdays": ALL_WEEKDAYS,
    "sl_ladder": ((0.5, -0.5),),
    "pip_size": 0.01,  # gold: 1 pip = $0.01 by common retail convention
    # --- data upload ---
    "symbol": "XAUUSD",
    "upload_tz": "auto",
    # --- capital simulation ---
    "capital_enabled": False,
    "capital_initial": 10000.0,
    "capital_risk_pct": 1.0,
    "capital_mode": "fixed",
    # --- monte carlo ---
    "montecarlo_iterations": 1000,
    "montecarlo_sample_mode": "bootstrap",
    "montecarlo_seed": None,  # None = random (non-deterministic)
    "montecarlo_capital": 10000.0,
    "montecarlo_risk_pct": 2.0,
    "montecarlo_sizing": "fixed",
    "montecarlo_target_pct": 8.0,
    "montecarlo_max_dd_pct": 10.0,
}
