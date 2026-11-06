"""
Per-timeframe rules. Each function looks only at CLOSED candles and returns a
Check describing which conditions passed, so every decision is explainable.

    15m  Main trend          EMA 20/50 alignment and slope, swing structure
    10m  Trend agreement     price vs EMAs, RSI momentum, structure intact
    5m   Trade setup         pullback to EMA20, breakout, or breakout-retest
    3m   Setup confirmation  candle direction, EMA, momentum or rejection wick
    1m   Entry timing        close beyond prior candle, EMA, no spike candle

SELL rules are the mirror image of BUY rules.
These are initial rules to TEST - they are not evidence of profitability.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from config import TIMEFRAME_ROLES, StrategySettings

BUY = "BUY"
SELL = "SELL"


def fmt(x: float) -> str:
    if x is None or (isinstance(x, float) and np.isnan(x)):
        return "n/a"
    return f"{x:.6g}"


class Frame:
    """Fast read-only view of an indicator DataFrame limited to the first `n` rows."""

    __slots__ = ("cols", "n", "label")

    def __init__(self, cols: Dict[str, np.ndarray], n: int, label: str = ""):
        self.cols = cols
        self.n = n
        self.label = label

    @classmethod
    def from_df(cls, df: pd.DataFrame, label: str = "") -> "Frame":
        cols = {c: df[c].to_numpy(dtype=float) for c in df.columns if c != "time"}
        cols["time"] = df["time"].to_numpy() if "time" in df.columns else np.array([])
        return cls(cols, len(df), label)

    def upto(self, n: int) -> "Frame":
        return Frame(self.cols, n, self.label)

    def v(self, col: str, back: int = 0) -> float:
        return float(self.cols[col][self.n - 1 - back])

    def win(self, col: str, length: int) -> np.ndarray:
        """Last `length` values ending at the latest closed bar."""
        start = max(0, self.n - length)
        return self.cols[col][start:self.n]

    def time(self, back: int = 0):
        return pd.Timestamp(self.cols["time"][self.n - 1 - back])


@dataclass
class Check:
    timeframe: str
    purpose: str
    direction: Optional[str]
    passed: bool
    items: List[Tuple[str, bool]] = field(default_factory=list)
    info: Dict[str, object] = field(default_factory=dict)

    @property
    def detail(self) -> str:
        if not self.items:
            return "No data"
        failed = [name for name, ok in self.items if not ok]
        if not failed:
            return "; ".join(name for name, _ in self.items)
        return "Failed: " + "; ".join(failed)

    def to_dict(self) -> dict:
        return {"timeframe": self.timeframe, "purpose": self.purpose, "direction": self.direction,
                "passed": self.passed, "detail": self.detail,
                "items": [[n, bool(ok)] for n, ok in self.items],
                "info": {k: (None if isinstance(v, float) and np.isnan(v) else v) for k, v in self.info.items()}}


def _check(tf: str, direction: Optional[str], items, info=None) -> Check:
    passed = bool(items) and all(ok for _, ok in items)
    return Check(tf, TIMEFRAME_ROLES[tf], direction, passed, list(items), info or {})


def _not_enough(tf: str, direction, n: int, need: int) -> Check:
    return _check(tf, direction, [(f"Need {need} closed {tf} candles, have {n}", False)])


def _gt(a: float, b: float, direction: str) -> bool:
    if np.isnan(a) or np.isnan(b):
        return False
    return a > b if direction == BUY else a < b


def min_bars(cfg: StrategySettings) -> int:
    return cfg.ema_slow + max(cfg.trend_slope_bars, cfg.breakout_bars, cfg.retest_bars) + 2


# --------------------------------------------------------------------------- 15m main trend

def check_trend(f: Frame, cfg: StrategySettings, d: str, f60: Optional[Frame] = None) -> Check:
    tf = "15m"
    need = min_bars(cfg)
    if f.n < need:
        return _not_enough(tf, d, f.n, need)
    extra = []
    if cfg.min_adx > 0:
        a = f.v("adx")
        extra.append((f"ADX {a:.0f}: trend strong enough (min {cfg.min_adx:g})", a >= cfg.min_adx))
    if cfg.min_trend_strength_atr > 0:
        gap = abs(f.v("ema_fast") - f.v("ema_slow")) / f.v("atr")
        extra.append((f"Trend strength {gap:.2f} ATR (min {cfg.min_trend_strength_atr:g})",
                      gap >= cfg.min_trend_strength_atr))
    if cfg.use_hourly_trend:
        need60 = cfg.ema_slow + 2
        if f60 is None or f60.n < need60:
            extra.append((f"Need {need60} closed 1h candles, have {0 if f60 is None else f60.n}", False))
        else:
            extra.append(("1-hour trend agrees (close and EMA20 on the trend side of EMA50)",
                          _gt(f60.v("close"), f60.v("ema_slow"), d) and _gt(f60.v("ema_fast"), f60.v("ema_slow"), d)))
    close, ef, es = f.v("close"), f.v("ema_fast"), f.v("ema_slow")
    ef_prev = f.v("ema_fast", cfg.trend_slope_bars)
    above = "above" if d == BUY else "below"
    if d == BUY:
        sw, sw_prev = f.v("last_sl"), f.v("prev_sl")
        structure = (f"Higher swing low ({fmt(sw)} > {fmt(sw_prev)})", _gt(sw, sw_prev, BUY))
        intact = (f"Price above last swing low {fmt(sw)}", _gt(close, sw, BUY))
    else:
        sw, sw_prev = f.v("last_sh"), f.v("prev_sh")
        structure = (f"Lower swing high ({fmt(sw)} < {fmt(sw_prev)})", _gt(sw, sw_prev, SELL))
        intact = (f"Price below last swing high {fmt(sw)}", _gt(close, sw, SELL))
    items = [
        (f"Close {fmt(close)} {above} EMA{cfg.ema_slow} {fmt(es)}", _gt(close, es, d)),
        (f"EMA{cfg.ema_fast} {above} EMA{cfg.ema_slow}", _gt(ef, es, d)),
        (f"EMA{cfg.ema_fast} {'rising' if d == BUY else 'falling'}", _gt(ef, ef_prev, d)),
        structure,
        intact,
        *extra,
    ]
    info = {"last_sh": f.v("last_sh"), "prev_sh": f.v("prev_sh"), "last_sl": f.v("last_sl"),
            "prev_sl": f.v("prev_sl"), "close": close}
    return _check(tf, d, items, info)


# --------------------------------------------------------------------------- 10m agreement

def check_agreement(f: Frame, cfg: StrategySettings, d: str) -> Check:
    tf = "10m"
    need = cfg.ema_slow + 2
    if f.n < need:
        return _not_enough(tf, d, f.n, need)
    close, ef, es, r = f.v("close"), f.v("ema_fast"), f.v("ema_slow"), f.v("rsi")
    above = "above" if d == BUY else "below"
    if d == BUY:
        sw = f.v("last_sl")
        intact = (f"Structure intact (above swing low {fmt(sw)})", _gt(close, sw, BUY))
        mom = (f"RSI {r:.1f} above {cfg.rsi_mid:g}", r > cfg.rsi_mid)
    else:
        sw = f.v("last_sh")
        intact = (f"Structure intact (below swing high {fmt(sw)})", _gt(close, sw, SELL))
        mom = (f"RSI {r:.1f} below {cfg.rsi_mid:g}", r < cfg.rsi_mid)
    items = [
        (f"Close {above} EMA{cfg.ema_fast}", _gt(close, ef, d)),
        (f"EMA{cfg.ema_fast} {above} EMA{cfg.ema_slow}", _gt(ef, es, d)),
        mom,
        intact,
    ]
    return _check(tf, d, items, {"rsi": r})


# --------------------------------------------------------------------------- 5m setup

def _pullback(f: Frame, cfg: StrategySettings, d: str) -> bool:
    w = cfg.pullback_bars
    closes, ef, es, atr = f.win("close", w), f.win("ema_fast", w), f.win("ema_slow", w), f.win("atr", w)
    close, ef_last, es_last = f.v("close"), f.v("ema_fast"), f.v("ema_slow")
    if d == BUY:
        touched = bool(np.any(f.win("low", w) <= ef + cfg.pullback_atr_tolerance * atr))
        held = bool(np.all(closes > es))
        resumed = close > ef_last > es_last
    else:
        touched = bool(np.any(f.win("high", w) >= ef - cfg.pullback_atr_tolerance * atr))
        held = bool(np.all(closes < es))
        resumed = close < ef_last < es_last
    return touched and held and resumed


def _breakout(f: Frame, cfg: StrategySettings, d: str) -> Tuple[bool, float]:
    close, open_, atr = f.v("close"), f.v("open"), f.v("atr")
    if d == BUY:
        level = f.v("range_high")
        ok = (not np.isnan(level)) and close > level and close > open_ and \
            (close - level) <= cfg.max_breakout_extension_atr * atr
    else:
        level = f.v("range_low")
        ok = (not np.isnan(level)) and close < level and close < open_ and \
            (level - close) <= cfg.max_breakout_extension_atr * atr
    return bool(ok), level


def _retest(f: Frame, cfg: StrategySettings, d: str) -> Tuple[bool, float]:
    r = cfg.retest_bars
    if f.n < r + 2:
        return False, np.nan
    closes = f.win("close", r + 1)
    levels = f.win("range_high" if d == BUY else "range_low", r + 1)
    lows, highs = f.win("low", r + 1), f.win("high", r + 1)
    close, open_, atr = f.v("close"), f.v("open"), f.v("atr")
    tol = cfg.retest_atr_tolerance * atr
    for j in range(r - 1, -1, -1):  # most recent breakout before the latest bar
        lvl = levels[j]
        if np.isnan(lvl):
            continue
        if d == BUY and closes[j] > lvl:
            came_back = lows[j + 1:].min() <= lvl + tol
            held = closes[j + 1:].min() > lvl - tol
            resumed = close > lvl and close > open_
            return bool(came_back and held and resumed), lvl
        if d == SELL and closes[j] < lvl:
            came_back = highs[j + 1:].max() >= lvl - tol
            held = closes[j + 1:].max() < lvl + tol
            resumed = close < lvl and close < open_
            return bool(came_back and held and resumed), lvl
    return False, np.nan


def check_setup(f: Frame, cfg: StrategySettings, d: str) -> Check:
    tf = "5m"
    need = min_bars(cfg)
    if f.n < need:
        return _not_enough(tf, d, f.n, need)
    pullback = _pullback(f, cfg, d)
    retest, retest_level = _retest(f, cfg, d)
    breakout, breakout_level = _breakout(f, cfg, d)
    setup_type = "pullback" if pullback else "retest" if retest else "breakout" if breakout else ""
    side_ok = _gt(f.v("close"), f.v("ema_slow"), d)
    lookback = cfg.stop_lookback_bars
    atr = f.v("atr")
    if d == BUY:
        stop_ref = float(f.win("low", lookback).min() - cfg.stop_buffer_atr * atr)
    else:
        stop_ref = float(f.win("high", lookback).max() + cfg.stop_buffer_atr * atr)
    items = [
        (f"Pullback to EMA{cfg.ema_fast} and resume" if pullback else "No pullback-and-resume", pullback),
        (f"Breakout retest of {fmt(retest_level)}" if retest else "No breakout retest", retest),
        (f"Breakout beyond {fmt(breakout_level)}" if breakout else "No clean breakout", breakout),
        (f"Price on trend side of EMA{cfg.ema_slow}", side_ok),
    ]
    # Any one setup is enough; the trend-side condition is always required.
    passed = side_ok and bool(setup_type)
    shown = [(n, ok) for n, ok in items[:3] if ok] or [("No pullback, breakout or retest", False)]
    chk = Check(tf, TIMEFRAME_ROLES[tf], d, passed, shown + [items[3]],
                {"setup_type": setup_type, "stop_reference": stop_ref, "atr": atr})
    return chk


# --------------------------------------------------------------------------- 3m confirmation

def check_confirmation(f: Frame, cfg: StrategySettings, d: str) -> Check:
    tf = "3m"
    need = cfg.ema_slow + 2
    if f.n < need:
        return _not_enough(tf, d, f.n, need)
    o, h, l, c = f.v("open"), f.v("high"), f.v("low"), f.v("close")
    r, r_prev, ef = f.v("rsi"), f.v("rsi", 1), f.v("ema_fast")
    rng = h - l
    if d == BUY:
        candle = c > o
        momentum = r > cfg.rsi_mid and r > r_prev
        wick = min(o, c) - l
        rejection = rng > 0 and wick >= cfg.rejection_wick_ratio * rng and (c - l) >= 0.6 * rng
        names = ("Bullish candle", f"Close above EMA{cfg.ema_fast}")
    else:
        candle = c < o
        momentum = r < cfg.rsi_mid and r < r_prev
        wick = h - max(o, c)
        rejection = rng > 0 and wick >= cfg.rejection_wick_ratio * rng and (h - c) >= 0.6 * rng
        names = ("Bearish candle", f"Close below EMA{cfg.ema_fast}")
    what = []
    if momentum:
        what.append(f"RSI {r:.1f} {'rising' if d == BUY else 'falling'}")
    if rejection:
        what.append("rejection wick")
    items = [
        (names[0], candle),
        (names[1], _gt(c, ef, d)),
        ("Momentum: " + (", ".join(what) if what else f"RSI {r:.1f} not confirming, no rejection wick"),
         momentum or rejection),
    ]
    return _check(tf, d, items, {"rsi": r})


# --------------------------------------------------------------------------- 1m entry trigger

def check_trigger(f: Frame, cfg: StrategySettings, d: str) -> Check:
    tf = "1m"
    need = cfg.ema_slow + 2
    if f.n < need:
        return _not_enough(tf, d, f.n, need)
    h, l, c = f.v("high"), f.v("low"), f.v("close")
    prev_h, prev_l, ef, atr = f.v("high", 1), f.v("low", 1), f.v("ema_fast"), f.v("atr")
    rng_ok = (h - l) <= cfg.max_entry_candle_atr * atr
    if d == BUY:
        items = [(f"Closed above previous high {fmt(prev_h)}", c > prev_h),
                 (f"Close above EMA{cfg.ema_fast}", c > ef)]
    else:
        items = [(f"Closed below previous low {fmt(prev_l)}", c < prev_l),
                 (f"Close below EMA{cfg.ema_fast}", c < ef)]
    items.append((f"Not a spike candle (range <= {cfg.max_entry_candle_atr:g} ATR)", rng_ok))
    return _check(tf, d, items)


def opposing_level(f15: Frame, d: str, entry: float) -> Optional[float]:
    """Nearest confirmed 15m swing level in the trade's path (resistance for BUY, support for SELL)."""
    if f15.n == 0:
        return None
    if d == BUY:
        lv = [x for x in (f15.v("last_sh"), f15.v("prev_sh")) if not np.isnan(x) and x > entry]
        return min(lv) if lv else None
    lv = [x for x in (f15.v("last_sl"), f15.v("prev_sl")) if not np.isnan(x) and x < entry]
    return max(lv) if lv else None
