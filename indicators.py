"""
Indicator functions. Every column is causal: the value on bar i uses only
bars 0..i, so a backtest that reads row i never sees the future.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from config import StrategySettings


def ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False).mean()


def rsi(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0.0)
    loss = -delta.clip(upper=0.0)
    avg_gain = gain.ewm(alpha=1.0 / period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1.0 / period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0.0, np.nan)
    out = 100.0 - 100.0 / (1.0 + rs)
    out = out.where(avg_loss != 0.0, 100.0)
    out = out.where(~((avg_gain == 0.0) & (avg_loss == 0.0)), 50.0)
    out.iloc[0] = 50.0
    return out


def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    prev_close = df["close"].shift(1)
    tr = pd.concat([
        df["high"] - df["low"],
        (df["high"] - prev_close).abs(),
        (df["low"] - prev_close).abs(),
    ], axis=1).max(axis=1)
    tr.iloc[0] = df["high"].iloc[0] - df["low"].iloc[0]
    return tr.ewm(alpha=1.0 / period, adjust=False).mean()


def adx(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """Wilder's Average Directional Index: how strongly price is trending (either way). 25+ = a clear trend."""
    up = df["high"].diff()
    down = -df["low"].diff()
    plus = pd.Series(np.where((up > down) & (up > 0), up, 0.0), index=df.index)
    minus = pd.Series(np.where((down > up) & (down > 0), down, 0.0), index=df.index)
    a = 1.0 / period
    tr = atr(df, 1)  # true range
    trn = tr.ewm(alpha=a, adjust=False).mean()
    pdi = 100 * plus.ewm(alpha=a, adjust=False).mean() / trn
    mdi = 100 * minus.ewm(alpha=a, adjust=False).mean() / trn
    dx = 100 * (pdi - mdi).abs() / (pdi + mdi).replace(0, np.nan)
    return dx.ewm(alpha=a, adjust=False).mean()


def swing_levels(high: np.ndarray, low: np.ndarray, strength: int):
    """
    Causal swing points. A swing high at bar j needs `strength` lower highs on each
    side, so it only becomes known at bar j + strength. Returns, for every bar, the
    last and previous confirmed swing high and swing low (NaN until known).
    """
    n = len(high)
    last_sh = np.full(n, np.nan)
    prev_sh = np.full(n, np.nan)
    last_sl = np.full(n, np.nan)
    prev_sl = np.full(n, np.nan)
    c_last_sh = c_prev_sh = c_last_sl = c_prev_sl = np.nan
    k = strength
    for i in range(n):
        j = i - k
        if j - k >= 0:
            left_h = high[j - k:j]
            right_h = high[j + 1:i + 1]
            if high[j] > left_h.max() and high[j] >= right_h.max():
                c_prev_sh, c_last_sh = c_last_sh, high[j]
            left_l = low[j - k:j]
            right_l = low[j + 1:i + 1]
            if low[j] < left_l.min() and low[j] <= right_l.min():
                c_prev_sl, c_last_sl = c_last_sl, low[j]
        last_sh[i], prev_sh[i], last_sl[i], prev_sl[i] = c_last_sh, c_prev_sh, c_last_sl, c_prev_sl
    return last_sh, prev_sh, last_sl, prev_sl


def add_indicators(df: pd.DataFrame, cfg: StrategySettings) -> pd.DataFrame:
    out = df.copy()
    if len(out) == 0:
        for c in ("ema_fast", "ema_slow", "rsi", "atr", "adx", "last_sh", "prev_sh", "last_sl", "prev_sl",
                  "range_high", "range_low"):
            out[c] = pd.Series(dtype=float)
        return out
    out["ema_fast"] = ema(out["close"], cfg.ema_fast)
    out["ema_slow"] = ema(out["close"], cfg.ema_slow)
    out["rsi"] = rsi(out["close"], cfg.rsi_period)
    out["atr"] = atr(out, cfg.atr_period)
    out["adx"] = adx(out, 14)
    sh, psh, sl, psl = swing_levels(out["high"].to_numpy(float), out["low"].to_numpy(float), cfg.swing_strength)
    out["last_sh"], out["prev_sh"], out["last_sl"], out["prev_sl"] = sh, psh, sl, psl
    # Range of the previous N bars, excluding the current bar (used for breakouts).
    out["range_high"] = out["high"].rolling(cfg.breakout_bars, min_periods=cfg.breakout_bars).max().shift(1)
    out["range_low"] = out["low"].rolling(cfg.breakout_bars, min_periods=cfg.breakout_bars).min().shift(1)
    return out
