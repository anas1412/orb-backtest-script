# Asia Range Breakout — Gold (XAUUSD) Backtester

A Python + FastAPI backtesting tool for an Asia-session opening-range breakout
strategy on Gold, M1 timeframe. All logic operates in UTC internally.

![Asia Range Breakout dashboard](docs/screenshot.png)

## What it does

1. Marks the high/low of the first N minutes after a configurable session
   open time (UTC), default 00:00 UTC / 15 minutes.
2. Watches for the first M1 candle to close outside that range within a
   configurable search window after the range closes (default 14 minutes →
   search until 00:29).
3. Enters via market order (next bar's open) or limit order (resting at the
   broken boundary, expiring at the search-window deadline).
4. Places the stop loss at the range midpoint by default (adjustable), and
   take profit at a configurable R multiple.
5. Force-closes any trade still open after a configurable max duration.
6. Reports full statistics, an equity curve, breakdowns by direction and
   day-of-week, and a downloadable trade log.

## Try it online (hosted)

No installation needed. Open the hosted app, upload your M1 gold CSV, and run:

**https://orb-backtest-script--anasbassoumi1.replit.app/**

1. Open the link.
2. Click **Load bundled data** to load the real gold M1 datasets shipped with the repo (all available months) and run a backtest instantly — no files needed. Or upload your own data.
3. Set "Raw timestamp TZ" to your CSV's source timezone (or leave `auto`).
4. Adjust strategy params if you like, then click **Generate backtest**.

## Running it locally

One command (auto-creates a venv, installs deps, reads `PORT` env var, defaults to 8000):

```bash
python3 run.py
```

Then open `http://localhost:8000` in a browser.

Already have the venv set up? Alternative:

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python3 -m uvicorn app.main:app --host 0.0.0.0 --port 8000
```

## Data

- Upload one or more M1 CSV files with columns `timestamp, open, high, low, close`
  (case-insensitive; common aliases like `date`/`time`/`o`/`h`/`l`/`c` are
  also recognized). If a file's raw timestamps are not already UTC, set
  the "Raw timestamp TZ" field to the correct source timezone (e.g. `EET`,
  `America/New_York`) before uploading — the loader will not guess this.
- Multiple files may be mixed formats (standard columns, MetaTrader-style
  headerless exports, split date/time columns). Each file is parsed
  individually, normalized to UTC, then merged into one dataset; bars with
  duplicate timestamps keep the first file's copy. One report is generated
  over the whole merged dataset.

Free M1 gold data sources to try: HistData.com, Dukascopy, MetaTrader 5's
History Center, or a Kaggle-hosted historical gold dataset. Verify each
source's raw timestamp timezone before uploading.

## API

- `POST /api/data/upload` — upload one or more M1 CSVs (`files` multipart field, repeated for multiple files)
- `GET /api/data/status/{dataset_id}` — check a loaded dataset
- `POST /api/backtest/run` — run a backtest with a given parameter set
- `POST /api/backtest/simulate/{job_id}` — size the trade log onto a dollar account (see below)
- `POST /api/backtest/montecarlo/{job_id}` — Monte Carlo across resampled trade orders (see below)
- `GET /api/backtest/export/{job_id}` — download the trade log as CSV; optional query params `capital=1&initial_capital=10000&risk_pct=1&mode=fixed|compounding` add a `pnl_usd` column
- `POST /api/backtest/images/{job_id}` — build a ZIP of one candlestick chart per trade (background thread, poll `/status` then download once)
- `GET /api/backtest/images/{job_id}/status` — render progress (`generating` → `ready`); `done`/`total` give the percent
- `GET /api/backtest/images/{job_id}/download` — stream the charts ZIP; the file is deleted from the server the moment the download completes (single-use, a second download 404s)

## Capital simulation

A pure post-process over the backtest's R-multiples (no re-run, instant):

- `initial_capital` — starting bankroll in dollars (default 10,000).
- `risk_pct` — percent of the account risked per trade (default 1; must be > 0 and < 10).
- `mode` — `fixed` (default) risks the same dollar
  amount every trade (`initial × p`); `compounding` sizes each trade off the current equity
  (`equity_{n} = equity_{n-1} × (1 + r_n × p)`).

When enabled, the dashboard shows dollar figures alongside R everywhere:
final equity, total P&L and return %, dollar profit factor, dollar max
drawdown, a P&L column in the trade table, and an equity chart drawn in $.
Without the capital sim, the equity chart stays in cumulative R.

## Monte Carlo

A second pure post-process over the trade R-multiples (no re-run). Each
simulated **account** is a full run: the observed outcomes are resampled,
re-sized through the same `simulate_capital` math, and followed trade by trade
until it reaches the target or blows (breaches the drawdown limit) - whichever
happens first:

- `iterations` — how many **accounts** to test (default 1,000; cap 10,000).
- `sample_mode` — `bootstrap` (default) draws the R values with replacement
  (random trades; tests how robust the edge is to a lucky or unlucky mix of
  winners/losers); `shuffle` randomizes the trade ORDER instead (same trades,
  tests sequence luck / drawdown depth). `both` runs the two independently.
- `seed` — optional integer; when set, the run is fully reproducible
  (`np.random.default_rng(seed)`); when omitted it is random.
- `initial_capital` / `risk_pct` / `sizing` — the same bankroll parameters as
  the capital simulation (fixed vs compounding risk basis).
- `target_pct` — the account return you need back; accounts that reach it
  first are counted as **passed**.
- `max_dd_pct` — the deepest drawdown you would tolerate; accounts that
  breach it first are counted as **blown**.

The response reports per mode: percentiles (5/25/50/75/95) of the stopped
final return % (snapped to the challenge boundaries), of the stopped max
drawdown %, and of trades-to-stop (how many trades accounts ran before
pausing), the actual (realized) path paused the same way (so if the real
account would have passed, its reported result is exactly `+target_pct`),
`p_loss`, a per-trade histogram of how many trades each account took before
pausing, split by outcome (one bin per trade: `[[center, passed, blown,
neither], ...]`), and account outcomes: `total_accounts`, `passed_accounts`,
`blown_accounts`, `pass_rate`, `risk_of_ruin`, plus `avg_trades_to_target` /
`avg_trades_to_blow` (mean number of trades it took among accounts that
passed / blew). Every account is a challenge and stops at its FIRST boundary:
passing accounts are sized at exactly `+target_pct` (the challenge pays the
target, not more), blown accounts at exactly `-max_dd_pct` (the account is
halted at its max loss), and the remainder ran all trades without hitting
either. No account can report a return past its own boundary. Also note
shuffle preserves the sum of R, so for fixed sizing its drawdown spread is
the informative output; bootstrap's goal is edge robustness.

## Per-trade chart export

The **Export charts (.zip)** button next to the trade log downloads one PNG
per closed trade, packed into a ZIP. Each chart shows that trade's candle
window in the dashboard's dark palette: the opening-range band (high/low),
the breakout bar, the session-open and search-deadline markers, the entry,
initial SL, moved SL (when the ladder advanced) and TP levels, and an exit
marker, plus a header with date, direction, entry mode, entry→exit times, exit
reason and the R outcome. The ZIP also contains a `trades.csv` manifest.

- Like the capital sim and Monte Carlo, charts are a pure post-process over
  the loaded candles + trade log — the backtest is never re-run.
- Generated in a background thread into a temp file (progress is pollable via
  `/api/backtest/images/{job_id}/status`), streamed on download, then
  **unlinked immediately after the response is sent**. Nothing persists on the
  server: a second download 404s, stale undownloaded exports are swept after
  30 minutes, and anything left on disk is cleared at app startup.
- Exports are capped at 5,000 charts (the manifest covers the full log).
- Deleting a dataset drops the backtest results and chart exports that
  depended on it.

## Verified behavior (tested during build)

- SL at `sl_pct_of_range = 0.5` lands exactly on the range midpoint;
  `1.0` lands exactly on the boundary opposite the breakout side.
- `tp_rr = 1.0` produces exactly ±1.0 R outcomes on TP/SL hits (before
  execution costs).
- `sl_ladder` (default `[[0.5, -0.5]]`) moves the
  stop step by step as price advances. Each step is `[trigger_R, sl_R]`: when
  price reaches `trigger_R` (R = risk distance from entry), the stop moves to
  `sl_R` (negative = below entry, 0 = breakeven, positive = locked profit).
  R is always the ORIGINAL risk distance, fixed for the whole trade — a moved
  stop never becomes the reference for later steps. Steps fire in ascending
  trigger order (order of entry doesn't matter). The moved stop applies
  from the next candle; a bar touching both a trigger
  and the old stop counts as the old stop (conservative), and a bar racing
  across several triggers applies the highest reached step. Empty = no moves.
- Limit-mode entries fill exactly at the broken range boundary.
- Trades left open are force-closed at `session_max_hours` (default 1h)
  with `exit_reason = "timeout"`.
- Malformed OHLC rows (e.g. high below open/close) are rejected on upload
  with a clear error rather than silently accepted.
