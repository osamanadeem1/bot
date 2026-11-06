"""
What happened to a signal after it was given: still running, hit its target, hit its stop-loss, or closed at
the time limit. Used by "Latest signals" on the Desk, which lists every BUY or SELL the desk actually showed
(the same ones as the Journal), including signals that "Last 3 days" leaves out because an earlier trade on
the same market was still running in the replay.

The candle rules match the backtests: BUY trades use bid prices, SELL trades ask prices (bid + spread); when a
candle touches both the stop-loss and the target, the stop-loss counts (the cautious choice). Results are in R
before costs: 1R = the distance from entry to the stop-loss.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import pandas as pd


@dataclass
class Followed:
    reason: str  # backtest wording: target, stop, breakeven, time, partial+... ; "end of data" = still running
    r: Optional[float]  # result in R for a closed signal; None while it is running
    half_done: bool  # the first half took its profit (the rest's stop-loss is then at entry)

    @property
    def running(self) -> bool:
        return self.reason == "end of data"


def follow(bars: pd.DataFrame, side: str, entry: float, stop: float, target: float, start,
           hold_minutes: Optional[float] = None, half_r: Optional[float] = None) -> Followed:
    """Follow a signal through the candles from `start` (when its signal candle closed, on the candles' clock).
    hold_minutes: close at that candle's close after this long (the tested time limit); None = no limit.
    half_r: half the position takes profit at this many R and the rest's stop moves to entry; None = off."""
    d = 1.0 if side == "BUY" else -1.0
    risk = abs(entry - stop)
    if bars is None or not len(bars) or risk <= 0 or start is None:
        return Followed("end of data", None, False)
    after = bars[bars["time"] >= pd.Timestamp(start)]
    spread = after["spread"] if "spread" in after else pd.Series(0.0, index=after.index)
    stop_now, half_done = stop, False
    half_px = entry + d * half_r * risk if half_r else None
    for (_, b), spr in zip(after.iterrows(), spread.fillna(0.0)):
        add = 0.0 if d > 0 else float(spr)  # a SELL closes at the ask
        hi, lo, close = b["high"] + add, b["low"] + add, b["close"] + add
        hit_stop = lo <= stop_now if d > 0 else hi >= stop_now
        hit_tgt = hi >= target if d > 0 else lo <= target
        if hit_stop:
            reason = "breakeven" if half_done else "stop"
            return _done(reason, d * (stop_now - entry) / risk, half_done, half_r)
        if half_px is not None and not half_done and (hi >= half_px if d > 0 else lo <= half_px):
            half_done, stop_now = True, entry
        if hit_tgt:
            return _done("target", d * (target - entry) / risk, half_done, half_r)
        if hold_minutes and b["time"] - pd.Timestamp(start) >= pd.Timedelta(minutes=hold_minutes):
            return _done("time", d * (close - entry) / risk, half_done, half_r)
    return Followed("end of data", None, half_done)


def _done(reason: str, rest_r: float, half_done: bool, half_r: Optional[float]) -> Followed:
    if half_done and half_r:
        return Followed(f"partial+{reason}", 0.5 * half_r + 0.5 * rest_r, True)
    return Followed(reason, rest_r, False)


def running_r(side: str, entry: float, stop: float, price: float, half_done: bool = False,
              half_r: Optional[float] = None) -> float:
    """Result in R of a still-running signal at `price` (bid for a BUY, ask for a SELL), before costs."""
    risk = abs(entry - stop)
    if risk <= 0:
        return 0.0
    r = (1.0 if side == "BUY" else -1.0) * (price - entry) / risk
    return 0.5 * half_r + 0.5 * r if half_done and half_r else r
