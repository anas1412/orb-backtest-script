# Asia Range Breakout — Gold (XAUUSD) Backtester

A Python + FastAPI backtesting tool for an Asia-session opening-range breakout
strategy on Gold, M1 timeframe. All logic operates in UTC internally.

## What it does

1. Marks the high/low of the first N minutes after a configurable session
   open time (UTC), default 00:00 UTC / 15 minutes.
2. Watches for the first M1 candle to close outside that range within a
   configurable search window (default 180 minutes).
3. Enters via market order (next bar's open) or limit order (resting at the
   broken boundary, expiring at the search-window deadline).
4. Places the stop loss at the range midpoint by default (adjustable), and
   take profit at a configurable R multiple.
5. Force-closes any trade still open after a configurable max duration.
6. Reports full statistics, an equity curve, breakdowns by direction and
   day-of-week, and a downloadable trade log.

## Project layout

```
app/
  engine/
    data_loader.py   # CSV loading + UTC timezone normalization
    session.py        # opening-range window computation, trading-day iteration
    strategy.py        # breakout detection, entry modes, SL/TP calculation
    backtester.py      # main day-by-day simulation loop
    stats.py            # summary statistics, trade log export, equity curve
  main.py                # FastAPI app + API routes
  templates/index.html    # dashboard UI
  static/style.css         # dashboard styling
  static/app.js              # dashboard behavior (calls the API, renders results)
```

The engine (`app/engine/`) is framework-agnostic — it can be imported and
used directly in a script or notebook without FastAPI.

## Running it

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

- Upload a CSV with columns `timestamp, open, high, low, close`
  (case-insensitive; common aliases like `date`/`time`/`o`/`h`/`l`/`c` are
  also recognized). If the file's raw timestamps are not already UTC, set
  the "Raw timestamp TZ" field to the correct source timezone (e.g. `EET`,
  `America/New_York`) before uploading — the loader will not guess this.

Free M1 gold data sources to try: HistData.com, Dukascopy, MetaTrader 5's
History Center, or a Kaggle-hosted historical gold dataset. Verify each
source's raw timestamp timezone before uploading.

## API

- `POST /api/data/upload` — upload a real M1 CSV
- `GET /api/data/status/{dataset_id}` — check a loaded dataset
- `POST /api/backtest/run` — run a backtest with a given parameter set
- `GET /api/backtest/export/{job_id}` — download the trade log as CSV

## Verified behavior (tested during build)

- SL at `sl_pct_of_range = 0.5` lands exactly on the range midpoint;
  `1.0` lands exactly on the boundary opposite the breakout side.
- `tp_rr = 1.0` produces exactly ±1.0 R outcomes on TP/SL hits (before
  execution costs).
- Limit-mode entries fill exactly at the broken range boundary.
- Trades left open are force-closed at `session_max_hours` (default 20h)
  with `exit_reason = "timeout"`.
- Malformed OHLC rows (e.g. high below open/close) are rejected on upload
  with a clear error rather than silently accepted.
