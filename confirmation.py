"""
Combines the five timeframe checks into one setup decision.

The 15m trend picks the only direction the bot may consider. Every lower
timeframe must then agree. If any check fails the result is NO TRADE.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

import numpy as np

from config import StrategySettings
from strategy import (BUY, SELL, Check, Frame, check_agreement, check_confirmation, check_setup,
                      check_trend, check_trigger)


@dataclass
class SetupResult:
    direction: Optional[str]  # BUY / SELL when every check passed, else None
    considered: Optional[str]  # direction the checks were evaluated for
    passed: bool
    checks: List[Check] = field(default_factory=list)
    setup_type: str = ""
    stop_reference: Optional[float] = None
    atr_5m: float = float("nan")
    reasons: List[str] = field(default_factory=list)


def evaluate_setup(frames: Dict[str, Frame], cfg: StrategySettings) -> SetupResult:
    f15 = frames["15m"]
    f60 = frames.get("60m")
    bull = check_trend(f15, cfg, BUY, f60)
    bear = check_trend(f15, cfg, SELL, f60)
    if bull.passed:
        considered, trend = BUY, bull
    elif bear.passed:
        considered, trend = SELL, bear
    else:
        # No trend: still show the lower timeframes for the side that came closest.
        score_bull = sum(ok for _, ok in bull.items)
        score_bear = sum(ok for _, ok in bear.items)
        considered, trend = (BUY, bull) if score_bull >= score_bear else (SELL, bear)

    checks = [
        trend,
        check_agreement(frames["10m"], cfg, considered),
        check_setup(frames["5m"], cfg, considered),
        check_confirmation(frames["3m"], cfg, considered),
        check_trigger(frames["1m"], cfg, considered),
    ]
    reasons = []
    if not bull.passed and not bear.passed:
        reasons.append(f"15m trend is not clearly bullish or bearish ({trend.detail.replace('Failed: ', 'failed: ')})")
    for c in checks:
        if not c.passed and not (c.timeframe == "15m" and reasons):
            reasons.append(f"{c.timeframe} {c.purpose.lower()}: {c.detail}")
    passed = all(c.passed for c in checks)
    setup = checks[2]
    return SetupResult(
        direction=considered if passed else None,
        considered=considered,
        passed=passed,
        checks=checks,
        setup_type=str(setup.info.get("setup_type", "")),
        stop_reference=setup.info.get("stop_reference"),
        atr_5m=float(setup.info.get("atr", np.nan)),
        reasons=reasons,
    )
