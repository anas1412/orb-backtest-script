# Gold (XAUUSD) Asia Session Opening Range Breakout — Backtest Plan

## 1. Goal
Backtest a breakout strategy on Gold, M1 timeframe: mark the high/low of the first 15 minutes of the Asia session, then trade a breakout of that range, with configurable entry style, stop loss, and take profit.

---

## 2. Timezone

- The system operates entirely in UTC. Every timestamp stored, compared, and logged is UTC — no local timezone conversions anywhere in the logic.
- Asia session open is defined as 00:00 UTC, matching the Asia/Tokyo session opening convention (00:00 UTC / 09:00 JST). Note: "Asia session open" is not a single universal industry standard — some definitions use Sydney open (22:00 UTC) instead of Tokyo open (00:00 UTC). This system uses 00:00 UTC by default. It is a single configurable parameter (`SESSION_OPEN_UTC`) if a different definition is required.
- Example date: 12 Aug 2026 → session open = 2026-08-12 00:00:00 UTC.

---

## 3. Data Requirements
- Instrument: Gold (XAUUSD)
- Timeframe: M1 (1-minute candles), OHLC + timestamp minimum
- Source: pluggable data loader (returns a standard OHLCV DataFrame), so the source can be swapped without touching strategy logic. See Section 10 for candidate free sources.
- Required columns: `timestamp (UTC)`, `open`, `high`, `low`, `close` (volume optional, not used in logic)
- Data must be timezone-aware or explicitly UTC-labeled on load. The loader validates this and raises an error rather than silently assuming a timezone.

---

## 4. Strategy Logic (step by step)

### Step 1 — Define the Opening Range
- Window: `[session_open_utc, session_open_utc + 15min)` → e.g. `00:00–00:15 UTC`
- `range_high` = max(high) of M1 candles in that window
- `range_low` = min(low) of M1 candles in that window
- `range_size` = `range_high - range_low`
- `range_mid` = `(range_high + range_low) / 2`

### Step 2 — Wait for a breakout candle
- Starting after the range window closes (00:15 UTC), scan each subsequent M1 candle close.
- Breakout condition:
  - Bullish breakout: candle `close > range_high`
  - Bearish breakout: candle `close < range_low`
- Timeout: if no breakout candle closes beyond the range within 1 hour of session open (i.e., by 01:00 UTC), stop looking — no trade for that day.
- Only the first qualifying breakout candle counts (first close outside range, in either direction).

### Step 3 — Entry
Two configurable modes (parameter: `entry_mode`):
- `market` (default): entry fills at the next candle's open following the breakout candle's close (the breakout candle's own close price cannot be used as a fill price in a backtest, so the next open is used as the realistic fill).
- `limit`: a resting limit order is placed back at the range boundary that was broken (`range_high` for a buy, `range_low` for a sell). It fills only if price returns to that level. The order expires at the same 1-hour cutoff from session open used for the breakout search (i.e., by 01:00 UTC) — one unified cutoff governs both the breakout search and the limit-order expiry. If unfilled by then, no trade for that day.
- Direction: buy if bullish breakout, sell if bearish breakout.

### Step 4 — Stop Loss
- SL is anchored at the range midpoint: `range_mid = (range_high + range_low) / 2`, regardless of entry mode or entry price.
- Parameter: `sl_pct_of_range` (default `0.5`) sets how far from the midpoint toward the entry side the SL sits, expressed as a fraction of `range_size`. Default `0.5` places SL exactly at the midpoint. Setting `1.0` moves the SL to the opposite boundary of the range.

### Step 5 — Take Profit
- Default: 1R (i.e., TP distance = SL distance × 1).
- Parameter: `tp_rr` (default `1.0`), any positive number (e.g. `2.0` = 2R).

### Step 6 — Trade management
- One trade per day maximum (only the first valid breakout is traded).
- Trade exits on whichever of SL/TP is hit first, checked candle-by-candle going forward from entry.
- If neither SL nor TP is hit within `session_max_hours` (default 20 hours) from entry, the trade is force-closed at the last available price at that cutoff, or at end of dataset if that comes first.

---

## 5. Data Costs (execution realism)
- `spread_pips` and `slippage_pips` are applied to entry and exit fills.
- Default for both: `0` (raw OHLC prices, frictionless baseline). Can be set to nonzero values for a more realistic pass.

---

## 6. Weekend Handling
- Saturday and Sunday are skipped entirely — no session, range, or trade logic runs on those days.
- Monday is the first trading day of the week.

---

## 7. Language: Python

Implementation language: Python throughout (backend, engine, and API).

---

## 8. Free Data Sources for M1 Gold (XAUUSD)

| Source | Notes |
|---|---|
| [HistData.com](https://www.histdata.com/download-free-forex-historical-data/) | Free M1 bar data (and tick data) for forex + metals, downloadable by month/pair, exportable as generic CSV. No signup required. |
| Dukascopy Historical Data Feed (via [Dukascopy's tools](https://www.dukascopy.com/swiss/english/marketwatch/historical/) or third-party front-ends) | Free tick data going back years for XAUUSD, aggregatable to M1. Considered one of the cleanest free sources; raw download UI is not user-friendly. Some third-party sites pre-package it as M1/M5/M15 CSV. |
| MetaTrader 5 History Center | Free M1 XAUUSD history from MetaQuotes' servers, accessible directly within MT5. Data completeness/quality varies by broker feed. |
| Kaggle — "XAU/USD Gold Price Historical Data (2004–2026)" | Pre-packaged dataset with multiple timeframes including intraday. Timeframe granularity and timezone labeling should be verified before use. |
| TrueFX | Free tick-level historical data (aggregated interbank rates). Requires aggregation to M1; high quality if precision matters. |

Recommended starting source: HistData.com M1 CSVs for XAUUSD — free, no signup, already 1-minute bars, minimal preprocessing.

Note: several free sources timestamp in EST/EET or platform-server time rather than UTC. The data loader requires an explicit, source-specific offset to normalize to UTC before any logic runs — it will not assume raw timestamps are already UTC.

---

## 9. Application Architecture — Python + FastAPI backend with UI

### Backend (Python, FastAPI)
- `POST /api/backtest/run` — accepts a JSON payload of all strategy parameters (Section 11), runs the backtest (synchronously, or as a background task with a job ID for longer runs), returns structured JSON results.
- `GET /api/backtest/results/{job_id}` — fetches results of a completed asynchronous run.
- `GET /api/data/status` — reports currently loaded data (symbol, date range, source, timezone confirmation).
- Core backtest logic is framework-agnostic (a pure Python module, independently testable), with FastAPI as the transport layer only — the same engine is reusable from a CLI or notebook.

### Frontend (served by/with the FastAPI app)
A single-page dashboard UI:
- Parameters panel — every parameter from Section 11 exposed as labeled inputs (dropdowns for `entry_mode`, numeric steppers for `sl_pct_of_range` / `tp_rr` / `spread_pips` / `slippage_pips`, date range picker for the backtest period), grouped into Session Settings / Entry Settings / Risk Settings / Execution Costs.
- Strategy summary panel — a plain-language restatement of the current configuration ("Buy/sell on M1 close beyond the 00:00–00:15 UTC range, SL at range midpoint, TP at 1R…"), updating live as parameters change.
- Generate button — triggers the backtest run, shows a loading state, disabled while running.
- Results view, shown after generation:
  - Headline stats cards: total trades, win rate, total R, average R per trade, profit factor, max drawdown (in R and %), expectancy, longest win/loss streak.
  - Equity curve chart (cumulative R over time).
  - Distribution chart of trade outcomes (R histogram).
  - Breakdown table: performance split by breakout direction (long vs short) and by day-of-week.
  - Full trade log table, sortable/filterable: date, session open (UTC), range high/low/mid, breakout time & direction, entry mode/price/time, SL, TP, exit price/time/reason (SL/TP/timeout), result in R.
  - Export button (CSV) for the trade log.
- Visual design: clean, data-dense, dark-mode-friendly dashboard layout with clear separation between inputs and outputs; card-based layout, consistent spacing/typography.

### Tech stack
- Backend: Python, FastAPI, pandas/numpy for the backtest engine, uvicorn to serve.
- Frontend: served via FastAPI (Jinja2 templates plus a modern CSS/JS approach, or a lightweight JS framework mounted as static files) — exact frontend implementation finalized during build, targeting a professional dashboard rather than a plain HTML form.

---

## 10. Full Parameter List

| Parameter | Default | Notes |
|---|---|---|
| `symbol` | XAUUSD | |
| `timeframe` | M1 | |
| `session_open_utc` | 00:00 | Asia/Tokyo session open convention |
| `range_minutes` | 15 | opening range duration |
| `breakout_search_minutes` | 180 | stop looking 3h after session open; also governs limit-order expiry |
| `entry_mode` | `market` | or `limit` |
| `sl_anchor` | range midpoint | fixed rule, not a parameter |
| `sl_pct_of_range` | 0.5 | fraction of range size from midpoint; 0.5 = midpoint itself, adjustable |
| `tp_rr` | 1.0 | risk:reward multiple, any positive number |
| `spread_pips` | 0 | execution cost, off by default |
| `slippage_pips` | 0 | execution cost, off by default |
| `session_max_hours` | 20 | force-close open trades after this many hours from entry |
| `skip_weekends` | true | Saturday & Sunday excluded entirely |

Note: with a single session (one opening range, one breakout search window per day), at most one trade per day is possible structurally — there is only ever one setup to take, so no "max trades per day" parameter exists in this version. Multiple trades per day would require multiple sessions (e.g. adding London/New York opens alongside Asia), deferred to a later phase.

---

## 11. Report Output (per backtest run)
- Total trades, win rate, average R, total R, max drawdown, profit factor, expectancy, longest win/loss streak.
- Equity curve chart.
- Distribution chart of trade outcomes.
- Breakdown by direction (long/short) and by day-of-week.
- Full trade log (CSV export): date, session open (UTC), range high/low/mid, breakout time/direction, entry price & time, SL price, TP price, exit price/time/reason, result in R.
- All timestamps logged in UTC.

---

## 12. Build Sequence
1. Core backtest engine (data loader → session/range logic → strategy → trade simulation → stats)
2. FastAPI backend wrapping the engine
3. Dashboard UI
