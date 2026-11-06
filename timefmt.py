"""
How dates and times are shown everywhere: day-month-year and 12-hour clock, on this PC's clock
(Pakistan time on your PC). Example: 28-10-2026 5:31pm.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

import pandas as pd


def _ok(ts) -> bool:
    return ts is not None and not (isinstance(ts, float) and ts != ts) and not pd.isna(ts)


def date_text(ts) -> str:
    """28-10-2026"""
    return pd.Timestamp(ts).strftime("%d-%m-%Y") if _ok(ts) else "-"


def time_text(ts, seconds: bool = False) -> str:
    """5:20pm (or 5:20:33pm)"""
    if not _ok(ts):
        return "-"
    text = pd.Timestamp(ts).strftime("%I:%M:%S%p" if seconds else "%I:%M%p").lower()
    return text[1:] if text.startswith("0") else text


def datetime_text(ts, seconds: bool = False) -> str:
    """28-10-2026 5:31pm"""
    return f"{date_text(ts)} {time_text(ts, seconds)}" if _ok(ts) else "-"


def when_text(ts, today: Optional[pd.Timestamp] = None) -> str:
    """Just the time for today (5:31pm), the date and time for any other day (27-10-2026 10:57pm)."""
    if not _ok(ts):
        return "-"
    today = pd.Timestamp(today if today is not None else datetime.now()).normalize()
    return time_text(ts) if pd.Timestamp(ts).normalize() == today else datetime_text(ts)


def now_text(seconds: bool = True) -> str:
    return time_text(datetime.now(), seconds)


def format_columns(df: pd.DataFrame, columns, seconds: bool = False) -> pd.DataFrame:
    """Copy of a table with the given date/time columns shown as 28-10-2026 5:31pm."""
    out = df.copy()
    for col in columns:
        if col in out.columns:
            out[col] = pd.to_datetime(out[col], errors="coerce").map(lambda t: datetime_text(t, seconds))
    return out
