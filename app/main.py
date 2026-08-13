from __future__ import annotations

import asyncio
import json
import queue
import re
import threading
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional

import pandas as pd
from fastapi import FastAPI, File, UploadFile, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field
import io

from app.engine.data_loader import (
    load_ohlcv_csv, DataValidationError, LoadedData
)
from app.engine.strategy import Params
from app.engine.backtester import run_backtest, BacktestResult
from app.engine.stats import compute_stats, trades_to_dataframe, equity_curve

BASE_DIR = Path(__file__).resolve().parent


def _normalize_date(value: str, field: str) -> str:
    """Accept DD/MM/YYYY or YYYY-MM-DD, return YYYY-MM-DD. Raise 400 otherwise."""
    v = value.strip()
    m = re.fullmatch(r"(\d{1,2})/(\d{1,2})/(\d{4})", v)
    if m:
        d, mo, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
        try:
            return datetime(y, mo, d).date().isoformat()
        except ValueError:
            raise HTTPException(status_code=400, detail=f"{field}: '{v}' is not a valid date (use DD/MM/YYYY)")
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", v):
        return v
    raise HTTPException(status_code=400, detail=f"{field}: could not parse '{v}' (use DD/MM/YYYY or YYYY-MM-DD)")

app = FastAPI(title="Gold Asia Range Breakout Backtester")
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
templates = Jinja2Templates(directory=BASE_DIR / "templates")


@app.middleware("http")
async def no_cache_static_assets(request: Request, call_next):
    """Static JS/CSS change often during development; force revalidation so a
    cached page never runs stale assets."""
    response = await call_next(request)
    if request.url.path.startswith("/static/"):
        response.headers["Cache-Control"] = "no-cache"
    return response

# In-memory store: single-user local tool, simple dict is enough.
DATA_STORE: dict[str, LoadedData] = {}
RESULT_STORE: dict[str, BacktestResult] = {}


class BacktestParams(BaseModel):
    dataset_id: str
    session_open_utc: str = "00:00"
    range_minutes: int = Field(15, ge=1, le=240)
    breakout_search_minutes: int = Field(180, ge=1, le=480)
    entry_mode: str = "market"
    sl_pct_of_range: float = Field(0.5, ge=0.0, le=5.0)
    tp_rr: float = Field(2.0, gt=0.0)
    spread_pips: float = Field(0.0, ge=0.0)
    slippage_pips: float = Field(0.0, ge=0.0)
    session_max_hours: float = Field(20.0, gt=0.0)
    skip_weekends: bool = True
    start_date: Optional[str] = None
    end_date: Optional[str] = None


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse(request=request, name="index.html")


@app.post("/api/data/upload")
async def upload_data(
    file: UploadFile = File(...),
    symbol: str = Form("XAUUSD"),
    source_tz: str = Form("auto"),
):
    try:
        content = await file.read()
        loaded = load_ohlcv_csv(content, symbol=symbol, source_tz=source_tz, source_name=file.filename)
    except DataValidationError as e:
        raise HTTPException(status_code=400, detail=str(e))

    dataset_id = str(uuid.uuid4())
    DATA_STORE[dataset_id] = loaded

    return {
        "dataset_id": dataset_id,
        "symbol": loaded.symbol,
        "source": loaded.source,
        "tz_note": loaded.tz_note,
        "rows": len(loaded.df),
        "start": loaded.df["timestamp"].min().isoformat(),
        "end": loaded.df["timestamp"].max().isoformat(),
    }


@app.get("/api/data/status/{dataset_id}")
async def data_status(dataset_id: str):
    loaded = DATA_STORE.get(dataset_id)
    if loaded is None:
        raise HTTPException(status_code=404, detail="Dataset not found")
    return {
        "dataset_id": dataset_id,
        "symbol": loaded.symbol,
        "source": loaded.source,
        "tz_note": loaded.tz_note,
        "rows": len(loaded.df),
        "start": loaded.df["timestamp"].min().isoformat(),
        "end": loaded.df["timestamp"].max().isoformat(),
    }


@app.post("/api/backtest/run")
async def run_backtest_endpoint(p: BacktestParams):
    loaded = DATA_STORE.get(p.dataset_id)
    if loaded is None:
        raise HTTPException(status_code=404, detail="Dataset not found. Upload data first.")

    # Optional date window (DD/MM/YYYY or YYYY-MM-DD, inclusive days, UTC).
    df = loaded.df
    if p.start_date or p.end_date:
        df = loaded.df.copy()
        if p.start_date:
            start_utc = pd.Timestamp(_normalize_date(p.start_date, "Start date") + " 00:00:00", tz="UTC")
            df = df[df["timestamp"] >= start_utc]
        if p.end_date:
            end_utc = pd.Timestamp(_normalize_date(p.end_date, "End date") + " 23:59:59", tz="UTC")
            df = df[df["timestamp"] <= end_utc]
        if len(df) == 0:
            raise HTTPException(status_code=400, detail="No bars exist in the selected date window (UTC).")

    params = Params(
        session_open_utc=p.session_open_utc,
        range_minutes=p.range_minutes,
        breakout_search_minutes=p.breakout_search_minutes,
        entry_mode=p.entry_mode,
        sl_pct_of_range=p.sl_pct_of_range,
        tp_rr=p.tp_rr,
        spread_pips=p.spread_pips,
        slippage_pips=p.slippage_pips,
        session_max_hours=p.session_max_hours,
        skip_weekends=p.skip_weekends,
    )

    # Stream the run as NDJSON: one {"type":"progress","done","total"} line per
    # trading day, then a final {"type":"done","result":{...}} (or error). The
    # engine runs in a daemon thread; the async generator drains its queue.
    q: queue.Queue = queue.Queue()

    def on_progress(done: int, total: int) -> None:
        q.put({"type": "progress", "done": done, "total": total})

    def worker() -> None:
        try:
            result = run_backtest(df, params, progress_cb=on_progress)
            job_id = str(uuid.uuid4())
            RESULT_STORE[job_id] = result
            q.put({"type": "done", "result": _serialize_result(result, job_id, loaded, p)})
        except Exception as exc:  # defensive: surface any engine failure to the UI
            q.put({"type": "error", "detail": str(exc)})

    async def stream() -> None:
        t = threading.Thread(target=worker, daemon=True)
        t.start()
        while True:
            await asyncio.sleep(0.05)
            try:
                msg = q.get_nowait()
            except queue.Empty:
                continue
            yield json.dumps(msg, default=str) + "\n"
            if msg["type"] in ("done", "error"):
                return

    return StreamingResponse(
        stream(),
        media_type="application/x-ndjson",
        headers={"X-Accel-Buffering": "no", "Cache-Control": "no-cache"},
    )


def _serialize_result(result: BacktestResult, job_id: str, loaded: LoadedData, p: BacktestParams) -> dict:
    stats = compute_stats(result)
    trades_df = trades_to_dataframe(result)
    curve = equity_curve(result)

    breakdown_direction = {
        "long": {"trades": stats.long_trades, "win_rate": stats.long_win_rate},
        "short": {"trades": stats.short_trades, "win_rate": stats.short_win_rate},
    }

    dow_breakdown = {}
    if len(trades_df) > 0:
        tmp = trades_df.copy()
        tmp["dow"] = pd.to_datetime(tmp["date"]).dt.day_name()
        for dow, grp in tmp.groupby("dow"):
            dow_breakdown[dow] = {
                "trades": len(grp),
                "win_rate": round((grp["r_multiple"] > 0).mean(), 4),
                "total_r": round(grp["r_multiple"].sum(), 4),
            }

    return {
        "job_id": job_id,
        "symbol": loaded.symbol,
        "params": p.dict(),
        "stats": {
            "total_trades": stats.total_trades,
            "wins": stats.wins,
            "losses": stats.losses,
            "timeouts": stats.timeouts,
            "win_rate": round(stats.win_rate, 4),
            "total_r": round(stats.total_r, 4),
            "average_r": round(stats.average_r, 4),
            "profit_factor": round(stats.profit_factor, 4) if stats.profit_factor is not None else None,
            "max_drawdown_r": round(stats.max_drawdown_r, 4),
            "expectancy_r": round(stats.expectancy_r, 4),
            "longest_win_streak": stats.longest_win_streak,
            "longest_loss_streak": stats.longest_loss_streak,
        },
        "no_trade_days": len(result.no_trade_days),
        "breakdown_direction": breakdown_direction,
        "breakdown_day_of_week": dow_breakdown,
        "equity_curve": curve,
        "trades": trades_df.to_dict(orient="records"),
    }


@app.get("/api/backtest/export/{job_id}")
async def export_trades_csv(job_id: str):
    result = RESULT_STORE.get(job_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Result not found")
    df = trades_to_dataframe(result)
    buf = io.StringIO()
    df.to_csv(buf, index=False)
    buf.seek(0)
    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename=trades_{job_id}.csv"},
    )
