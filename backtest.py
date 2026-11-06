"""
Event-driven backtester that uses the SAME signal engine as live scanning.

How it avoids looking into the future:
- Indicators are causal (each row uses only earlier rows).
- At each 1-minute close, only candles whose period has ENDED are visible
  to the engine (same rule as live).
- Orders fill at the NEXT 1-minute candle's open, never at the signal candle.

Costs included: spread (BUY fills at the ask, SELL at the bid; exits likewise; never less than the
instrument's typical spread, because some demo feeds record zero),
round-trip commission, and slippage on entries, stops and time exits.
If a candle touches both the stop and the target, the stop is assumed first.
Targets fill at the target price (no positive slippage).

Validation helpers: in-sample vs out-of-sample split and walk-forward windows.
The rules are fixed (nothing is tuned here), so walk-forward shows whether
results are stable across successive periods.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional

import numpy as np
import pandas as pd

from candles import normalize_bars, resample_bars, validate_bars
from config import TIMEFRAMES, InstrumentSpec, Settings
from confirmation import evaluate_setup
from engine import NO_TRADE, evaluate
from indicators import add_indicators
from position_sizing import value_per_price_unit
from risk_manager import RiskState
from strategy import BUY, Frame

NS_PER_MIN = 60_000_000_000


@dataclass
class Precomputed:
    bars: pd.DataFrame
    frames: Dict[str, Frame]
    ends: Dict[str, np.ndarray]
    gap_ok: np.ndarray
    report: object


def precompute(bars_1m: pd.DataFrame, spec: InstrumentSpec, settings: Settings) -> Precomputed:
    bars = normalize_bars(bars_1m, spec.typical_spread)
    bars = bars.drop_duplicates("time", keep="last").reset_index(drop=True)
    # Some demo servers record a zero spread on most candles. Never assume trading is cheaper than normal.
    bars["spread"] = bars["spread"].clip(lower=spec.typical_spread)
    report = validate_bars(bars, max_gap_minutes=settings.risk.max_gap_minutes,
                           gap_check_minutes=settings.risk.gap_check_minutes)
    frames, ends = {}, {}
    for tf, minutes in TIMEFRAMES.items():
        df = add_indicators(resample_bars(bars, minutes), settings.strategy)
        frames[tf] = Frame.from_df(df, tf)
        ends[tf] = df["time"].to_numpy().astype("datetime64[ns]").astype(np.int64) + minutes * NS_PER_MIN
    # gap_ok[i]: no gap larger than max_gap_minutes within the last gap_check_minutes before bar i
    t = bars["time"].to_numpy().astype("datetime64[ns]").astype(np.int64)
    diffs = np.diff(t, prepend=t[0]) // NS_PER_MIN
    big = diffs > settings.risk.max_gap_minutes
    last_big = pd.Series(np.where(big, t, np.nan)).ffill().to_numpy()
    window = settings.risk.gap_check_minutes * NS_PER_MIN
    gap_ok = np.isnan(last_big) | ((t - np.nan_to_num(last_big)) > window)
    return Precomputed(bars, frames, ends, gap_ok, report)


@dataclass
class BacktestResult:
    symbol: str
    trades: pd.DataFrame
    equity_curve: pd.DataFrame
    metrics: dict
    data_report: object
    start: Optional[pd.Timestamp] = None
    end: Optional[pd.Timestamp] = None
    notes: List[str] = field(default_factory=list)


def compute_metrics(trades: pd.DataFrame, start_equity: float) -> dict:
    n = len(trades)
    if n == 0:
        return {"trades": 0, "win_rate_pct": None, "net_pnl": 0.0, "expectancy": None, "profit_factor": None,
                "max_drawdown": 0.0, "max_drawdown_pct": 0.0, "avg_r": None, "gross_profit": 0.0,
                "gross_loss": 0.0, "return_pct": 0.0, "final_equity": start_equity, "wins": 0, "losses": 0}
    pnl = trades["pnl"].to_numpy(float)
    wins = pnl[pnl > 0]
    losses = pnl[pnl <= 0]
    gross_profit = float(wins.sum())
    gross_loss = float(-losses.sum())
    equity = start_equity + np.cumsum(pnl)
    peaks = np.maximum.accumulate(np.concatenate([[start_equity], equity]))[1:]
    dd = peaks - equity
    i = int(np.argmax(dd)) if len(dd) else 0
    return {
        "trades": n,
        "wins": int(len(wins)),
        "losses": int(len(losses)),
        "win_rate_pct": 100.0 * len(wins) / n,
        "net_pnl": float(pnl.sum()),
        "expectancy": float(pnl.mean()),
        "profit_factor": (gross_profit / gross_loss) if gross_loss > 0 else None,
        "gross_profit": gross_profit,
        "gross_loss": gross_loss,
        "avg_win": float(wins.mean()) if len(wins) else 0.0,
        "avg_loss": float(losses.mean()) if len(losses) else 0.0,
        "avg_r": float(trades["r_multiple"].mean()),
        "max_drawdown": float(dd.max()) if len(dd) else 0.0,
        "max_drawdown_pct": float(100.0 * dd[i] / peaks[i]) if len(dd) and peaks[i] > 0 else 0.0,
        "final_equity": float(equity[-1]),
        "return_pct": float(100.0 * pnl.sum() / start_equity),
    }


def run_backtest(
    bars_1m: pd.DataFrame,
    spec: InstrumentSpec,
    settings: Settings,
    start_equity: Optional[float] = None,
    trade_start: Optional[pd.Timestamp] = None,
    trade_end: Optional[pd.Timestamp] = None,
    pre: Optional[Precomputed] = None,
    progress: Optional[Callable[[float], None]] = None,
    minutes_per_day: int = 1440,
) -> BacktestResult:
    """minutes_per_day: length of a trading day on the bars' clock, for the daily limits
    (288 for the 5-minute swing profile, whose engine clock runs 5x slower; see profiles.py)."""
    risk = settings.risk
    start_equity = float(start_equity if start_equity is not None else risk.account_equity)
    pre = pre or precompute(bars_1m, spec, settings)
    bars = pre.bars
    n = len(bars)
    t = bars["time"].to_numpy().astype("datetime64[ns]").astype(np.int64)
    o, h, l, c = (bars[k].to_numpy(float) for k in ("open", "high", "low", "close"))
    spr = bars["spread"].to_numpy(float)
    slip = spec.slippage
    comm_side = spec.commission_per_unit_side
    ts_start = pd.Timestamp(trade_start).value if trade_start is not None else -np.inf
    ts_end = pd.Timestamp(trade_end).value if trade_end is not None else np.inf
    max_hold = risk.max_hold_minutes * NS_PER_MIN
    max_gap = risk.max_gap_minutes * NS_PER_MIN
    be_r = risk.breakeven_at_r

    equity = start_equity
    day = None
    state = RiskState(equity=equity)
    pos = None
    trades: List[dict] = []
    curve = [(pd.Timestamp(t[0]) if n else None, equity)]
    notes = []

    def take_partial(price: float):
        """Close the first part at the partial target, then move the stop to entry (as the live order pair does)."""
        nonlocal equity
        sign = 1.0 if pos["side"] == BUY else -1.0
        q1 = pos["pq"]
        pnl1 = sign * (price - pos["entry"]) * q1 * pos["vpu"] - 2.0 * comm_side * q1
        equity += pnl1
        state.realized_pnl_today += pnl1
        pos["part_pnl"] += pnl1
        pos["qty"] -= q1
        pos["stop"], pos["be"], pos["partial_done"] = pos["entry"], True, True

    def close_position(k: int, price: float, reason: str):
        nonlocal equity, pos
        sign = 1.0 if pos["side"] == BUY else -1.0
        rest = sign * (price - pos["entry"]) * pos["qty"] * pos["vpu"] - 2.0 * comm_side * pos["qty"]
        equity += rest
        state.realized_pnl_today += rest
        pnl = rest + pos["part_pnl"]
        if pos["partial_done"]:
            reason = f"partial+{reason}"
        state.consecutive_losses = state.consecutive_losses + 1 if pnl < 0 else 0
        trades.append({
            "symbol": spec.symbol, "side": pos["side"], "setup": pos["setup"],
            "signal_time": pd.Timestamp(pos["signal_t"]), "entry_time": pd.Timestamp(t[pos["i"]]),
            "exit_time": pd.Timestamp(t[k]), "entry": pos["entry"], "exit": price, "stop": pos["stop0"],
            "target": pos["target"], "quantity": pos["qty0"], "planned_loss": pos["planned_loss"],
            "pnl": pnl, "r_multiple": pnl / pos["planned_loss"] if pos["planned_loss"] > 0 else 0.0,
            "exit_reason": reason, "minutes_held": int((t[k] - t[pos["i"]]) // NS_PER_MIN) + 1,
        })
        curve.append((pd.Timestamp(t[k]) + pd.Timedelta(minutes=1), equity))
        pos = None

    step = max(1, n // 100)
    for i in range(n - 1):
        if progress and i % step == 0:
            progress(i / n)
        bar_day = (t[i] + NS_PER_MIN) // (minutes_per_day * NS_PER_MIN)
        if bar_day != day:  # daily limits reset at midnight on the data clock
            day = bar_day
            state.realized_pnl_today = 0.0
            state.trades_today = 0
            state.consecutive_losses = 0
        # ---- manage an open position on this candle
        if pos is not None and pos["i"] <= i:
            if pos["side"] == BUY:
                if o[i] <= pos["stop"]:
                    close_position(i, o[i] - slip, "stop (gap)")
                elif l[i] <= pos["stop"]:
                    close_position(i, pos["stop"] - slip, "breakeven" if pos["be"] else "stop")
                else:
                    if pos["ptarget"] is not None and not pos["partial_done"] and h[i] >= pos["ptarget"]:
                        take_partial(pos["ptarget"])
                    if h[i] >= pos["target"]:
                        close_position(i, pos["target"], "target")
                    elif t[i] - t[pos["i"]] >= max_hold:
                        close_position(i, c[i] - slip, "time")
            else:
                ao, ah, al, ac = o[i] + spr[i], h[i] + spr[i], l[i] + spr[i], c[i] + spr[i]
                if ao >= pos["stop"]:
                    close_position(i, ao + slip, "stop (gap)")
                elif ah >= pos["stop"]:
                    close_position(i, pos["stop"] + slip, "breakeven" if pos["be"] else "stop")
                else:
                    if pos["ptarget"] is not None and not pos["partial_done"] and al <= pos["ptarget"]:
                        take_partial(pos["ptarget"])
                    if al <= pos["target"]:
                        close_position(i, pos["target"], "target")
                    elif t[i] - t[pos["i"]] >= max_hold:
                        close_position(i, ac + slip, "time")
            if pos is not None and be_r > 0 and not pos["be"]:
                if pos["side"] == BUY:
                    hit = h[i] >= pos["entry"] + be_r * pos["risk"]
                else:
                    hit = l[i] + spr[i] <= pos["entry"] - be_r * pos["risk"]
                if hit:
                    pos["stop"], pos["be"] = pos["entry"], True
        if pos is not None:
            continue
        # ---- look for a new signal at this candle's close
        if not (ts_start <= t[i] < ts_end) or not pre.gap_ok[i]:
            continue
        now_ns = t[i] + NS_PER_MIN
        frames = {tf: f.upto(int(np.searchsorted(pre.ends[tf], now_ns, side="right")))
                  for tf, f in pre.frames.items()}
        if not evaluate_setup(frames, settings.strategy).passed:
            continue  # fast path: the full engine below re-checks everything
        state.equity = equity
        bid, ask = c[i], c[i] + spr[i]
        vpu = value_per_price_unit(spec, bid, risk.account_currency, risk.fx_rates_to_account)
        sig = evaluate(spec.symbol, frames, bid, ask, spec, vpu, state, settings, now=pd.Timestamp(now_ns),
                       check_data=False)
        if sig.decision == NO_TRADE:
            continue
        j = i + 1
        if t[j] - t[i] > max_gap:
            continue  # market closed / data gap before the fill
        plan = sig.plan
        if plan.direction == BUY:
            fill = o[j] + spr[j] + slip
            if fill <= plan.stop or fill >= plan.target:
                continue
        else:
            fill = o[j] - slip
            if fill >= plan.stop or fill <= plan.target:
                continue
        pos = {"i": j, "side": plan.direction, "entry": fill, "stop": plan.stop, "target": plan.target,
               "qty": plan.quantity, "vpu": plan.value_per_unit, "planned_loss": plan.planned_loss,
               "setup": sig.setup_type, "signal_t": t[i], "stop0": plan.stop, "be": False,
               "risk": abs(fill - plan.stop), "qty0": plan.quantity, "ptarget": plan.partial_target,
               "pq": plan.partial_quantity or 0.0, "part_pnl": 0.0, "partial_done": False}
        if pos["ptarget"] is not None and ((plan.direction == BUY and fill >= pos["ptarget"]) or
                                           (plan.direction != BUY and fill <= pos["ptarget"])):
            pos["ptarget"] = None  # filled beyond the first take-profit: MT5 would refuse it, so one order
        state.trades_today += 1

    if pos is not None and n:
        k = n - 1
        price = c[k] - slip if pos["side"] == BUY else c[k] + spr[k] + slip
        close_position(k, price, "end of data")
        notes.append("Last trade closed at the end of the data")
    if progress:
        progress(1.0)

    trades_df = pd.DataFrame(trades)
    curve_df = pd.DataFrame(curve, columns=["time", "equity"])
    return BacktestResult(
        symbol=spec.symbol, trades=trades_df, equity_curve=curve_df,
        metrics=compute_metrics(trades_df, start_equity), data_report=pre.report,
        start=pd.Timestamp(trade_start) if trade_start is not None else (bars["time"].iloc[0] if n else None),
        end=pd.Timestamp(trade_end) if trade_end is not None else (bars["time"].iloc[-1] if n else None),
        notes=notes,
    )


def in_out_of_sample(bars_1m: pd.DataFrame, spec: InstrumentSpec, settings: Settings,
                     split: float = 0.7, start_equity: Optional[float] = None):
    pre = precompute(bars_1m, spec, settings)
    times = pre.bars["time"]
    cut = times.iloc[int(len(times) * split)]
    ins = run_backtest(pre.bars, spec, settings, start_equity, None, cut, pre)
    oos = run_backtest(pre.bars, spec, settings, start_equity, cut, None, pre)
    return ins, oos


def walk_forward(bars_1m: pd.DataFrame, spec: InstrumentSpec, settings: Settings,
                 windows: int = 4, start_equity: Optional[float] = None) -> pd.DataFrame:
    pre = precompute(bars_1m, spec, settings)
    times = pre.bars["time"]
    edges = [times.iloc[int(k * (len(times) - 1) / windows)] for k in range(windows + 1)]
    rows = []
    for k in range(windows):
        res = run_backtest(pre.bars, spec, settings, start_equity, edges[k],
                           edges[k + 1] if k < windows - 1 else None, pre)
        m = res.metrics
        rows.append({"window": k + 1, "from": edges[k], "to": edges[k + 1], "trades": m["trades"],
                     "win_rate_pct": m["win_rate_pct"], "net_pnl": m["net_pnl"], "expectancy": m["expectancy"],
                     "profit_factor": m["profit_factor"], "max_drawdown_pct": m["max_drawdown_pct"]})
    return pd.DataFrame(rows)


def split_report(trades: pd.DataFrame, first: pd.Timestamp, last: pd.Timestamp, start_equity: float,
                 split: float = 0.7, windows: int = 4):
    """In-sample / out-of-sample metrics and walk-forward windows from ONE run's trades, split by signal time.
    Works for every strategy speed. Returns (in_sample, out_of_sample, walk_forward DataFrame)."""
    cut = first + split * (last - first)
    ts = trades["signal_time"] if len(trades) else pd.Series(dtype="datetime64[ns]")
    ins = compute_metrics(trades[ts <= cut], start_equity)
    oos = compute_metrics(trades[ts > cut], start_equity)
    rows = []
    edges = [first + k * (last - first) / max(windows, 1) for k in range(windows + 1)]
    for k in range(windows):
        part = trades[(ts > edges[k]) & (ts <= edges[k + 1])] if k else trades[ts <= edges[1]]
        m = compute_metrics(part, start_equity)
        rows.append({"window": k + 1, "from": edges[k], "to": edges[k + 1], "trades": m["trades"],
                     "win_rate_pct": m["win_rate_pct"], "avg_r": m["avg_r"], "net_pnl": m["net_pnl"],
                     "profit_factor": m["profit_factor"], "max_drawdown_pct": m["max_drawdown_pct"]})
    return ins, oos, pd.DataFrame(rows)


def results_by(trades: pd.DataFrame, column: str) -> pd.DataFrame:
    """Performance split by a column such as symbol, side, setup or exit_reason."""
    if trades.empty:
        return pd.DataFrame()
    g = trades.groupby(column)["pnl"]
    out = pd.DataFrame({
        "trades": g.size(),
        "win_rate_pct": g.apply(lambda s: 100.0 * (s > 0).mean()),
        "net_pnl": g.sum(),
        "expectancy": g.mean(),
    })
    return out.reset_index()
