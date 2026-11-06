"""
Manual orders through MetaTrader 5: sent only when you click Place order and then
Confirm on the dashboard. Nothing in this file runs by itself, and the scanner never
calls it.

Every order
- carries the signal's stop-loss and target, so MT5 closes the trade at one of them
  even if the dashboard is closed,
- is sized again from the live price, so the money at risk never exceeds the plan,
- is refused unless all of these pass at the moment you confirm:
  demo account (unless trading.allow_real_account is true), MT5 Algo Trading on,
  emergency stop off, daily loss / trade-count / losing-streak limits not reached,
  no trade from the same strategy already open on that market, at most trading.max_open_positions open,
  fresh prices, price still close to the planned entry, and MT5's own order check.

Closing a trade is always allowed (it only reduces risk).
"""
from __future__ import annotations

import functools
import re
import time
from contextlib import nullcontext
from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Optional, Tuple

import pandas as pd

from market_data import MarketDataError
from position_sizing import round_down_to_step, size_position
from risk_manager import RiskManager, RiskState
from strategy import BUY, SELL, fmt

# MetaTrader 5 constants (numbers are fixed by the MT5 API; used directly so the module works with any version)
ACTION_DEAL, ACTION_SLTP, TYPE_BUY, TYPE_SELL, TIME_GTC = 1, 6, 0, 1, 0
FILL_FOK, FILL_IOC, FILL_RETURN = 0, 1, 2
DEAL_ENTRY_OUT, DEAL_ENTRY_OUT_BY = 1, 3
FILLED = {10009, 10010}  # done, done partially
CHECK_OK = {0, 10009}
RES_OK = 1  # last_error() code meaning "no error"

RETCODES = {
    10004: "the broker re-quoted the price - try again",
    10006: "the broker rejected the order",
    10007: "the order was cancelled",
    10013: "the order is invalid",
    10014: "the lot size is not allowed for this symbol",
    10015: "the price is invalid",
    10016: "the stop-loss or target is invalid (too close to the price?)",
    10017: "trading is disabled for this symbol or account",
    10018: "the market is closed",
    10019: "there is not enough free margin",
    10020: "prices changed - try again",
    10021: "there are no prices to fill the order",
    10024: "too many requests - wait a moment",
    10026: "automatic trading is disabled by the broker's server",
    10027: "Algo Trading is switched off in MT5 - click the Algo Trading button on the MT5 toolbar so it turns green",
    10030: "the broker does not support this fill type",
    10031: "there is no connection to the broker server",
    10034: "the volume limit for this symbol is reached",
    10040: "the broker's limit on open positions is reached",
}

TICKET_NOTE = re.compile(r"MT5 ticket (\d+)")
CLOSE_REASONS = {0: "closed in MT5", 1: "closed in MT5 (phone)", 2: "closed in MT5 (web)", 3: "closed from the desk",
                 4: "hit its stop-loss", 5: "hit its target", 6: "closed by the broker (stop out)"}


def retcode_text(code) -> str:
    try:
        return RETCODES.get(int(code), f"MT5 return code {code}")
    except (TypeError, ValueError):
        return f"MT5 return code {code}"


def _locked(method):
    """One MT5 call at a time (shares the data source's lock)."""
    @functools.wraps(method)
    def wrapper(self, *args, **kwargs):
        with getattr(self.src, "lock", None) or nullcontext():
            return method(self, *args, **kwargs)
    return wrapper


class OrderError(RuntimeError):
    """An order was refused, by these safety checks or by MT5. The message says why, in plain words."""


@dataclass
class OrderCheck:
    symbol: str
    side: str
    blocks: List[str] = field(default_factory=list)
    request: Optional[dict] = None
    volume: float = 0.0
    unit: str = "lots"
    price: float = 0.0
    stop: float = 0.0
    target: float = 0.0
    planned_loss: float = 0.0  # account currency if the stop is hit, incl. commission
    planned_profit: float = 0.0  # account currency if the target is hit, after commission
    notes: List[str] = field(default_factory=list)
    legs: List[Tuple[float, float]] = field(default_factory=list)  # (volume, take-profit) per order
    partial_target: Optional[float] = None  # first half's (closer) take-profit, when the order is split
    partial_profit: float = 0.0  # account currency the first half makes at its take-profit

    @property
    def ok(self) -> bool:
        return not self.blocks and self.request is not None


@dataclass
class Fill:
    ticket: int  # MT5 position id (the opening order's ticket)
    symbol: str
    side: str
    volume: float
    price: float
    stop: float
    target: float
    note: str = ""
    part: str = ""  # "1/2", "2/2" for a split order
    legs: List["Fill"] = field(default_factory=list)  # every order sent for this signal (first one included)


class Trader:
    def __init__(self, source, settings):
        if getattr(source, "name", "") != "mt5" or not hasattr(source, "mt5"):
            raise OrderError("Orders need MetaTrader 5 as the data source.")
        self.src = source
        self.mt5 = source.mt5
        self.settings = settings
        self.t = settings.trading

    # ------------------------------------------------------------------ state
    def account_mode(self) -> str:
        return self.src.account()["mode"]

    def _terminal_blocks(self) -> List[str]:
        try:
            self.src._ensure_connected()
        except MarketDataError as exc:
            return [str(exc)]
        term = self.mt5.terminal_info()
        if term is None:
            return ["MetaTrader 5 is not responding. Is it open?"]
        out = []
        if not getattr(term, "connected", True):
            out.append("MT5 has lost its connection to the broker server. Log in again in MT5.")
        if not getattr(term, "trade_allowed", False):
            out.append("Algo Trading is switched off in MT5. Click the Algo Trading button on the MT5 toolbar "
                       "so it turns green, then try again.")
        return out

    @_locked
    def readiness(self) -> List[str]:
        """Reasons the desk cannot open trades right now (empty list = ready)."""
        out = self._terminal_blocks()
        if out and "not responding" in out[0]:
            return out
        acct = self.mt5.account_info()
        if acct is None:
            return out + ["MT5 account details are not available. Is MT5 logged in?"]
        mode = {0: "demo", 1: "contest", 2: "real"}.get(getattr(acct, "trade_mode", -1), "unknown")
        if mode != "demo" and not self.t.allow_real_account:
            out.append(f"This MT5 login is a {mode} account, not a demo. Order buttons only work on demo accounts "
                       'unless you set "allow_real_account": true under "trading" in settings.json.')
        if not getattr(acct, "trade_allowed", True):
            out.append("Your broker does not allow trading on this login (logged in with a read-only password?).")
        if not getattr(acct, "trade_expert", True):
            out.append("Your broker does not allow orders from programs on this account.")
        return out

    def _all_positions(self) -> list:
        """Every open position in the account. Raises instead of guessing when MT5 does not answer."""
        res = self.mt5.positions_get()
        if res is None:
            err = self.mt5.last_error()
            code = err[0] if isinstance(err, tuple) and err else None
            if code != RES_OK:
                raise OrderError(f"MT5 did not return the list of open trades ({err}). Try again in a moment.")
            return []
        return list(res)

    @staticmethod
    def _pos_id(p) -> int:
        """The position identifier stays the same for the life of a trade (the ticket can change)."""
        return int(getattr(p, "identifier", 0) or p.ticket)

    def _our_name(self, broker_name: str) -> str:
        for ours, theirs in getattr(self.src, "_names", {}).items():
            if theirs == broker_name:
                return ours
        return broker_name

    @_locked
    def positions(self) -> List[dict]:
        """Open trades placed from this desk (other trades in your account are left alone)."""
        out = []
        for p in self._all_positions():
            if int(getattr(p, "magic", 0)) != self.t.magic_number:
                continue
            out.append({
                "ticket": int(p.ticket), "id": self._pos_id(p), "symbol": self._our_name(p.symbol),
                "broker_symbol": p.symbol,
                "side": BUY if p.type == TYPE_BUY else SELL, "volume": float(p.volume),
                "open_price": float(p.price_open), "stop": float(p.sl), "target": float(p.tp),
                "price": float(p.price_current), "profit": float(p.profit) + float(getattr(p, "swap", 0.0) or 0.0),
                "opened": pd.Timestamp(int(p.time), unit="s"),  # broker server clock
                "comment": str(getattr(p, "comment", "") or ""),
            })
        return out

    # ------------------------------------------------------------------ orders
    @staticmethod
    def _fill_types(info) -> List[int]:
        flags = int(getattr(info, "filling_mode", 0) or 0)
        first = [f for f, bit in ((FILL_FOK, 1), (FILL_IOC, 2)) if flags & bit]
        return first + [f for f in (FILL_FOK, FILL_IOC, FILL_RETURN) if f not in first]

    def _with_fill_type(self, request: dict, info, send: bool):
        """Run order_check (send=False) or order_send (send=True), trying the broker's fill types in turn."""
        call = self.mt5.order_send if send else self.mt5.order_check
        res = None
        for fill in self._fill_types(info):
            request["type_filling"] = fill
            res = call(request)
            if res is None or int(res.retcode) != 10030:
                break
        return res

    @_locked
    def check(self, sig, state: RiskState, prices_at: Optional[float] = None, tag: str = "") -> OrderCheck:
        """Everything that must be true before an order for this signal may be sent. Sends nothing."""
        side = sig.decision
        chk = OrderCheck(sig.symbol, side)
        p = sig.plan
        if side not in (BUY, SELL) or p is None:
            chk.blocks.append("There is no live signal for this market.")
            return chk
        chk.blocks += self.readiness()
        chk.blocks += RiskManager(self.settings.risk).account_blocks(state)
        if prices_at is not None:
            age = time.time() - prices_at
            if age > self.t.max_signal_age_seconds:
                chk.blocks.append(f"The prices behind this signal are {age:.0f} seconds old. Click Scan now.")
        if chk.blocks:
            return chk
        try:
            spec, value, info = self.src.broker_spec(sig.symbol)
        except MarketDataError as exc:
            chk.blocks.append(str(exc))
            return chk
        name = spec.broker_symbol
        chk.unit = spec.unit
        if spec.tick_size <= 0 or spec.volume_step <= 0:
            chk.blocks.append(f"MT5 returned an invalid tick size or lot step for {name}.")
            return chk
        try:
            open_now = self._all_positions()
        except OrderError as exc:
            chk.blocks.append(str(exc))
            return chk
        def strategy_of(pos) -> str:  # "Signal desk BUY swing 1/2" -> "swing"; "" for other trades
            words = str(getattr(pos, "comment", "")).split()
            return words[3] if len(words) > 3 and words[:2] == ["Signal", "desk"] else ""

        same = [pos for pos in open_now if pos.symbol == name and
                (not tag or int(getattr(pos, "magic", 0)) != self.t.magic_number or strategy_of(pos) in ("", tag))]
        if same:
            chk.blocks.append(f"A {sig.symbol} trade from these rules is already open. The desk allows one trade per "
                              "market for each strategy.")
        mine = [pos for pos in open_now if int(getattr(pos, "magic", 0)) == self.t.magic_number
                and not str(getattr(pos, "comment", "")).endswith("2/2")]  # the two halves count as one trade
        if len(mine) >= self.t.max_open_positions:
            chk.blocks.append(f"{len(mine)} trades from this desk are already open "
                              f"(limit {self.t.max_open_positions}, trading.max_open_positions).")
        tick = self.mt5.symbol_info_tick(name)
        if tick is None or tick.bid <= 0 or tick.ask <= 0:
            chk.blocks.append("MT5 has no live price for this market right now.")
            return chk
        offset = getattr(self.src, "_offset_hours", None)
        if offset is not None:  # tick.time is on the broker's clock
            tick_age = time.time() + offset * 3600 - float(tick.time)
            if tick_age > self.settings.risk.stale_tick_seconds:
                chk.blocks.append(f"MT5's last price is {tick_age:.0f} seconds old (market closed or feed frozen).")
        price = float(tick.ask if side == BUY else tick.bid)
        step = spec.tick_size

        def on_tick(x: float) -> float:
            return round(round(x / step) * step, spec.digits)

        stop, target = on_tick(p.stop), on_tick(p.target)
        chk.price, chk.stop, chk.target = price, stop, target
        allowed = self.t.max_price_move_fraction * p.stop_distance
        if abs(price - p.entry) > allowed:
            chk.blocks.append(f"Price has moved from {fmt(p.entry)} to {fmt(price)} since the signal, more than "
                              f"{self.t.max_price_move_fraction:.0%} of the stop distance. Wait for the next signal.")
        if (side == BUY and not stop < price < target) or (side == SELL and not target < price < stop):
            chk.blocks.append("Price is already beyond the stop-loss or the target.")
            return chk
        point = float(getattr(info, "point", 0.0) or step)
        min_gap = float(getattr(info, "trade_stops_level", 0) or 0) * point
        if min_gap > 0 and min(abs(price - stop), abs(target - price)) <= min_gap:
            chk.blocks.append(f"The broker needs the stop-loss and target at least {fmt(min_gap)} from the price.")
        risk = self.settings.risk
        sizing = size_position(state.equity, risk.risk_per_trade_pct, price, stop, spec, value, risk.max_leverage)
        volume = round_down_to_step(min(p.quantity, sizing.quantity) if sizing.ok else 0.0, spec.volume_step)
        if volume < spec.volume_min:
            chk.blocks.append(sizing.reject_reason or f"At the live price the size is below the broker minimum "
                                                      f"({spec.volume_min:g} {spec.unit}).")
        commission = 2 * spec.commission_per_unit_side * volume
        chk.volume = volume
        chk.planned_loss = volume * abs(price - stop) * value + commission
        legs = [(volume, target)]
        if p.partial_target is not None and p.partial_quantity and p.quantity:
            # Two orders with the same stop-loss: one takes profit early, the other at the target. MT5 handles
            # both take-profits by itself, even when the dashboard is closed.
            q1 = round_down_to_step(volume * p.partial_quantity / p.quantity, spec.volume_step)
            q2 = round_down_to_step(volume - q1, spec.volume_step)
            pt = on_tick(p.partial_target)
            beyond = (side == BUY and not price < pt < target) or (side == SELL and not target < pt < price)
            too_close = min_gap > 0 and abs(pt - price) <= min_gap
            if q1 >= spec.volume_min and q2 >= spec.volume_min and not beyond and not too_close:
                legs = [(q1, pt), (q2, target)]
                chk.partial_target = pt
                chk.partial_profit = q1 * abs(pt - price) * value - 2 * spec.commission_per_unit_side * q1
            else:
                chk.notes.append("Sent as one order: the size is too small to split, or the price is already "
                                 "at the first take-profit.")
        chk.legs = legs
        chk.planned_profit = sum(q * abs(tp - price) * value for q, tp in legs) - commission
        if chk.blocks:
            return chk
        request = {
            "action": ACTION_DEAL, "symbol": name, "volume": volume, "type": TYPE_BUY if side == BUY else TYPE_SELL,
            "price": price, "sl": stop, "tp": target, "deviation": int(self.t.deviation_points),
            "magic": int(self.t.magic_number), "comment": f"Signal desk {side} {tag}".strip()[:31],
            "type_time": TIME_GTC,
        }
        res = self._with_fill_type(request, info, send=False)
        if res is None:
            chk.blocks.append(f"MT5 could not check the order: {self.mt5.last_error()}")
        elif int(res.retcode) not in CHECK_OK:
            chk.blocks.append(f"MT5 would refuse this order: {retcode_text(res.retcode)}.")
        else:
            chk.request = request
        return chk

    def _send_leg(self, request: dict, info, sig, chk: OrderCheck, part: str) -> Tuple[Optional[Fill], str]:
        """Send one market order. Returns (fill, "") or (None, why). Looks for a hidden fill after a timeout."""
        name = request["symbol"]
        try:
            res = self._with_fill_type(dict(request), info, send=True)
            error = None
        except Exception as exc:  # the request may still have reached the broker
            res, error = None, exc
        if res is not None and int(res.retcode) in FILLED:
            price = float(getattr(res, "price", 0.0) or 0.0) or request["price"]
            volume = float(getattr(res, "volume", 0.0) or 0.0) or request["volume"]
            note = "" if int(res.retcode) == 10009 else f"Only {volume:g} of {request['volume']:g} lots were filled."
            return Fill(int(res.order), sig.symbol, chk.side, volume, price, chk.stop, request["tp"], note, part), ""
        # Timeouts and lost connections can hide a fill: look before saying nothing was sent.
        time.sleep(0.5)
        try:
            opened = [p for p in self._all_positions()
                      if p.symbol == name and int(getattr(p, "magic", 0)) == self.t.magic_number
                      and (not part or str(getattr(p, "comment", "")).endswith(part))]
        except OrderError:
            opened = []
        if opened:
            p = opened[-1]
            what = f"code {res.retcode}" if res is not None else "no answer"
            return Fill(self._pos_id(p), sig.symbol, chk.side, float(p.volume), float(p.price_open), float(p.sl),
                        float(p.tp), f"MT5 reported {what}, but the trade is open in MT5.", part), ""
        if res is None:
            return None, (f"MT5 did not accept the order: {error or self.mt5.last_error()}. "
                          "Check the Trade tab in MT5 before trying again.")
        return None, f"MT5 rejected the order: {retcode_text(res.retcode)}."

    @_locked
    def place(self, sig, state: RiskState, prices_at: Optional[float] = None, tag: str = "") -> Fill:
        """Re-run every check on a fresh price, then send the order: one market order, or two (same stop-loss;
        the first takes profit early, the second at the target) when the plan takes half the profit early.
        `tag` (the strategy speed) goes into the MT5 order comment, so the desk knows each trade's time limit.
        Returns the first fill; `.legs` lists every order that was filled."""
        chk = self.check(sig, state, prices_at, tag)
        if not chk.ok:
            raise OrderError(chk.blocks[0] if chk.blocks else "The order did not pass the checks.")
        name = chk.request["symbol"]
        info = self.mt5.symbol_info(name)
        legs = chk.legs or [(chk.volume, chk.target)]
        fills: List[Fill] = []
        for n, (volume, tp) in enumerate(legs, 1):
            part = f"{n}/{len(legs)}" if len(legs) > 1 else ""
            request = dict(chk.request, volume=volume, tp=tp)
            if part:
                request["comment"] = f"{chk.request['comment']} {part}"[:31]
            if n > 1:  # fresh price for the second order
                tick = self.mt5.symbol_info_tick(name)
                if tick is not None and tick.bid > 0:
                    request["price"] = float(tick.ask if chk.side == BUY else tick.bid)
            fill, why = self._send_leg(request, info, sig, chk, part)
            if fill is None:
                if not fills:
                    raise OrderError(why)
                fills[0].note = (fills[0].note + " " if fills[0].note else "") + \
                    f"Only the first half was placed ({why}) It has its stop-loss and its first take-profit."
                break
            fills.append(fill)
        first = fills[0]
        first.legs = fills
        return first

    @_locked
    def close(self, ticket: int) -> Tuple[float, float]:
        """Close one desk trade at market. Returns (price, volume)."""
        blocks = self._terminal_blocks()
        if blocks:
            raise OrderError(blocks[0])
        found = self.mt5.positions_get(ticket=int(ticket))
        if not found:
            raise OrderError("That trade is no longer open (it may have hit its stop-loss or target).")
        pos = found[0]
        if int(getattr(pos, "magic", 0)) != self.t.magic_number:
            raise OrderError("That trade was not opened from this desk. Close it in MT5.")
        tick = self.mt5.symbol_info_tick(pos.symbol)
        if tick is None or tick.bid <= 0:
            raise OrderError("MT5 has no live price for this market, so the trade cannot be closed right now.")
        is_buy = pos.type == TYPE_BUY
        request = {
            "action": ACTION_DEAL, "symbol": pos.symbol, "volume": float(pos.volume), "position": int(pos.ticket),
            "type": TYPE_SELL if is_buy else TYPE_BUY, "price": float(tick.bid if is_buy else tick.ask),
            "deviation": int(self.t.deviation_points), "magic": int(self.t.magic_number),
            "comment": "Signal desk close", "type_time": TIME_GTC,
        }
        res = self._with_fill_type(request, self.mt5.symbol_info(pos.symbol), send=True)
        left = self.mt5.positions_get(ticket=int(ticket)) or []
        if left:
            remaining = float(left[0].volume)
            if remaining < float(pos.volume):
                raise OrderError(f"Only part of the trade was closed: {remaining:g} lots are still open. "
                                 "Click Close again or close it in MT5.")
            why = retcode_text(res.retcode) if res is not None else str(self.mt5.last_error())
            raise OrderError(f"MT5 did not close the trade: {why}.")
        price = float(getattr(res, "price", 0.0) or 0.0) if res is not None else 0.0
        return price or request["price"], float(pos.volume)

    @_locked
    def modify_stop(self, ticket: int, new_stop: float) -> None:
        """Move one desk trade's stop-loss (keeps its take-profit)."""
        blocks = self._terminal_blocks()
        if blocks:
            raise OrderError(blocks[0])
        found = self.mt5.positions_get(ticket=int(ticket))
        if not found:
            raise OrderError("That trade is no longer open.")
        pos = found[0]
        if int(getattr(pos, "magic", 0)) != self.t.magic_number:
            raise OrderError("That trade was not opened from this desk.")
        request = {"action": ACTION_SLTP, "position": int(pos.ticket), "symbol": pos.symbol,
                   "sl": float(new_stop), "tp": float(pos.tp), "magic": int(self.t.magic_number)}
        res = self.mt5.order_send(request)
        if res is None:
            raise OrderError(f"MT5 did not accept the change: {self.mt5.last_error()}")
        if int(res.retcode) not in (10009, 10025):  # done, or no change needed
            raise OrderError(f"MT5 refused to move the stop-loss: {retcode_text(res.retcode)}.")

    @_locked
    def manage_partials(self, logger) -> List[dict]:
        """For split orders: once the first half has taken its profit, move the second half's stop-loss to the
        entry price (as tested). Returns what was moved: [{symbol, side, ticket, entry, first_half_pnl}]."""
        if not self.t.move_stop_after_half:
            return []
        positions = self._all_positions()
        open_ids = {self._pos_id(p) for p in positions}
        trades = logger.trades(300)
        notes = trades["notes"].fillna("") if not trades.empty else pd.Series(dtype=str)
        moved = []
        for p in positions:
            if int(getattr(p, "magic", 0)) != self.t.magic_number or not str(getattr(p, "comment", "")).endswith("2/2"):
                continue
            entry = float(p.price_open)
            is_buy = p.type == TYPE_BUY
            if p.sl and ((is_buy and p.sl >= entry) or (not is_buy and p.sl <= entry)):
                continue  # already at entry or better
            mine = notes[notes.str.contains(f"MT5 ticket {self._pos_id(p)} ", regex=False)]
            key = re.search(r"signal (\S+)", mine.iloc[0]).group(1) if len(mine) and "signal " in mine.iloc[0] else None
            if key is None:
                continue
            first = notes[notes.str.contains(f"signal {key}", regex=False) & notes.str.contains("part 1/2", regex=False)]
            m = TICKET_NOTE.search(first.iloc[0]) if len(first) else None
            if m is None or int(m.group(1)) in open_ids:
                continue  # first half still open
            deals = self.mt5.history_deals_get(position=int(m.group(1))) or []
            pnl = sum(float(d.profit) + float(getattr(d, "commission", 0.0) or 0.0) + float(getattr(d, "swap", 0.0) or 0.0)
                      for d in deals)
            if not deals or pnl <= 0:
                continue  # the first half did not take its profit (closed by hand or at a loss): leave it alone
            self.modify_stop(int(p.ticket), entry)
            moved.append({"symbol": self._our_name(p.symbol), "side": BUY if is_buy else SELL,
                          "ticket": int(p.ticket), "entry": entry, "first_half_pnl": pnl})
        return moved

    # ------------------------------------------------------------------ journal
    @_locked
    def sync_journal(self, logger) -> List[dict]:
        """
        Write results of desk trades that MT5 has closed (stop-loss, target or Close button) into the journal,
        so the daily loss limit and losing-streak rules see them. Returns the newly closed trades as dicts:
        symbol, side, ticket, pnl, exit_price, reason (plain words, e.g. "hit its target").
        """
        positions = self._all_positions()
        still_open = {self._pos_id(p) for p in positions}
        trades = logger.trades(500)
        known = set()
        for note in trades["notes"].fillna("") if not trades.empty else []:
            m = TICKET_NOTE.search(str(note))
            if m:
                known.add(int(m.group(1)))
        for p in positions:  # a desk trade MT5 has open but the journal missed (e.g. after a timeout)
            if int(getattr(p, "magic", 0)) == self.t.magic_number and self._pos_id(p) not in known:
                logger.add_trade(self._our_name(p.symbol), BUY if p.type == TYPE_BUY else SELL, float(p.volume),
                                 float(p.price_open), None, None,
                                 notes=f"MT5 ticket {self._pos_id(p)} (signal desk, found open in MT5)")
        if trades.empty:
            return []
        offset = getattr(self.src, "_offset_hours", None)
        closed = []
        for _, row in trades[trades["pnl"].isna()].iterrows():
            m = TICKET_NOTE.search(str(row.get("notes") or ""))
            if not m or int(m.group(1)) in still_open:
                continue
            deals = self.mt5.history_deals_get(position=int(m.group(1))) or []
            outs = [d for d in deals if int(getattr(d, "entry", -1)) in (DEAL_ENTRY_OUT, DEAL_ENTRY_OUT_BY)]
            if not outs:
                continue  # MT5 history not loaded yet; try again next scan
            pnl = sum(float(d.profit) + float(getattr(d, "commission", 0.0) or 0.0) + float(getattr(d, "swap", 0.0) or 0.0)
                      + float(getattr(d, "fee", 0.0) or 0.0) for d in deals)
            last = max(outs, key=lambda d: int(d.time))
            when = (datetime.fromtimestamp(int(last.time) - offset * 3600) if offset is not None else datetime.now())
            logger.close_trade(int(row["id"]), float(last.price), pnl, when)
            reason = CLOSE_REASONS.get(int(getattr(last, "reason", -1) or 0), "closed")
            part = re.search(r"part (\d/\d)", str(row.get("notes") or ""))
            closed.append({"symbol": str(row["symbol"]), "side": str(row["side"]), "ticket": int(m.group(1)),
                           "pnl": pnl, "exit_price": float(last.price), "reason": reason,
                           "part": part.group(1) if part else ""})
        return closed
