# Asia Range Breakout — Gold (XAUUSD) Backtester

A Python + FastAPI backtesting tool for an Asia-session opening-range breakout strategy on Gold, M1 timeframe.

## How to run

The workflow **Start application** runs the server:

```
uvicorn app.main:app --host 0.0.0.0 --port 5000
```

Open the webview at port 5000. No environment variables or API keys are required.

## Usage

1. **Upload real data** — upload a CSV with columns `timestamp, open, high, low, close`. Set the "Raw timestamp TZ" field to the source timezone if timestamps are not already UTC.
3. **Configure strategy** — adjust session open time, range minutes, entry mode (market/limit), SL/TP, spread/slippage.
4. **Run backtest** — results stream in real time; stats, equity curve, and trade-log export are shown on completion.

## Project layout

```
app/
  engine/
    data_loader.py   # CSV loading, timezone normalization
    session.py       # opening-range window computation, trading-day iteration
    strategy.py      # breakout detection, entry modes, SL/TP calculation
    backtester.py    # main day-by-day simulation loop
    stats.py         # summary statistics, trade log export, equity curve
  main.py            # FastAPI app + API routes
  templates/index.html
  static/style.css
  static/app.js
```

## User preferences

- Keep the existing project structure and stack (Python, FastAPI, vanilla JS dashboard).
