"""
Data loading and validation for M1 OHLCV data.

Design intent (per plan.md):
- The engine only ever works with UTC timestamps internally.
- Loaders are pluggable: each one returns a standardized DataFrame.
- If a source's timestamps are not already UTC, the caller MUST tell us
  the source's raw offset/timezone via `source_tz`. We never guess.
"""
from __future__ import annotations

import io
from dataclasses import dataclass
from datetime import timedelta, timezone

import numpy as np
import pandas as pd

REQUIRED_COLUMNS = ["timestamp", "open", "high", "low", "close"]

# FX/metals market calendar anchors (UTC): last bar before the weekend close
# (~21:00 UTC on Friday, so the last Friday bar prints 20-21:00) and the
# Sunday reopen, which is 22:00 UTC exactly for gold across essentially all
# brokers. The offset is solved from the reopen hour, then verified against
# the Friday close hour.
WEEKEND_REOPEN_HOUR_UTC = 22
WEEKEND_CLOSE_HOURS_UTC = (20, 21)
BROKER_OFFSET_RANGE = range(-5, 6)  # realistic broker server times for FX/metals


class DataValidationError(ValueError):
    """Raised when loaded data doesn't meet the engine's requirements."""


def detect_utc_offset(ts: pd.Series) -> timedelta | None:
    """
    Infer the offset of naive timestamps from weekend gaps.

    The raw timestamps' hourly position of the Friday close / Sunday open is
    compared against the known ~21:00 UTC close and ~22:00 UTC reopen. Every
    consistent weekend gap votes for an offset; the majority wins. Returns
    None when the data carries no reliable signal (no weekend gaps, or
    conflicting votes).
    """
    if len(ts) < 2:
        return None
    t = ts.sort_values().reset_index(drop=True)
    gaps = t.diff()
    # A diff > 20h lands on the bar AFTER the jump (the Sunday reopen), so the
    # day-of-week checks happen inside the loop on the before/after bars.
    weekend_idx = gaps.index[gaps > pd.Timedelta(hours=20)]

    votes: list[int] = []
    for i in weekend_idx:
        before, after = t.iloc[i - 1], t.iloc[i]
        if before.dayofweek not in (4, 5):  # close bar is a Friday (or Saturday for UTC+4/+5 servers)
            continue
        if after.dayofweek not in (6, 0):  # resumed on a Sunday or Monday
            continue
        # Solve the offset from the Sunday reopen hour (22:00 UTC exactly)...
        off = (after.hour - WEEKEND_REOPEN_HOUR_UTC) % 24
        if off > 12:
            off -= 24
        if off not in BROKER_OFFSET_RANGE:
            continue
        # ...then verify it also puts the Friday close on 20-21:00 UTC.
        close_h = (before.hour - off) % 24
        if close_h in WEEKEND_CLOSE_HOURS_UTC:
            votes.append(off)

    if not votes:
        return None
    unique = set(votes)
    best = max(unique, key=votes.count)
    n = votes.count(best)
    if len(unique) > 1 and n < max(2, int(round(len(votes) * 0.6))):
        return None  # conflicting signals; don't guess
    return timedelta(hours=best)


@dataclass
class LoadedData:
    df: pd.DataFrame          # columns: timestamp (UTC, tz-aware), open, high, low, close
    symbol: str
    source: str
    tz_note: str              # human-readable note on how timezone was resolved


def _standardize_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Map common column-name variants (from different free data sources) to our schema."""
    lower_cols = {c.lower().strip(): c for c in df.columns}

    date_col = next((c for c in df.columns if c.lower().strip() == "date"), None)
    time_col = next((c for c in df.columns if c.lower().strip() == "time"), None)
    hour_col = next((c for c in df.columns if c.lower().strip() == "hour"), None)
    minute_col = next((c for c in df.columns if c.lower().strip() in ("minute", "min")), None)

    # Some sources split the timestamp into date + time, or date + hour + minute.
    # Merge those BEFORE the alias rename, or the rename consumes "date"/"time"
    # and the time-of-day part is lost.
    if date_col is not None and "timestamp" not in df.columns:
        if time_col is not None:
            df["timestamp"] = df[date_col].astype(str) + " " + df[time_col].astype(str)
        elif hour_col is not None and minute_col is not None:
            df["timestamp"] = (
                df[date_col].astype(str) + " "
                + df[hour_col].astype(str).str.zfill(2) + ":"
                + df[minute_col].astype(str).str.zfill(2)
            )
        if "timestamp" in df.columns:
            for src in (date_col, time_col, hour_col, minute_col):
                if src is not None and src != "timestamp":
                    df = df.drop(columns=[src])

    colmap = {}
    lower_cols = {c.lower().strip(): c for c in df.columns}

    aliases = {
        "timestamp": ["timestamp", "datetime", "date_time", "time", "date"],
        "open": ["open", "o"],
        "high": ["high", "h"],
        "low": ["low", "l"],
        "close": ["close", "c"],
        "volume": ["volume", "vol", "v"],
    }
    for target, options in aliases.items():
        for opt in options:
            if opt in lower_cols:
                colmap[lower_cols[opt]] = target
                break

    df = df.rename(columns=colmap)

    return df


def load_ohlcv_csv(
    file_bytes: bytes,
    symbol: str,
    source_tz: str = "UTC",
    source_name: str = "uploaded_csv",
    delimiter: str | None = None,
) -> LoadedData:
    """
    Load M1 OHLCV data from raw CSV bytes.

    Parameters
    ----------
    file_bytes: raw CSV content
    symbol: instrument label, e.g. "XAUUSD"
    source_tz: the timezone the RAW timestamps in the file are labeled in
               (e.g. "UTC", "EST", "America/New_York", "Etc/GMT-2" for
               "EET-like" broker server time). This is REQUIRED reasoning,
               not a guess -- caller must supply the correct value for
               the chosen data source. Defaults to "UTC" only because
               that is the safest fail-closed assumption to validate
               against, not because it's likely correct for every source.
               Pass "auto" to infer the offset from weekend close/open
               gaps instead (refuses with an error when the data has no
               reliable signal).
    source_name: label for reporting, e.g. "histdata.com"
    delimiter: override CSV delimiter auto-detection if needed
    """
    try:
        raw = pd.read_csv(io.BytesIO(file_bytes), sep=delimiter, engine="python")
    except Exception as exc:
        raise DataValidationError(f"Could not parse CSV: {exc}") from exc

    df = _standardize_columns(raw)

    missing = [c for c in REQUIRED_COLUMNS if c not in df.columns]
    if missing and raw.shape[1] >= 5:
        # MetaTrader-style exports have no header row and a structure like:
        #   "2026-05-01 20:41, 4611.278, 4611.998, 4610.578, 4611.818, 1"
        # i.e. the first value of the first row parses as a datetime. Try again
        # with positional column names instead of rejecting the file.
        first_cell = str(raw.iloc[0, 0]).strip()
        if not pd.isna(pd.to_datetime(first_cell, errors="coerce")):
            raw2 = pd.read_csv(io.BytesIO(file_bytes), sep=delimiter, engine="python", header=None)
            names = ["timestamp", "open", "high", "low", "close"]
            if raw2.shape[1] >= 6:
                names.append("volume")
            raw2.columns = names
            df = _standardize_columns(raw2)
            missing = [c for c in REQUIRED_COLUMNS if c not in df.columns]

    if missing:
        raise DataValidationError(
            f"CSV is missing required columns {missing}. "
            f"Found columns: {list(raw.columns)}"
        )

    # Parse timestamp. Some providers mix plain "YYYY-MM-DD HH:MM:SS" rows with
    # ISO-8601 rows ("2025-11-17T14:02:00Z", e.g. when an export was appended
    # to an existing file). pandas 3.x returns NaT for the minority format in a
    # mixed column, so normalize ISO variants to the plain naive form first:
    # "T" -> " ", drop the trailing "Z" (Z merely labels UTC, which the
    # source_tz / "auto" logic below resolves).
    ts_raw = df["timestamp"]
    if pd.api.types.is_string_dtype(ts_raw):
        ts_raw = ts_raw.astype(str).str.replace("T", " ", regex=False).str.rstrip("Zz")
    ts = pd.to_datetime(ts_raw, errors="coerce", utc=False)
    if ts.isna().any():
        n_bad = int(ts.isna().sum())
        raise DataValidationError(
            f"{n_bad} row(s) had a timestamp that could not be parsed."
        )

    if ts.dt.tz is not None:
        # Already tz-aware; convert straight to UTC, ignore source_tz.
        ts_utc = ts.dt.tz_convert("UTC")
        tz_note = "Timestamps were already timezone-aware; converted directly to UTC."
    else:
        # Naive timestamps: the caller either names the source timezone, or
        # opts into auto-detection ("auto") anchored on the weekend gap.
        src = str(source_tz).strip()
        if src.lower() in ("auto", ""):
            offset = detect_utc_offset(ts)
            if offset is None:
                raise DataValidationError(
                    "Could not auto-detect the timestamps' timezone from the data "
                    "(no reliable weekend close/open gaps were found). Set the "
                    "'Raw timestamp TZ' field to the source's actual timezone, "
                    "e.g. 'UTC', 'EET', or 'Etc/GMT-3', and retry."
                )
            ts_utc = ts.dt.tz_localize(timezone(offset)).dt.tz_convert("UTC")
            off_h = int(offset.total_seconds() // 3600)
            label = "UTC" if off_h == 0 else f"UTC{off_h:+d}h"
            tz_note = (
                f"Auto-detected from weekend gaps: raw timestamps appear to be "
                f"{label}; normalized to UTC."
            )
        else:
            try:
                ts_utc = ts.dt.tz_localize(source_tz).dt.tz_convert("UTC")
            except Exception as exc:
                raise DataValidationError(
                    f"Could not localize naive timestamps using source_tz='{source_tz}': {exc}"
                ) from exc
            tz_note = f"Naive timestamps localized as '{source_tz}' then converted to UTC."

    out = pd.DataFrame({
        "timestamp": ts_utc,
        "open": pd.to_numeric(df["open"], errors="coerce"),
        "high": pd.to_numeric(df["high"], errors="coerce"),
        "low": pd.to_numeric(df["low"], errors="coerce"),
        "close": pd.to_numeric(df["close"], errors="coerce"),
    })

    if "volume" in df.columns:
        out["volume"] = pd.to_numeric(df["volume"], errors="coerce")

    if out[["open", "high", "low", "close"]].isna().any().any():
        raise DataValidationError("Some OHLC values could not be parsed as numbers.")

    # Sanity: high >= max(open,close,low), low <= min(open,close,high)
    bad_bars = out[
        (out["high"] < out[["open", "close", "low"]].max(axis=1))
        | (out["low"] > out[["open", "close", "high"]].min(axis=1))
    ]
    if len(bad_bars) > 0:
        raise DataValidationError(
            f"{len(bad_bars)} bar(s) have inconsistent OHLC values "
            f"(high/low don't bound open/close)."
        )

    out = out.sort_values("timestamp").drop_duplicates(subset="timestamp").reset_index(drop=True)

    if len(out) == 0:
        raise DataValidationError("No valid rows after parsing/cleaning.")

    return LoadedData(df=out, symbol=symbol, source=source_name, tz_note=tz_note)


def generate_synthetic_gold_m1(
    start_date: str,
    end_date: str,
    seed: int = 42,
    base_price: float = 2000.0,
) -> LoadedData:
    """
    Generate synthetic M1 OHLCV data resembling gold price action, for
    testing the engine end-to-end when no real data source is connected.

    This is NOT real market data and must never be presented as such --
    it exists purely to validate that the mechanics of the backtester
    (range detection, breakout logic, fills, SL/TP, stats) work correctly.
    """
    rng = np.random.default_rng(seed)

    idx = pd.date_range(start=start_date, end=end_date, freq="min", tz="UTC")
    # Skip weekends here too, just so synthetic data is realistic; the engine
    # would skip them anyway.
    idx = idx[idx.dayofweek < 5]

    n = len(idx)
    # Random walk with tiny drift, small per-minute volatility, occasional
    # volatility bursts around session opens (00:00 UTC) to create breakout
    # scenarios worth testing.
    minute_of_day = idx.hour * 60 + idx.minute
    vol_burst = np.where((minute_of_day >= 0) & (minute_of_day < 60), 1.8, 1.0)

    step_std = 0.35 * vol_burst
    steps = rng.normal(loc=0.0, scale=step_std, size=n)
    close = base_price + np.cumsum(steps)

    open_ = np.empty(n)
    open_[0] = base_price
    open_[1:] = close[:-1]

    intrabar_range = np.abs(rng.normal(loc=0.4, scale=0.25, size=n)) * vol_burst
    high = np.maximum(open_, close) + intrabar_range * rng.uniform(0.2, 1.0, size=n)
    low = np.minimum(open_, close) - intrabar_range * rng.uniform(0.2, 1.0, size=n)

    df = pd.DataFrame({
        "timestamp": idx,
        "open": open_,
        "high": high,
        "low": low,
        "close": close,
    })

    return LoadedData(
        df=df,
        symbol="XAUUSD (SYNTHETIC)",
        source="synthetic_generator",
        tz_note="Synthetic data generated directly in UTC; no conversion needed.",
    )
