"""
Signal engine: one function, `evaluate`, turns closed candles + current
bid/ask + account state into BUY, SELL or NO TRADE with a full trade plan.

The live scanner, the backtester and the "reproduce a saved signal" feature
all call the same function, so a signal can be re-created from saved data.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

from candles import DataQualityReport, build_timeframes, closed_bars, validate_bars
from config import InstrumentSpec, Settings
from confirmation import SetupResult, evaluate_setup
from indicators import add_indicators
from market_data import MarketSnapshot
from position_sizing import TradePlan, build_trade_plan, round_down_to_step, size_position
from risk_manager import RiskManager, RiskState
from strategy import BUY, SELL, Check, Frame, fmt, opposing_level

NO_TRADE = "NO TRADE"


@dataclass
class Signal:
    symbol: str
    decision: str  # BUY | SELL | NO TRADE
    considered: Optional[str]
    reason: str  # headline reason
    reasons: List[str]
    checks: List[Check]
    plan: Optional[TradePlan]
    setup_type: str
    bid: float
    ask: float
    time: Optional[pd.Timestamp]
    bar_time: Optional[pd.Timestamp]  # open time of the last closed 1m candle used
    source: str = ""
    notes: List[str] = field(default_factory=list)
    data_report: Optional[DataQualityReport] = None
    explanation: str = ""
    tested_market: bool = False
    profile: str = ""  # the strategy that made it (profiles.py key), set by evaluate_profile

    @property
    def actionable(self) -> bool:
        return self.decision in (BUY, SELL)

    @property
    def spread(self) -> float:
        return self.ask - self.bid

    def summary_row(self) -> dict:
        p = self.plan
        return {
            "Symbol": self.symbol,
            "Decision": self.decision,
            "Checks passed": f"{sum(c.passed for c in self.checks)}/5" if self.checks else "-",
            "Setup": self.setup_type or "-",
            "Entry": fmt(p.entry) if p else "-",
            "Stop": fmt(p.stop) if p else "-",
            "Target": fmt(p.target) if p else "-",
            "Size": f"{p.quantity:g} {p.unit}" if p else "-",
            "Planned loss": f"{p.planned_loss:.2f}" if p else "-",
            "Planned profit": f"{p.planned_profit:.2f}" if p else "-",
            "Net R:R": f"{p.reward_risk_net:.2f}" if p else "-",
            "Tested market": "Yes" if self.tested_market else "No",
            "Reason": self.reason,
        }


def split_plan(plan: TradePlan, spec: InstrumentSpec, at_r: float, fraction: float) -> None:
    """Take `fraction` of the size off at `at_r` R (then the stop moves to entry). Skipped when the size is too
    small to split into two parts of at least the broker's minimum lot."""
    q1 = round_down_to_step(plan.quantity * fraction, spec.volume_step)
    q2 = round_down_to_step(plan.quantity - q1, spec.volume_step)
    if q1 < spec.volume_min or q2 < spec.volume_min:
        plan.notes.append(f"Size {plan.quantity:g} is too small to split, so it is one order to the target.")
        return
    step = at_r * plan.stop_distance
    plan.partial_target = plan.entry + step if plan.direction == BUY else plan.entry - step
    plan.partial_quantity = q1


def prepare_frames(bars_1m: pd.DataFrame, now: pd.Timestamp, settings: Settings) -> Dict[str, Frame]:
    raw = build_timeframes(bars_1m, now)
    return {tf: Frame.from_df(add_indicators(df, settings.strategy), tf) for tf, df in raw.items()}


def _explain(symbol: str, decision: str, setup: SetupResult, plan: Optional[TradePlan], reasons: List[str]) -> str:
    if decision == NO_TRADE:
        first = reasons[0] if reasons else "Conditions not met"
        if setup.passed:
            return f"NO TRADE on {symbol}. All five timeframe checks passed, but a risk rule blocks it: {first}."
        passed = [c.timeframe for c in setup.checks if c.passed]
        head = f"NO TRADE on {symbol}. {first}."
        if passed:
            head += f" Passed: {', '.join(passed)}."
        return head
    word = "uptrend" if decision == BUY else "downtrend"
    p = plan
    return (
        f"{decision} {symbol}: 15m {word} confirmed, 10m agrees, 5m {setup.setup_type} setup, "
        f"3m confirmation and 1m entry trigger all passed on closed candles. "
        f"Entry {fmt(p.entry)}, stop {fmt(p.stop)}, target {fmt(p.target)}. "
        f"Size {p.quantity:g} {p.unit}: planned loss {p.planned_loss:.2f}, planned profit {p.planned_profit:.2f} "
        f"after costs (net {p.reward_risk_net:.2f}:1)."
        + (f" Move the stop to the entry price once price reaches {fmt(p.breakeven_trigger)}."
           if p.breakeven_trigger is not None else "")
        + " This is a rule-based setup, not a guarantee."
    )


def evaluate(
    symbol: str,
    frames: Dict[str, Frame],
    bid: float,
    ask: float,
    spec: InstrumentSpec,
    value_per_unit: float,
    state: RiskState,
    settings: Settings,
    data_report: Optional[DataQualityReport] = None,
    now: Optional[pd.Timestamp] = None,
    tick_time: Optional[pd.Timestamp] = None,
    source: str = "",
    notes: Optional[List[str]] = None,
    check_data: bool = True,
) -> Signal:
    risk = settings.risk
    rm = RiskManager(risk)
    blocks: List[str] = []
    blocks += rm.account_blocks(state)
    if risk.blocked_hours and now is not None and now.hour in risk.blocked_hours:
        blocks.append(f"No new trades at {now.hour:02d}:00 broker time (blocked_hours setting)")
    if check_data:
        blocks += rm.data_blocks(data_report, now, tick_time)
        blocks += rm.price_blocks(bid, ask, frames["1m"].v("close") if frames["1m"].n else None)

    setup = evaluate_setup(frames, settings.strategy)
    plan: Optional[TradePlan] = None
    reasons = list(blocks)

    if setup.passed:
        d = setup.direction
        spread = ask - bid
        entry = ask if d == BUY else bid
        # A BUY stop is hit when the bid falls to it; a SELL stop when the ask rises to it.
        stop = setup.stop_reference if d == BUY else setup.stop_reference + spread
        dist = (entry - stop) if d == BUY else (stop - entry)
        atr5 = setup.atr_5m
        cfg = settings.strategy
        if not np.isfinite(dist) or dist <= 0:
            reasons.append("Stop-loss would be on the wrong side of the entry")
        else:
            if dist < cfg.min_stop_atr * atr5:
                reasons.append(f"Stop too tight ({dist / atr5:.2f} ATR, minimum {cfg.min_stop_atr:g})")
            if dist > cfg.max_stop_atr * atr5:
                reasons.append(f"Stop too wide ({dist / atr5:.2f} ATR, maximum {cfg.max_stop_atr:g})")
            target = entry + risk.target_reward_risk * dist if d == BUY else entry - risk.target_reward_risk * dist
            level = opposing_level(frames["15m"], d, entry)
            if level is not None:
                room = abs(level - entry) / dist
                if room < risk.obstacle_min_reward_risk:
                    kind = "resistance" if d == BUY else "support"
                    reasons.append(f"15m {kind} at {fmt(level)} leaves only {room:.2f}R of room "
                                   f"(need {risk.obstacle_min_reward_risk:g}R)")
            reasons += rm.spread_blocks(spread, spec, dist)
            sizing = size_position(state.equity, risk.risk_per_trade_pct, entry, stop, spec,
                                   value_per_unit, risk.max_leverage)
            if not sizing.ok:
                reasons.append(sizing.reject_reason)
            else:
                candidate = build_trade_plan(d, entry, stop, target, sizing.quantity, spec, value_per_unit,
                                             spread, state.equity, sizing.risk_budget, sizing.notes)
                reasons += rm.plan_blocks(candidate)
                if risk.breakeven_at_r > 0:
                    step = risk.breakeven_at_r * dist
                    candidate.breakeven_trigger = entry + step if d == BUY else entry - step
                if risk.partial_at_r > 0:
                    split_plan(candidate, spec, risk.partial_at_r, risk.partial_fraction)
                if not reasons:
                    plan = candidate
    else:
        reasons += setup.reasons

    decision = setup.direction if (setup.passed and plan is not None and not reasons) else NO_TRADE
    if decision == NO_TRADE and not reasons:
        reasons.append("Conditions not met")
    bar_time = frames["1m"].time() if frames["1m"].n else None
    sig = Signal(
        symbol=symbol, decision=decision, considered=setup.considered,
        reason="All checks passed" if decision != NO_TRADE else reasons[0],
        reasons=reasons if decision == NO_TRADE else [], checks=setup.checks, plan=plan,
        setup_type=setup.setup_type if setup.passed else "", bid=bid, ask=ask, time=now,
        bar_time=bar_time, source=source, notes=list(notes or []), data_report=data_report,
    )
    sig.explanation = _explain(symbol, decision, setup, plan, sig.reasons)
    sig.tested_market = spec.tested_profitable
    if sig.actionable and not spec.tested_profitable:
        sig.explanation += f" WARNING: {spec.test_note}."
    return sig


def evaluate_bars(symbol: str, bars_1m: pd.DataFrame, now: Optional[pd.Timestamp], bid: float, ask: float,
                  spec: InstrumentSpec, value_per_unit: float, state: RiskState, settings: Settings,
                  tick_time: Optional[pd.Timestamp] = None, source: str = "", notes=None) -> Signal:
    """Full pipeline from raw 1m candles. Used for live scans and for reproducing saved signals."""
    r = settings.risk
    clock = now
    if clock is None:  # unknown clock: treat the newest bar as still forming
        clock = bars_1m["time"].iloc[-1] if len(bars_1m) else pd.Timestamp("1970-01-01")
    elif tick_time is not None and tick_time < clock:
        # A candle only counts as closed once the broker's own clock (its latest tick) has passed its end.
        # This guards against a computer clock that runs ahead of the broker's.
        clock = tick_time
    closed = closed_bars(bars_1m, clock, 1)
    report = validate_bars(closed, now=now, stale_bar_seconds=r.stale_bar_seconds,
                           max_gap_minutes=r.max_gap_minutes, gap_check_minutes=r.gap_check_minutes)
    frames = prepare_frames(closed, clock, settings)
    return evaluate(symbol, frames, bid, ask, spec, value_per_unit, state, settings, report, now,
                    tick_time, source, notes)


def evaluate_snapshot(snap: MarketSnapshot, state: RiskState, settings: Settings) -> Signal:
    return evaluate_bars(snap.symbol, snap.bars_1m, snap.now, snap.bid, snap.ask, snap.spec,
                         snap.value_per_unit, state, settings, snap.tick_time, snap.source, snap.notes)
