"""
Health check against your live MetaTrader 5 connection.

    python diagnose.py            (or double-click check_mt5.bat)

Checks the connection, prices, candles, contract specs, position sizing and the
signal engine, then replays the last few days of real MT5 prices through the bot
to show how many BUY/SELL signals it would have produced and how they ended.
Writes everything to data/diagnostic_report.txt. Read-only: it never places orders.
"""
from __future__ import annotations

import platform
import sys
import time
import traceback
from datetime import datetime

import pandas as pd

from backtest import run_backtest
from config import DATA_DIR, ensure_dirs, load_settings
from engine import evaluate_snapshot
from market_data import MarketDataError, MT5Source
from position_sizing import size_position
from risk_manager import RiskManager
from trade_logger import TradeLogger

REPLAY_DAYS = 7
lines = []
results = {}  # check name -> (ok, note)


def out(text: str = "") -> None:
    lines.append(text)
    try:
        print(text, flush=True)
    except UnicodeEncodeError:
        print(text.encode("ascii", "replace").decode(), flush=True)


def check(name: str, ok: bool, note: str = "") -> None:
    results[name] = (ok, note)
    out(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" - {note}" if note else ""))


def section(title: str) -> None:
    out("")
    out("=" * 78)
    out(title)
    out("=" * 78)


def main() -> int:
    ensure_dirs()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")
    settings = load_settings()
    symbols = settings.enabled_symbols()
    out(f"Trading bot diagnostic - {datetime.now():%Y-%m-%d %H:%M:%S}")
    out(f"Python {platform.python_version()} | pandas {pd.__version__} | symbols: {', '.join(symbols)}")

    # ------------------------------------------------------------------ connection
    section("1. MetaTrader 5 connection")
    try:
        src = MT5Source(settings)
    except MarketDataError as exc:
        check("Connect to MT5", False, str(exc))
        return finish()
    import MetaTrader5 as mt5  # available because MT5Source imported it
    term = mt5.terminal_info()
    check("Connect to MT5", True, f"terminal build {getattr(term, 'build', '?')}, path {getattr(term, 'path', '?')}")
    connected = bool(getattr(term, "connected", False))
    ping = getattr(term, "ping_last", None)
    check("Terminal connected to broker server", connected,
          (f"ping {ping / 1000:.0f} ms" if ping else "") if connected else
          "MT5 is not connected to its server - see the bottom-right corner of MT5")
    if not connected:
        out("  -> In MT5: File > Login to Trade Account (tick Save password), or open a new demo account.")
    try:
        acct = src.account()
    except MarketDataError as exc:
        check("Logged in to a trading account", False, str(exc))
        return finish()
    connection_test(mt5, src, symbols)
    out(f"  Account {acct['login']} on {acct['server']} ({acct['mode']}), balance {acct['balance']:.2f} "
        f"{acct['currency']}, equity {acct['equity']:.2f}")
    check("Account is a demo account", acct["mode"] == "demo",
          "" if acct["mode"] == "demo" else "Use a DEMO account while testing")

    logger = TradeLogger()
    for symbol in symbols:
        section(f"2. {symbol}")
        try:
            run_symbol(src, settings, logger, symbol, acct)
        except Exception as exc:  # keep going so one bad symbol doesn't hide the rest
            check(f"{symbol}: unexpected error", False, f"{type(exc).__name__}: {exc}")
            out(traceback.format_exc())
    src.close()
    return finish()


def connection_test(mt5, src: MT5Source, symbols, seconds: int = 60, every: float = 2.0) -> None:
    """Watch the MT5 link for a minute: server connection, login, ping and how long prices go without updating."""
    section(f"1b. Connection stability test ({seconds} seconds)")
    names = []
    for sym in symbols:
        try:
            names.append(src.resolve_symbol(sym))
        except MarketDataError:
            pass
    last_change = {n: None for n in names}
    last_msc = {n: None for n in names}
    longest = {n: 0.0 for n in names}
    disconnected = logged_out = samples = 0
    pings = []
    start = time.time()
    while time.time() - start < seconds:
        now = time.time()
        samples += 1
        term = mt5.terminal_info()
        if term is None or not getattr(term, "connected", False):
            disconnected += 1
        elif getattr(term, "ping_last", None):
            pings.append(term.ping_last / 1000.0)
        if mt5.account_info() is None:
            logged_out += 1
        for n in names:
            tick = mt5.symbol_info_tick(n)
            msc = getattr(tick, "time_msc", None) if tick is not None else None
            if msc is not None and msc != last_msc[n]:
                last_msc[n], last_change[n] = msc, now
            if last_change[n] is not None:
                longest[n] = max(longest[n], now - last_change[n])
            else:
                longest[n] = now - start
        time.sleep(every)
    check("Stayed connected to the broker server", disconnected == 0,
          f"{disconnected} of {samples} checks disconnected" if disconnected else
          (f"average ping {sum(pings) / len(pings):.0f} ms" if pings else ""))
    check("Stayed logged in to the account", logged_out == 0,
          f"{logged_out} of {samples} checks logged out" if logged_out else "")
    for n in names:
        out(f"  {n}: longest time without a new price {longest[n]:.0f}s")
    stuck = [n for n in names if longest[n] > 45]
    check("Prices kept updating", not stuck,
          ("no new prices for over 45s: " + ", ".join(stuck) + " (market closed, or MT5 not receiving data)")
          if stuck else "")


def run_symbol(src: MT5Source, settings, logger, symbol: str, acct: dict) -> None:
    # --- live snapshot
    try:
        snap = src.snapshot(symbol)
    except MarketDataError as exc:
        check(f"{symbol}: price data", False, str(exc))
        return
    spec = snap.spec
    bars = snap.bars_1m
    check(f"{symbol}: price data", len(bars) > 1000,
          f"broker symbol '{spec.broker_symbol}', {len(bars)} one-minute candles "
          f"from {bars['time'].iloc[0]:%Y-%m-%d %H:%M} to {bars['time'].iloc[-1]:%Y-%m-%d %H:%M}")
    out(f"  Bid {snap.bid} / Ask {snap.ask} / spread {snap.spread:.6g} | tick {snap.tick_time} | "
        f"broker clock {snap.now} (UTC{src._offset_hours:+g}h)" if snap.now is not None else
        f"  Bid {snap.bid} / Ask {snap.ask} | broker clock UNKNOWN")
    tick_age = (snap.now - snap.tick_time).total_seconds() if snap.now is not None else None
    if tick_age is not None and tick_age < -5:
        out(f"  Note: this computer's clock is about {-tick_age:.0f}s behind the broker's. Harmless (the bot waits "
            f"for the broker's clock), but you can fix it in Windows Settings > Time > Sync now.")
    bad = RiskManager.price_blocks(snap.bid, snap.ask, float(bars["close"].iloc[-1]))
    check(f"{symbol}: live price is valid", not bad, bad[0] if bad else "")
    check(f"{symbol}: live price is fresh", tick_age is not None and tick_age <= settings.risk.stale_tick_seconds,
          (f"last tick {max(tick_age, 0):.0f}s old" if tick_age is not None else "clock unknown"))
    out(f"  Contract: size {spec.contract_size:g}, tick {spec.tick_size:g}, volume {spec.volume_min:g}-"
        f"{spec.volume_max:g} step {spec.volume_step:g}, profit currency {spec.quote_currency}")
    vinfo = src.value_info.get(symbol, {})
    for name, val in vinfo.get("checks", {}).items():
        out(f"    value of a 1.0 price move per lot by {name}: {val:,.2f} {acct['currency']}")
    out(f"    -> bot uses {snap.value_per_unit:,.2f} {acct['currency']} (the most cautious)")
    if vinfo.get("note"):
        out(f"  Note: {vinfo['note']}")
    check(f"{symbol}: contract specs from broker", snap.value_per_unit > 0 and spec.volume_min > 0)

    # --- live decision
    state = logger.risk_state(acct["equity"])
    sig = evaluate_snapshot(snap, state, settings)
    rep = sig.data_report
    check(f"{symbol}: candles valid", rep is not None and rep.ok, rep.summary() if rep else "no report")
    check(f"{symbol}: signal engine ran", sig.decision in ("BUY", "SELL", "NO TRADE"), f"decision = {sig.decision}")
    out(f"  Current decision: {sig.decision} - {sig.reason}")
    for c in sig.checks:
        out(f"    {c.timeframe:>3} {c.purpose:<18} {'PASS' if c.passed else 'fail'}  {c.detail}")
    if sig.plan:
        p = sig.plan
        out(f"  Plan: entry {p.entry} stop {p.stop} target {p.target} size {p.quantity:g} {p.unit} "
            f"loss {p.planned_loss:.2f} profit {p.planned_profit:.2f} net R:R {p.reward_risk_net:.2f}")

    # --- sizing sanity on real specs: a 1 x ATR(5m) stop
    atr = next((c.info.get("atr") for c in sig.checks if c.timeframe == "5m" and "atr" in c.info), None)
    if atr and atr == atr:
        stop = snap.ask - atr
        sz = size_position(acct["equity"], settings.risk.risk_per_trade_pct, snap.ask, stop, spec,
                           snap.value_per_unit, settings.risk.max_leverage)
        if sz.ok:
            loss = sz.quantity * sz.loss_per_unit
            check(f"{symbol}: position sizing", loss <= sz.risk_budget + 1e-9,
                  f"stop {atr:.6g} away -> {sz.quantity:g} {spec.unit}, loss {loss:.2f} <= budget {sz.risk_budget:.2f}")
        else:
            check(f"{symbol}: position sizing", True, f"correctly rejected: {sz.reject_reason}")

    # --- replay recent real history through the same engine
    hist = src.download_history(symbol, days=REPLAY_DAYS, save=False)  # never overwrite saved history
    res = run_backtest(hist, spec, settings, start_equity=acct["equity"])
    m = res.metrics
    t = res.trades
    out(f"  Replay of last {REPLAY_DAYS} days ({len(hist)} candles, not saved):")
    if m["trades"]:
        out(f"    {m['trades']} signals -> {m['wins']} hit target/profit, {m['losses']} lost | win rate "
            f"{m['win_rate_pct']:.0f}% | net {m['net_pnl']:.2f} {acct['currency']} | profit factor "
            f"{m['profit_factor'] if m['profit_factor'] is None else round(m['profit_factor'], 2)} | "
            f"max drawdown {m['max_drawdown_pct']:.2f}%")
        for _, r in t.tail(5).iterrows():
            out(f"    {r['signal_time']:%a %d %b %H:%M} {r['side']:<4} {r['setup']:<8} entry {r['entry']:.6g} "
                f"stop {r['stop']:.6g} target {r['target']:.6g} -> {r['exit_reason']:<10} {r['pnl']:+.2f}")
    else:
        out("    No signals in this period (all candles failed at least one rule).")
    check(f"{symbol}: engine produces signals on real data", m["trades"] > 0,
          f"{m['trades']} in {REPLAY_DAYS} days" if m["trades"] else "none - rules may be too strict for this market")


def finish() -> int:
    section("SUMMARY")
    passed = sum(1 for ok, _ in results.values() if ok)
    failed = [(k, n) for k, (ok, n) in results.items() if not ok]
    out(f"  {passed} passed, {len(failed)} failed")
    for k, n in failed:
        out(f"  FAILED: {k}" + (f" - {n}" if n else ""))
    out("")
    out("  Note: 'working' means the software is connected and every rule is evaluated correctly.")
    out("  NO TRADE most of the time is normal - all five timeframes and every risk rule must agree.")
    path = DATA_DIR / "diagnostic_report.txt"
    path.write_text("\n".join(lines), encoding="utf-8")
    out(f"  Report saved to {path}")
    return 0 if not failed else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        out(traceback.format_exc())
        finish()
        sys.exit(2)
