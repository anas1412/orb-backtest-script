"""Server-side per-trade chart images for the Asia-range backtester.

Renders one candlestick chart per closed trade (opening-range band, breakout
bar, entry / SL / moved-stop / TP levels, exit marker) as a PNG, then packs
them into a ZIP written progressively to disk so memory stays bounded. A pure
post-process over the loaded candles + trade log: nothing in app/engine is
touched and the backtest is never re-run.

Images are drawn with matplotlib's headless Agg backend, styled to match the
dashboard palette (app/static/style.css :root).
"""
from __future__ import annotations

import csv
import io
import zipfile
from datetime import timedelta
from pathlib import Path
from typing import Callable

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd
from matplotlib.patches import Rectangle

from app.engine.backtester import Trade
from app.engine.strategy import Params

# Bounds for a single export: a hard cap keeps CPU and zip size finite.
MAX_EXPORT_TRADES = 5000
LEAD_MINUTES = 10      # context bars before session open
TAIL_BARS = 18         # context bars after the exit
FIG_SIZE = (12.8, 7.2)
DPI = 110

# Dashboard palette (style.css :root)
BG = "#0c0f14"
PANEL = "#12161d"
BORDER = "#232937"
TEXT = "#e8e6df"
DIM = "#8890a0"
FAINT = "#68718a"
GOLD = "#e0b94a"
POS = "#6bc593"
NEG = "#d97267"


def _window(df: pd.DataFrame, trade: Trade) -> pd.DataFrame:
    """Slice the candles covering one trade: lead-in, session, exit tail."""
    start = trade.session_open - timedelta(minutes=LEAD_MINUTES)
    end = trade.exit_time + timedelta(minutes=TAIL_BARS)
    win = df.loc[(df["timestamp"] >= start) & (df["timestamp"] <= end)].sort_values("timestamp")
    if len(win) == 0:  # defensive: dataset ended before exit, widen around entry
        mid = trade.entry_time - timedelta(minutes=30)
        win = df.loc[
            (df["timestamp"] >= mid) & (df["timestamp"] <= trade.entry_time + timedelta(minutes=30))
        ].sort_values("timestamp")
    return win


def _bar_index(times: pd.Series, target: pd.Timestamp, default: int) -> int:
    if len(times) == 0:
        return default
    idx = times.searchsorted(target)
    idx = min(idx, len(times) - 1)
    return int(idx)


def _filename(trade: Trade) -> str:
    hm = trade.entry_time.strftime("%H%M")
    sign = "+" if trade.r_multiple >= 0 else ""
    return f"{trade.day:%Y-%m-%d}_{hm}_{trade.direction}_{sign}{trade.r_multiple:.2f}R.png"


def render_trade_chart(df: pd.DataFrame, trade: Trade, params: Params) -> bytes:
    """Draw one trade's candles + levels and return the PNG bytes."""
    win = _window(df, trade)
    n = len(win)
    times = win["timestamp"]

    fig, ax = plt.subplots(figsize=FIG_SIZE, dpi=DPI, facecolor=BG)
    ax.set_facecolor(BG)

    if n > 0:
        low = win["low"].to_numpy(dtype=float)
        high = win["high"].to_numpy(dtype=float)
        o = win["open"].to_numpy(dtype=float)
        c = win["close"].to_numpy(dtype=float)
        up = c >= o

        # Wicks then bodies so bodies sit on top.
        for i in range(n):
            col = POS if up[i] else NEG
            ax.plot([i, i], [low[i], high[i]], color=col, linewidth=0.8, alpha=0.85, solid_capstyle="butt", zorder=2)
        for i in range(n):
            col = POS if up[i] else NEG
            if o[i] == c[i]:
                ax.plot([i - 0.35, i + 0.35], [o[i], o[i]], color=col, linewidth=1.4, zorder=3)
            else:
                h = abs(c[i] - o[i])
                ax.add_patch(Rectangle(
                    (i - 0.35, min(o[i], c[i])), 0.7, h,
                    facecolor=col, edgecolor=col, linewidth=0.4, zorder=3,
                ))

        # Opening-range band.
        if trade.range_high is not None and trade.range_low is not None:
            ax.axhspan(trade.range_low, trade.range_high, color=GOLD, alpha=0.07, zorder=1)
            ax.axhline(trade.range_high, color=GOLD, linewidth=0.7, linestyle=(0, (6, 4)), alpha=0.55, zorder=1)
            ax.axhline(trade.range_low, color=GOLD, linewidth=0.7, linestyle=(0, (6, 4)), alpha=0.55, zorder=1)
            ax.text(0.3, trade.range_high, "RANGE HIGH", fontsize=8, color=GOLD, alpha=0.9,
                    va="bottom", ha="left", zorder=5)
            ax.text(0.3, trade.range_low, "RANGE LOW", fontsize=8, color=GOLD, alpha=0.9,
                    va="top", ha="left", zorder=5)

        # Session open / search deadline vertical markers.
        if params is not None:
            range_end = trade.session_open + timedelta(minutes=params.range_minutes)
            deadline = range_end + timedelta(minutes=params.breakout_search_minutes)
            i_open = _bar_index(times, trade.session_open, 0)
            i_dead = _bar_index(times, deadline, n - 1)
            ax.axvline(i_open, color=GOLD, linewidth=0.9, alpha=0.7, zorder=1)
            ax.axvline(i_dead, color=FAINT, linewidth=0.9, linestyle=(0, (3, 3)), alpha=0.9, zorder=1)
            ax.text(i_open + 0.15, 0.98, "SESSION OPEN", transform=ax.get_xaxis_transform(),
                    fontsize=7.5, color=GOLD, alpha=0.9, va="top", zorder=5)
            ax.text(i_dead + 0.15, 0.98, "DEADLINE", transform=ax.get_xaxis_transform(),
                    fontsize=7.5, color=FAINT, va="top", zorder=5)

        # Breakout bar highlight.
        if trade.breakout_time is not None:
            i_bo = _bar_index(times, trade.breakout_time, 0)
            ax.axvspan(i_bo - 0.5, i_bo + 0.5, color=GOLD, alpha=0.06, zorder=1)
            ax.text(i_bo, 0.015, "BREAKOUT", transform=ax.get_xaxis_transform(),
                    fontsize=7.5, color=GOLD, alpha=0.95, va="bottom", ha="center", zorder=5)

    # Level lines + labels (x-coords measured off the last candle).
    last_x = max(n - 1, 0)
    label_x = last_x + 1.1

    def level(price, color, style, text, side):
        ax.axhline(price, color=color, linewidth=1.0, linestyle=style, alpha=0.9, zorder=4)
        ax.text(label_x, price, text, fontsize=8.5, color=color,
                va="center", ha="left", zorder=5)

    level(trade.entry_price, GOLD, (0, (1, 1)), f"ENTRY {trade.entry_price:.2f}", "right")
    level(trade.tp_price, POS, (0, (5, 3)), f"TP {trade.tp_price:.2f}", "right")
    if trade.sl_moved and trade.sl_final != trade.sl_price:
        level(trade.sl_price, NEG, (0, (5, 3)), f"SL {trade.sl_price:.2f}", "right")
        level(trade.sl_final, NEG, (0, (2, 2)), f"SL→ {trade.sl_final:.2f}", "right")
    else:
        level(trade.sl_price, NEG, (0, (5, 3)), f"SL {trade.sl_price:.2f}", "right")

    # Entry + exit markers on their bars.
    if n > 0:
        i_entry = _bar_index(times, trade.entry_time, 0)
        i_exit = _bar_index(times, trade.exit_time, min(n - 1, i_entry + 1))
        ax.scatter([i_entry], [trade.entry_price], marker="^" if trade.direction == "long" else "v",
                   s=70, color=GOLD, edgecolor=BG, linewidths=0.6, zorder=6)
        exit_col = POS if trade.r_multiple > 0 else (NEG if trade.r_multiple < 0 else DIM)
        ax.scatter([i_exit], [trade.exit_price], marker="X", s=55, color=exit_col,
                   edgecolor=BG, linewidths=0.6, zorder=6)

    # Price padding so every level is inside the frame.
    level_vals = [trade.sl_price, trade.sl_final, trade.entry_price, trade.exit_price, trade.tp_price]
    lo = min(level_vals)
    hi = max(level_vals)
    if n > 0:
        lo = min(lo, float(win["low"].min()))
        hi = max(hi, float(win["high"].max()))
    pad = (hi - lo) * 0.06 or 1.0
    ax.set_ylim(lo - pad, hi + pad)
    ax.set_xlim(-0.5, last_x + 5.0)

    # X ticks: ~10 evenly spread bars, HH:MM (UTC).
    if n > 0:
        step = max(1, n // 10)
        tick_i = list(range(0, n, step))
        ax.set_xticks(tick_i)
        ax.set_xticklabels([times.iloc[i].strftime("%H:%M") for i in tick_i])
    for s in ax.spines.values():
        s.set_color(BORDER)
    ax.tick_params(colors=DIM, labelsize=8.5, length=3)
    ax.yaxis.set_major_formatter(lambda v, _pos: f"{v:.2f}")
    ax.grid(axis="y", color=BORDER, linewidth=0.5, alpha=0.55)

    # Title: date · direction · entry mode · window · exit reason · R.
    is_win = trade.r_multiple > 0
    r_color = POS if is_win else (NEG if trade.r_multiple < 0 else DIM)
    entry_txt = trade.entry_time.strftime("%H:%M")
    exit_txt = trade.exit_time.strftime("%H:%M")
    title = (f"{trade.day:%a %d %b %Y}  ·  {trade.direction.upper()} {trade.entry_mode.upper()}  ·  "
             f"{entry_txt}→{exit_txt} UTC  ·  {trade.exit_reason.upper()}")
    fig.text(0.015, 0.965, title, color=TEXT, fontsize=12.5, fontweight="bold", va="top")
    fig.text(0.985, 0.965, f"{trade.r_multiple:+.2f}R", color=r_color, fontsize=13,
             fontweight="bold", ha="right", va="top")

    buf = io.BytesIO()
    fig.savefig(buf, format="png", facecolor=BG, bbox_inches="tight", pad_inches=0.25)
    plt.close(fig)
    return buf.getvalue()


def _manifest_csv(trades: list[Trade], cap: int) -> str:
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow([
        "date", "direction", "entry_mode", "entry_time_utc", "entry_price",
        "range_high", "range_low", "sl_price", "sl_final", "tp_price",
        "exit_time_utc", "exit_price", "exit_reason", "r_multiple",
    ])
    for t in trades[:cap]:
        w.writerow([
            t.day.date().isoformat(), t.direction, t.entry_mode,
            t.entry_time.isoformat(), f"{t.entry_price:.4f}",
            f"{t.range_high:.4f}", f"{t.range_low:.4f}",
            f"{t.sl_price:.4f}", f"{t.sl_final:.4f}", f"{t.tp_price:.4f}",
            t.exit_time.isoformat(), f"{t.exit_price:.4f}", t.exit_reason,
            f"{t.r_multiple:.4f}",
        ])
    return out.getvalue()


def build_trade_images_zip(
    df: pd.DataFrame,
    trades: list[Trade],
    params: Params,
    out_path: Path,
    progress_cb: Callable[[int, int], None] | None = None,
) -> int:
    """Render every trade to PNG and write a ZIP to `out_path` on disk.

    Returns the number of images written. `progress_cb(done, total)` is called
    after each image. The capped count is respected: trades beyond
    `MAX_EXPORT_TRADES` are not rendered (the manifest lists what was exported).
    """
    cap = len(trades) if len(trades) <= MAX_EXPORT_TRADES else MAX_EXPORT_TRADES
    with zipfile.ZipFile(out_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("trades.csv", _manifest_csv(trades, cap))
        for i, trade in enumerate(trades[:cap]):
            png = render_trade_chart(df, trade, params)
            zf.writestr(_filename(trade), png)
            if progress_cb is not None:
                progress_cb(i + 1, cap)
    return cap
