"""
Candle handling: normalise 1-minute bars, validate them, and build the
3, 5, 10 and 15-minute candles from closed 1-minute candles only.

Times are bar OPEN times. A bar of N minutes that opened at T is closed
once the clock reaches T + N minutes. Decisions only ever use closed bars.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

import pandas as pd

from config import TIMEFRAMES

PRICE_COLUMNS = ["open", "high", "low", "close"]
ONE_MINUTE = pd.Timedelta(minutes=1)


def normalize_bars(df: pd.DataFrame, default_spread: float = 0.0) -> pd.DataFrame:
    """Return a clean copy with columns time, open, high, low, close, spread, volume."""
    if df is None or len(df) == 0:
        return pd.DataFrame(columns=["time", *PRICE_COLUMNS, "spread", "volume"])
    out = df.copy()
    out.columns = [str(c).strip().lower() for c in out.columns]
    if "time" not in out.columns:
        for alt in ("datetime", "date", "timestamp"):
            if alt in out.columns:
                out = out.rename(columns={alt: "time"})
                break
    missing = [c for c in ["time", *PRICE_COLUMNS] if c not in out.columns]
    if missing:
        raise ValueError(f"Candle data is missing columns: {missing}")
    if pd.api.types.is_numeric_dtype(out["time"]):
        out["time"] = pd.to_datetime(out["time"], unit="s")
    else:
        out["time"] = pd.to_datetime(out["time"])
    if isinstance(out["time"].dtype, pd.DatetimeTZDtype):
        out["time"] = out["time"].dt.tz_convert("UTC").dt.tz_localize(None)
    out["time"] = out["time"].astype("datetime64[ns]")
    for c in PRICE_COLUMNS:
        out[c] = pd.to_numeric(out[c], errors="coerce").astype(float)
    if "spread" not in out.columns:
        out["spread"] = float(default_spread)
    out["spread"] = pd.to_numeric(out["spread"], errors="coerce").fillna(default_spread).astype(float)
    if "volume" not in out.columns:
        out["volume"] = out["tick_volume"] if "tick_volume" in out.columns else 0.0
    out["volume"] = pd.to_numeric(out["volume"], errors="coerce").fillna(0.0).astype(float)
    out = out.sort_values("time", kind="mergesort").reset_index(drop=True)
    return out[["time", *PRICE_COLUMNS, "spread", "volume"]]


@dataclass
class DataQualityReport:
    ok: bool
    issues: List[str] = field(default_factory=list)  # block trading
    warnings: List[str] = field(default_factory=list)  # informational
    last_bar_time: Optional[pd.Timestamp] = None
    bar_age_seconds: Optional[float] = None
    bars: int = 0

    def summary(self) -> str:
        if self.ok and not self.warnings:
            return "Data OK"
        parts = self.issues + self.warnings
        return "; ".join(parts)


def validate_bars(
    df: pd.DataFrame,
    now: Optional[pd.Timestamp] = None,
    stale_bar_seconds: Optional[float] = None,
    max_gap_minutes: int = 3,
    gap_check_minutes: int = 30,
) -> DataQualityReport:
    """Check closed 1-minute bars for problems that should stop trading."""
    issues: List[str] = []
    warnings: List[str] = []
    if df is None or len(df) == 0:
        return DataQualityReport(ok=False, issues=["No candle data received"])

    t = df["time"]
    if t.duplicated().any():
        issues.append(f"{int(t.duplicated().sum())} duplicate timestamps")
    if not t.is_monotonic_increasing:
        issues.append("Timestamps are not in order")
    misaligned = (t.dt.second != 0) | (t.dt.microsecond != 0)
    if misaligned.any():
        issues.append(f"{int(misaligned.sum())} candles not aligned to whole minutes")
    if df[PRICE_COLUMNS].isna().any().any():
        issues.append("Missing (NaN) prices")
    if (df[PRICE_COLUMNS] <= 0).any().any():
        issues.append("Zero or negative prices")
    bad_ohlc = (df["high"] < df[["open", "close"]].max(axis=1) - 1e-12) | (
        df["low"] > df[["open", "close"]].min(axis=1) + 1e-12
    ) | (df["high"] < df["low"])
    if bad_ohlc.any():
        issues.append(f"{int(bad_ohlc.sum())} candles with inconsistent high/low")

    last_time = t.iloc[-1]
    # Recent gaps: if any candle in the last window came right after missing candles (a data gap, the daily
    # break or the weekend), trading waits. Same rule as the backtester (backtest.precompute gap_ok).
    window_start = last_time - pd.Timedelta(minutes=gap_check_minutes)
    gaps = t.diff()
    in_window = t >= window_start
    big = gaps[in_window & (gaps > pd.Timedelta(minutes=max_gap_minutes))]
    if len(big):
        issues.append(f"Gap of {int(big.max().total_seconds() // 60)} min in the last {gap_check_minutes} min")
    small = gaps[in_window & (gaps > ONE_MINUTE) & (gaps <= pd.Timedelta(minutes=max_gap_minutes))]
    if len(small):
        warnings.append(f"{len(small)} short gaps (1-{max_gap_minutes} min) recently")

    age = None
    if now is not None:
        age = (now - (last_time + ONE_MINUTE)).total_seconds()
        if stale_bar_seconds is not None and age > stale_bar_seconds:
            if age >= 900:
                mins = int(age // 60)
                ago = f"{mins // 60}h {mins % 60}m" if mins >= 60 else f"{mins}m"
                issues.append(f"Stale data: last candle closed {ago} ago - market closed or no prices")
            else:
                issues.append(f"Stale data: last candle closed {int(age)}s ago")
        if age < -60:
            issues.append("Candle timestamps are ahead of the clock (time zone problem?)")

    return DataQualityReport(
        ok=not issues, issues=issues, warnings=warnings, last_bar_time=last_time,
        bar_age_seconds=age, bars=len(df),
    )


def closed_bars(df: pd.DataFrame, now: pd.Timestamp, minutes: int) -> pd.DataFrame:
    """Keep only bars whose full period has ended by `now`."""
    if len(df) == 0:
        return df
    end = df["time"] + pd.Timedelta(minutes=minutes)
    return df.loc[end <= now].reset_index(drop=True)


def resample_bars(df_1m: pd.DataFrame, minutes: int) -> pd.DataFrame:
    """Build N-minute candles from 1-minute candles, aligned to midnight (00:00, 00:03, ...)."""
    if minutes == 1:
        out = df_1m.copy()
        out["bars"] = 1
        return out
    if len(df_1m) == 0:
        return pd.DataFrame(columns=["time", *PRICE_COLUMNS, "spread", "volume", "bars"])
    g = df_1m.set_index("time").resample(f"{minutes}min", label="left", closed="left", origin="start_day")
    out = g.agg({
        "open": "first", "high": "max", "low": "min", "close": "last",
        "spread": "mean", "volume": "sum",
    })
    out["bars"] = g["close"].count()
    out = out[out["bars"] > 0].reset_index()
    return out[["time", *PRICE_COLUMNS, "spread", "volume", "bars"]]


def build_timeframes(df_1m: pd.DataFrame, now: pd.Timestamp) -> Dict[str, pd.DataFrame]:
    """Closed candles for every timeframe, built from closed 1-minute candles only."""
    closed_1m = closed_bars(df_1m, now, 1)
    frames = {}
    for label, minutes in TIMEFRAMES.items():
        frames[label] = closed_bars(resample_bars(closed_1m, minutes), now, minutes)
    return frames
