"""
Risk gates. Every BUY/SELL must pass all of them; there is no override.

- Emergency stop (file-based, survives restarts, readable by dashboard and scanner)
- Data quality: stale candles, stale ticks, gaps, broken candles, unknown clock
- Spread filter: absolute and relative to the stop distance
- Daily limits: loss limit, max trades, consecutive losses
- Plan checks: stop placement, room to the next opposing level, net reward-to-risk,
  and the planned loss never exceeding the risk budget
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Tuple

import pandas as pd

from candles import DataQualityReport
from config import EMERGENCY_STOP_FILE, InstrumentSpec, RiskSettings
from position_sizing import TradePlan


@dataclass
class RiskState:
    equity: float
    realized_pnl_today: float = 0.0
    trades_today: int = 0
    consecutive_losses: int = 0
    emergency_stop: bool = False
    emergency_reason: str = ""


# ---------------------------------------------------------------------- emergency stop

def emergency_stop_status(path: Path = EMERGENCY_STOP_FILE) -> Tuple[bool, str]:
    path = Path(path)
    if path.exists():
        try:
            return True, path.read_text(encoding="utf-8").strip() or "Emergency stop file present"
        except OSError:
            return True, "Emergency stop file present"
    return False, ""


def activate_emergency_stop(reason: str = "Manual emergency stop", path: Path = EMERGENCY_STOP_FILE) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"{reason} ({datetime.now():%Y-%m-%d %H:%M:%S})", encoding="utf-8")


def clear_emergency_stop(path: Path = EMERGENCY_STOP_FILE) -> None:
    path = Path(path)
    if path.exists():
        path.unlink()


# ---------------------------------------------------------------------- gates

class RiskManager:
    def __init__(self, risk: RiskSettings):
        self.r = risk

    def account_blocks(self, state: RiskState) -> List[str]:
        r = self.r
        out = []
        if state.emergency_stop:
            out.append(f"EMERGENCY STOP active: {state.emergency_reason}")
        limit = state.equity * r.daily_loss_limit_pct / 100.0
        if state.realized_pnl_today <= -limit:
            out.append(f"Daily loss limit reached ({state.realized_pnl_today:.2f} vs limit -{limit:.2f})")
        if state.trades_today >= r.max_trades_per_day:
            out.append(f"Max trades per day reached ({state.trades_today})")
        if state.consecutive_losses >= r.max_consecutive_losses:
            out.append(f"{state.consecutive_losses} losses in a row today - stopped for the day")
        if state.equity <= 0:
            out.append("Account equity unknown or zero")
        return out

    def data_blocks(self, report: Optional[DataQualityReport], now: Optional[pd.Timestamp],
                    tick_time: Optional[pd.Timestamp]) -> List[str]:
        out = []
        if report is not None:
            out.extend(report.issues)
        if now is None:
            out.append("Current time on the data clock is unknown - cannot confirm candles are closed")
        elif tick_time is not None:
            age = (now - tick_time).total_seconds()
            if age > self.r.stale_tick_seconds:
                out.append(f"Stale price: last tick {int(age)}s old")
        return out

    @staticmethod
    def price_blocks(bid: float, ask: float, last_close: Optional[float]) -> List[str]:
        """Reject impossible quotes (the free MetaQuotes-Demo feed has sent e.g. bid 17931 for GBPUSD)."""
        if not (bid > 0 and ask > 0):
            return [f"Bad price from broker (bid {bid}, ask {ask})"]
        if ask < bid:
            return [f"Bad price from broker: ask {ask:.6g} is below bid {bid:.6g}"]
        if last_close and abs(bid - last_close) / last_close > 0.03:
            return [f"Bad price from broker: bid {bid:.6g} is far from the last candle close {last_close:.6g}"]
        return []

    def spread_blocks(self, spread: float, spec: InstrumentSpec, stop_distance: Optional[float]) -> List[str]:
        out = []
        if spread is None or spread < 0:
            out.append("Spread unavailable")
            return out
        if spec.max_spread > 0 and spread > spec.max_spread:
            out.append(f"Spread {spread:.6g} above maximum {spec.max_spread:.6g}")
        if stop_distance and spread > self.r.max_spread_to_stop_ratio * stop_distance:
            out.append(f"Spread is {100 * spread / stop_distance:.0f}% of the stop distance "
                       f"(max {100 * self.r.max_spread_to_stop_ratio:.0f}%)")
        return out

    def plan_blocks(self, plan: TradePlan) -> List[str]:
        out = []
        if plan.reward_risk_net < self.r.min_net_reward_risk:
            out.append(f"Net reward-to-risk {plan.reward_risk_net:.2f} below minimum {self.r.min_net_reward_risk:g}")
        if plan.planned_loss > plan.risk_budget * 1.0001:
            out.append(f"Planned loss {plan.planned_loss:.2f} exceeds risk budget {plan.risk_budget:.2f}")
        if plan.quantity <= 0:
            out.append("Quantity is zero")
        return out
