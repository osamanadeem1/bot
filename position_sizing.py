"""
Position sizing and profit/loss planning.

    Risk budget   = Equity x Risk% / 100
    Position size = Risk budget / estimated loss per unit at the stop

"Loss per unit" uses the instrument's contract size, tick value, the account
currency conversion, assumed slippage and round-trip commission. The size is
rounded DOWN to the broker's volume step; if even the minimum size would risk
more than the budget, the trade is rejected.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from decimal import ROUND_FLOOR, Decimal
from typing import Dict, List, Optional

from config import InstrumentSpec


def quote_to_account_rate(spec: InstrumentSpec, price: float, account_ccy: str,
                          fx_rates: Dict[str, float]) -> float:
    """Rate that converts an amount in the instrument's quote currency to the account currency."""
    q = spec.quote_currency.upper()
    a = account_ccy.upper()
    if q == a:
        return 1.0
    if spec.base_currency.upper() == a and price > 0:
        return 1.0 / price  # e.g. USDJPY in a USD account
    if q in fx_rates and a in fx_rates and fx_rates[a] > 0:
        return fx_rates[q] / fx_rates[a]
    raise ValueError(f"No conversion rate from {q} to {a}; add it to risk.fx_rates_to_account")


def value_per_price_unit(spec: InstrumentSpec, price: float, account_ccy: str,
                         fx_rates: Dict[str, float]) -> float:
    """Account-currency P&L of a 1.0 price move on 1 lot (or 1 share)."""
    return spec.contract_size * quote_to_account_rate(spec, price, account_ccy, fx_rates)


def round_down_to_step(quantity: float, step: float) -> float:
    if quantity <= 0:
        return 0.0
    d_step = Decimal(str(step))
    units = (Decimal(repr(float(quantity))) / d_step).to_integral_value(rounding=ROUND_FLOOR)
    return float(units * d_step)


@dataclass
class SizingResult:
    ok: bool
    quantity: float
    risk_budget: float
    loss_per_unit: float  # account currency lost per 1 lot/share if the stop is hit (incl. costs)
    raw_quantity: float
    notes: List[str] = field(default_factory=list)
    reject_reason: str = ""


def size_position(
    equity: float,
    risk_pct: float,
    entry: float,
    stop: float,
    spec: InstrumentSpec,
    value_per_unit: float,
    max_leverage: Optional[float] = None,
) -> SizingResult:
    risk_budget = equity * risk_pct / 100.0
    stop_distance = abs(entry - stop)
    if equity <= 0:
        return SizingResult(False, 0.0, risk_budget, 0.0, 0.0, reject_reason="Account equity is zero or negative")
    if stop_distance <= 0:
        return SizingResult(False, 0.0, risk_budget, 0.0, 0.0, reject_reason="Stop-loss equals entry price")
    loss_per_unit = (stop_distance + spec.slippage) * value_per_unit + 2.0 * spec.commission_per_unit_side
    raw = risk_budget / loss_per_unit
    qty = round_down_to_step(raw, spec.volume_step)
    notes: List[str] = []
    if qty > spec.volume_max:
        qty = round_down_to_step(spec.volume_max, spec.volume_step)
        notes.append(f"Capped at broker maximum {spec.volume_max} {spec.unit}")
    if max_leverage:
        notional_per_unit = value_per_unit * entry
        if notional_per_unit > 0:
            max_qty = round_down_to_step(max_leverage * equity / notional_per_unit, spec.volume_step)
            if qty > max_qty:
                qty = max_qty
                notes.append(f"Reduced to keep leverage at or below {max_leverage:g}x")
    if qty < spec.volume_min - 1e-12:
        min_loss = spec.volume_min * loss_per_unit
        return SizingResult(
            False, 0.0, risk_budget, loss_per_unit, raw, notes,
            reject_reason=(f"Minimum size {spec.volume_min} {spec.unit} would risk "
                           f"{min_loss:.2f}, above the {risk_budget:.2f} risk budget"),
        )
    return SizingResult(True, qty, risk_budget, loss_per_unit, raw, notes)


@dataclass
class TradePlan:
    direction: str  # BUY or SELL
    entry: float
    stop: float
    target: float
    stop_distance: float
    target_distance: float
    quantity: float
    unit: str
    risk_budget: float
    planned_loss: float  # if the stop is hit, incl. slippage and commission
    planned_profit: float  # if the target is hit, after commission
    spread_cost: float  # already inside the entry price; shown for transparency
    commission: float  # round trip
    slippage_cost: float
    total_costs: float
    reward_risk_price: float  # target distance / stop distance
    reward_risk_net: float  # planned profit / planned loss
    risk_pct_of_equity: float
    notional: float
    leverage: float
    value_per_unit: float
    notes: List[str] = field(default_factory=list)
    breakeven_trigger: Optional[float] = None  # move stop to entry when price reaches this
    partial_target: Optional[float] = None  # take partial_quantity off here, then move the stop to entry
    partial_quantity: Optional[float] = None

    def to_dict(self) -> dict:
        return asdict(self)


def build_trade_plan(direction: str, entry: float, stop: float, target: float, quantity: float,
                     spec: InstrumentSpec, value_per_unit: float, spread: float, equity: float,
                     risk_budget: float, notes: Optional[List[str]] = None) -> TradePlan:
    stop_distance = abs(entry - stop)
    target_distance = abs(target - entry)
    commission = 2.0 * spec.commission_per_unit_side * quantity
    slippage_cost = spec.slippage * value_per_unit * quantity
    spread_cost = spread * value_per_unit * quantity
    planned_loss = stop_distance * value_per_unit * quantity + slippage_cost + commission
    planned_profit = target_distance * value_per_unit * quantity - commission
    notional = value_per_unit * entry * quantity
    return TradePlan(
        direction=direction, entry=entry, stop=stop, target=target,
        stop_distance=stop_distance, target_distance=target_distance,
        quantity=quantity, unit=spec.unit, risk_budget=risk_budget,
        planned_loss=planned_loss, planned_profit=planned_profit,
        spread_cost=spread_cost, commission=commission, slippage_cost=slippage_cost,
        total_costs=spread_cost + commission + slippage_cost,
        reward_risk_price=target_distance / stop_distance if stop_distance else 0.0,
        reward_risk_net=planned_profit / planned_loss if planned_loss > 0 else 0.0,
        risk_pct_of_equity=100.0 * planned_loss / equity if equity > 0 else 0.0,
        notional=notional, leverage=notional / equity if equity > 0 else 0.0,
        value_per_unit=value_per_unit, notes=list(notes or []),
    )


# ---------------------------------------------------------------- scenario maths (calculator tab)

def break_even_win_rate(reward_risk: float, cost_in_r: float = 0.0) -> float:
    """Win rate needed to break even. cost_in_r = costs per trade as a fraction of 1R."""
    win = reward_risk - cost_in_r
    loss = 1.0 + cost_in_r
    if win <= 0:
        return 1.0
    return loss / (win + loss)


def scenario(equity: float, risk_pct: float, reward_risk: float, win_rate: float, trades: int,
             cost_per_trade: float = 0.0) -> dict:
    """Fixed-risk, independent-outcome scenario. Not a forecast."""
    risk = equity * risk_pct / 100.0
    reward = risk * reward_risk
    expected_per_trade = win_rate * reward - (1 - win_rate) * risk - cost_per_trade
    return {
        "risk_per_trade": risk,
        "profit_per_win": reward,
        "break_even_win_rate": break_even_win_rate(reward_risk, cost_per_trade / risk if risk else 0.0),
        "expected_per_trade": expected_per_trade,
        "expected_total": expected_per_trade * trades,
    }
