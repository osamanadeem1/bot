"""
Strategy speeds. The same five-timeframe rules can run on three candle sizes:

- "swing"  Gold 5-minute swing (default): 5/15/25/50/75-minute candles, stop buffer 1.0 ATR, target 2.5R, only
           when ADX is 20+, half the position closed at 0.5R and the stop then moved to entry. Profitable over
           3 years of gold (Nov 2023 - Nov 2026) for every candle start time, but only slightly (README 7b-7e).
- "medium" Gold 3-minute: 3/9/15/30/45-minute candles with the plain rules (0.5 ATR, 2R). About 3 times as many
           signals; similar total profit over Aug 2024 - Nov 2026 but less per trade and bigger drops (README 7e).
- "fast"   Gold 1-minute: 1/3/5/10/15-minute candles. Lost money on 2.2 years of gold (Aug 2024 - Oct 2026),
           so it is watch only (README section 7d).

How the swing speed works: every real 5 minutes counts as one engine minute on a fixed clock, so the
unchanged engine treats 5-minute candles as its "1m" and builds 15/25/50/75-minute candles as its
"3m/5m/10m/15m". The clock is anchored to fixed dates, so live scans, backtests and saved-signal
replays build exactly the same candles whatever window of history is loaded.
"""
from __future__ import annotations

import copy
import re
from dataclasses import dataclass, field, replace
from typing import Dict, Optional, Tuple

import pandas as pd

from config import Settings
from engine import NO_TRADE, Signal, evaluate_bars, evaluate_snapshot
from strategy import Check

REAL_EPOCH = pd.Timestamp("2020-01-06")  # a Monday, on the data clock
ENGINE_EPOCH = pd.Timestamp("2000-01-03")


@dataclass(frozen=True)
class Profile:
    key: str
    name: str
    base_minutes: int
    overrides: Dict[str, object] = field(default_factory=dict)
    note: str = ""
    tested: bool = True  # False = lost money in testing: signals are shown for information only, no orders
    how_often: str = ""  # how often a signal comes, from the long backtests
    short: str = ""  # a few words for labels, e.g. "5-min swing"

    @property
    def labels(self) -> Dict[str, str]:
        """Engine timeframe name -> real candle size shown to you."""
        return {tf: f"{int(tf[:-1]) * self.base_minutes}m" for tf in ("60m", "15m", "10m", "5m", "3m", "1m")}


PROFILES: Dict[str, Profile] = {
    "swing": Profile(
        "swing", "Gold 5-minute swing", 5,
        {"strategy.stop_buffer_atr": 1.0, "risk.target_reward_risk": 2.5, "risk.max_hold_minutes": 240,
         "strategy.min_adx": 20.0, "risk.partial_at_r": 0.5},
        "5/15/25/50/75-minute candles; only when the trend is strong (ADX 20+); half the profit taken at 0.5R, "
        "then the stop moves to entry. Tested on 3 years of gold (Nov 2023-Nov 2026): 68% of trades won, 28% hit "
        "the full stop-loss, +0.08R per trade, about 2 trades a week. Small profit, not proven.",
        how_often="about twice a week (gold, 3-year test)", short="5-min swing"),
    "medium": Profile(
        "medium", "Gold 3-minute", 3, {"risk.max_hold_minutes": 240},
        "3/9/15/30/45-minute candles, plain rules (stop buffer 0.5 ATR, target 2R). Tested Aug 2024-Nov 2026 on "
        "gold: about 4.5 trades a week, 40% won, 53% hit the full stop-loss, +0.06R per trade. More signals than "
        "the swing rules but less profit per trade and bigger drops. Not proven.",
        how_often="about once a trading day (gold, 2-year test)", short="3-min"),
    "fast": Profile(
        "fast", "Gold 1-minute (watch only)", 1, {},
        "1/3/5/10/15-minute candles. Lost money on 2.2 years of gold (Aug 2024-Oct 2026: -0.07R per trade over "
        "1,430 trades), so its signals are information only, with no order buttons.", tested=False,
        how_often="about twice a day", short="1-min"),
}

# "both": the 5-minute swing and 3-minute rules side by side, each with its own trades (README section 7g).
BOTH = ("swing", "medium")
MODE_KEYS = ["both", "swing", "medium", "fast"]
BOTH_NOTE = ("Runs Gold 5-minute swing and Gold 3-minute together, about 6.4 gold signals a week. Up to two gold "
             "trades can be open at once, one per strategy. Tested Aug 2024-Nov 2026: +0.07R per trade, +53R in "
             "total, largest drop about 20R (medians of 5 candle start times), profitable in both halves for every "
             "start time. Not proven.")


def lanes(key: Optional[str]) -> list:
    """The strategies a mode runs: both -> [swing, medium]; any single strategy -> [itself]."""
    if key == "both":
        return list(BOTH)
    return [key if key in PROFILES else "fast"]


def mode_name(key: str) -> str:
    return "Gold swing + 3-minute (both)" if key == "both" else get_profile(key).name


def mode_note(key: str) -> str:
    return BOTH_NOTE if key == "both" else get_profile(key).note


def get_profile(key: Optional[str]) -> Profile:
    if key == "both":  # a mode, not one strategy: its first strategy
        return PROFILES[BOTH[0]]
    return PROFILES.get(key or "fast", PROFILES["fast"])


def profile_settings(settings: Settings, key: str) -> Settings:
    """A copy of the settings with the profile's rule changes applied (your risk % etc. are kept)."""
    prof = get_profile(key)
    s = copy.deepcopy(settings)
    for path, value in prof.overrides.items():
        section, name = path.split(".")
        setattr(getattr(s, section), name, value)
    if s.risk.take_half_at_1r is not None:  # the dashboard switch wins over the strategy's own choice
        s.risk.partial_at_r = half_level(key) if s.risk.take_half_at_1r else 0.0
    if prof.base_minutes > 1:
        # These optional filters (off by default) were only designed and tested for the 1-minute rules.
        s.risk.blocked_hours = []
        s.strategy.use_hourly_trend = False
    return s


def takes_half(key: str) -> bool:
    """Does this strategy take half the profit early by default?"""
    return float(get_profile(key).overrides.get("risk.partial_at_r", 0.0)) > 0


def half_level(key: str) -> float:
    """Where the first half takes profit, in R: the strategy's own tested level, else 1R (when switched on)."""
    return float(get_profile(key).overrides.get("risk.partial_at_r", 0.0)) or 1.0


def hold_hours(settings: Settings, key: str) -> float:
    """The time limit the backtests use: close a trade after this long if neither stop nor target is hit."""
    return profile_settings(settings, key).risk.max_hold_minutes * get_profile(key).base_minutes / 60


# ------------------------------------------------------------------ clocks
def to_engine(ts, base: int):
    """Real (data-clock) time -> engine time. Works on a Timestamp or a Series."""
    if base == 1 or ts is None:
        return ts
    return ENGINE_EPOCH + (ts - REAL_EPOCH) / base


def to_real(ts, base: int):
    if base == 1 or ts is None:
        return ts
    return REAL_EPOCH + (ts - ENGINE_EPOCH) * base


def engine_bars(bars: pd.DataFrame, base: int) -> pd.DataFrame:
    return bars if base == 1 else bars.assign(time=to_engine(bars["time"], base))


# ------------------------------------------------------------------ wording
_TF = re.compile(r"\b(60|15|10|5|3|1)m\b(?! ago)")
_AGO = re.compile(r"(?:(\d+)h )?(\d+)m ago")
_SECS = re.compile(r"(\d+)s (ago|old)")
_GAP = re.compile(r"Gap of (\d+) min in the last (\d+) min")


def relabel_text(text: str, base: int) -> str:
    """Rewrite engine timeframe names and durations into real ones (e.g. '15m trend' -> '75m trend')."""
    if base == 1 or not text:
        return text
    text = _TF.sub(lambda m: f"{int(m.group(1)) * base}m", text)

    def ago(m):
        mins = (int(m.group(1) or 0) * 60 + int(m.group(2))) * base
        return (f"{mins // 60}h {mins % 60}m" if mins >= 60 else f"{mins}m") + " ago"

    text = _AGO.sub(ago, text)
    text = _SECS.sub(lambda m: f"{int(m.group(1)) * base}s {m.group(2)}", text)
    return _GAP.sub(lambda m: f"Gap of {int(m.group(1)) * base} min in the last {int(m.group(2)) * base} min", text)


def _real_check(c: Check, base: int) -> Check:
    return replace(c, timeframe=relabel_text(c.timeframe, base),
                   items=[(relabel_text(name, base), ok) for name, ok in c.items])


def to_real_signal(sig: Signal, base: int) -> Signal:
    """Engine-clock signal -> the same decision with real times and real candle names."""
    if base == 1:
        return sig
    return replace(
        sig, time=to_real(sig.time, base), bar_time=to_real(sig.bar_time, base),
        reason=relabel_text(sig.reason, base), reasons=[relabel_text(r, base) for r in sig.reasons],
        checks=[_real_check(c, base) for c in sig.checks], explanation=relabel_text(sig.explanation, base),
        notes=list(sig.notes))


# ------------------------------------------------------------------ evaluation
@dataclass
class EngineInputs:
    """Exactly what the engine saw, for saving and reproducing a signal."""
    bars: pd.DataFrame
    now: Optional[pd.Timestamp]
    tick_time: Optional[pd.Timestamp]
    settings: Settings


def evaluate_profile(snap, state, settings: Settings, key: str) -> Tuple[Signal, EngineInputs]:
    prof = get_profile(key)
    ps = profile_settings(settings, key)
    base = prof.base_minutes
    if base == 1:
        sig = evaluate_snapshot(snap, state, ps)
        sig.profile = prof.key
        return sig, EngineInputs(snap.bars_1m, snap.now, snap.tick_time, ps)
    bars = engine_bars(snap.bars_1m, base)
    now, tick = to_engine(snap.now, base), to_engine(snap.tick_time, base)
    sig = to_real_signal(evaluate_bars(snap.symbol, bars, now, snap.bid, snap.ask, snap.spec, snap.value_per_unit,
                                       state, ps, tick, snap.source, snap.notes), base)
    # A real-clock price freshness check, stricter than the slower engine clock can express.
    extra = []
    if snap.now is not None and snap.tick_time is not None:
        age = (snap.now - snap.tick_time).total_seconds()
        if age > settings.risk.stale_tick_seconds:
            extra.append(f"Stale price: last tick {int(age)}s old")
    if extra:
        reasons = extra + list(sig.reasons)
        sig = replace(sig, decision=NO_TRADE, plan=None, reason=reasons[0], reasons=reasons,
                      setup_type="" if sig.actionable else sig.setup_type,
                      explanation=f"NO TRADE on {snap.symbol}. {reasons[0]}.")
    sig.profile = prof.key
    return sig, EngineInputs(bars, now, tick, ps)


def replay(bars_real: pd.DataFrame, spec, settings: Settings, key: str, equity: float,
           start: Optional[pd.Timestamp] = None, end: Optional[pd.Timestamp] = None, progress=None):
    """Backtest a profile on real-clock candles of its own size. Trade times come back on the real clock."""
    from backtest import run_backtest  # local import keeps this module light for the scanner

    prof = get_profile(key)
    base = prof.base_minutes
    res = run_backtest(engine_bars(bars_real, base), spec, profile_settings(settings, key), equity,
                       to_engine(start, base), to_engine(end, base), progress=progress,
                       minutes_per_day=1440 // base)
    if base > 1 and len(res.trades):
        for col in ("signal_time", "entry_time", "exit_time"):
            res.trades[col] = to_real(res.trades[col], base)
        res.trades["minutes_held"] = res.trades["minutes_held"] * base
        res.trades["setup"] = res.trades["setup"].astype(str)
    if base > 1 and not res.equity_curve.empty:
        res.equity_curve["time"] = to_real(res.equity_curve["time"], base)
    return res
