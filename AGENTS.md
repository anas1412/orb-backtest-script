# AGENTS.md

Asia-session opening-range breakout backtester for M1 gold (XAUUSD). FastAPI dashboard over a pure-Python backtest engine. All logic is UTC-only.

## Setup & run

```bash
python3 -m venv .venv && source .venv/bin/activate   # deps are NOT installed on the system python
pip install -r requirements.txt                       # fastapi, uvicorn[standard], pandas, numpy, python-multipart, jinja2
python3 -m uvicorn app.main:app --host 0.0.0.0 --port 8000   # MUST run from repo root (imports are app.engine.*)
```

UI at `http://localhost:8000`. No test suite, no pytest, no lint/typecheck config exists.

## Architecture

- `app/engine/` is framework-agnostic and importable standalone (notebook/script): `data_loader` (CSV -> UTC DataFrame) -> `session` (daily windows, trading days) -> `strategy` (breakout detection, entry modes, SL/TP) -> `backtester` (per-day loop, exit simulation) -> `stats` (summary, trade log, equity curve).
- `app/main.py` is a thin transport layer: routes call the engine, then keep results in module-level in-memory dicts (`DATA_STORE`, `RESULT_STORE`). Everything is lost on restart; this is a single-user local tool.
- README's "Verified behavior" section is the strategy spec; the engine docstrings are the closest thing to an authoritative description of intended behavior. Reconcile behavior changes against them.
- Frontend is vanilla JS + a hand-rolled canvas equity chart (no chart library).

## Invariants (easy to break, deliberately implemented)

- Timestamps are tz-aware UTC end-to-end. Uploads take a `source_tz`; the loader NEVER guesses silently. `"auto"` (UI default) infers the offset from weekend gaps (Sunday reopen 22:00 UTC anchor + Friday close ~21:00 UTC check, broker range -5..+5h) and raises a clear error instead of guessing when the data has no reliable signal (`detect_utc_offset`).
- CSV schema: `timestamp, open, high, low, close` (+optional `volume`). Aliases (date/time or date/hour/minute split columns, o/h/l/c) are merged; headerless MetaTrader-style exports (`date time, o, h, l, c, v`) are auto-detected via the first cell parsing as a datetime. Mixed timestamp formats are tolerated: ISO-8601 rows (`2025-11-17T14:02:00Z`, e.g. an appended export block) are normalized to `YYYY-MM-DD HH:MM:SS` before parsing, because pandas 3.x NaTs the minority format in a mixed column; the stripped `Z` merely labels UTC, which `source_tz`/`"auto"` still resolve. Rows where high/low don't bound open/close are REJECTED on upload.
- `Params.pip_size = 0.01` hardcoded (gold convention); `spread_pips`/`slippage_pips` are in pips and applied adversely on entry and exit.
- `session_open_utc` is an "HH:MM" string applied per calendar day. Weekends skipped by default.
- `/api/backtest/run` accepts optional `start_date`/`end_date` (DD/MM/YYYY or ISO, inclusive days UTC) that window the dataset before the backtest; `_normalize_date` in main.py is the single parser. The endpoint streams the run as NDJSON: one `{"type":"progress","done","total"}` line per trading day (fed by the engine's `progress_cb` from a daemon thread), then a final `{"type":"done","result":{...}}` or `{"type":"error","detail"}` line.
- At most ONE trade per day structurally (single opening range per day); no max-trades parameter exists.
- Market entry fills at the NEXT bar's open after the breakout candle (never the breakout close). Limit entry fills at the broken boundary only if touched before `search_deadline`, else expires.
- SL is anchored at range midpoint: `sl_pct_of_range 0.5` = exact midpoint, `1.0` = opposite boundary. TP = `tp_rr` x risk distance from entry.
- Same-bar SL+TP touch resolves to SL (conservative, no tick data). Timeout exits at the last close at/before entry + `session_max_hours`; with no future bars it exits at entry price, 0R.
- Days with no trade are recorded in `result.no_trade_days` with a reason (`no_range_data` | `no_breakout` | `no_fill`) — do not drop this.

## Verification

- Engine smoke test (no server needed): load `datasets/XAUUSD_M1_2024-01-01_2025-12-06.csv` via `load_ohlcv_csv`, run backtest, compute stats — `load_ohlcv_csv(...)` -> `run_backtest(loaded.df, Params())` -> `compute_stats(result)`.
- README's "Verified behavior" section lists invariants that must keep holding (SL 0.5 lands exactly on midpoint, tp_rr 1.0 gives +/-1.0 R, limit fills at boundary, timeout at `session_max_hours`).