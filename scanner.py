"""
Scanner: fetch data for each enabled symbol, run the engine, log actionable
signals, and send optional alerts. Used by the dashboard and runnable on its own:

    python scanner.py            # scan every minute until Ctrl+C
    python scanner.py --once     # one scan and exit
    python scanner.py --source demo --symbols EURUSD XAUUSD
    python scanner.py --profile swing   # Gold 5-minute swing rules (see profiles.py)
"""
from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime
from typing import Iterable, List, Optional

from alerts import alert_new_signal, format_signal, send_telegram, should_popup
from config import Settings, ensure_dirs, load_settings
from engine import NO_TRADE, Signal
from market_data import MarketDataError, get_source
from profiles import MODE_KEYS, evaluate_profile, get_profile, lanes, mode_name
from risk_manager import emergency_stop_status
from timefmt import datetime_text
from trade_logger import TradeLogger


def scan_symbols(source, settings: Settings, logger: TradeLogger, symbols: Iterable[str],
                 equity_override: Optional[float] = None, send_alerts: bool = True,
                 profile: Optional[str] = None) -> List[Signal]:
    results: List[Signal] = []
    emergency = emergency_stop_status()
    prof = get_profile(profile or settings.data.strategy_profile)
    for symbol in symbols:
        try:
            snap = source.snapshot(symbol, prof.base_minutes)
        except (MarketDataError, KeyError, ValueError) as exc:
            sig = Signal(symbol=symbol, decision=NO_TRADE, considered=None, reason=f"Data error: {exc}",
                         reasons=[f"Data error: {exc}"], checks=[], plan=None, setup_type="", bid=float("nan"),
                         ask=float("nan"), time=None, bar_time=None, source=getattr(source, "name", ""),
                         profile=prof.key)
            sig.explanation = sig.reason
            results.append(sig)
            logger.log_event(f"{symbol}: {exc}", "WARNING")
            continue
        equity = snap.account_equity if snap.account_equity else (equity_override or settings.risk.account_equity)
        state = logger.risk_state(equity, emergency=emergency)
        sig, used = evaluate_profile(snap, state, settings, prof.key)
        if sig.actionable:
            if logger.recent_signal_exists(symbol, sig.decision, settings.risk.signal_cooldown_minutes, prof.key):
                sig.notes.append(f"Same {sig.decision} already signalled in the last "
                                 f"{settings.risk.signal_cooldown_minutes} min - not re-alerted")
            else:
                sig_id = logger.log_signal(sig, used.bars, snap.spec, snap.value_per_unit, state, used.settings,
                                           used.tick_time, now=used.now, profile=prof.key)
                sig.notes.append(f"Saved as signal #{sig_id}")
                if send_alerts and should_popup(sig, settings, prof.tested):
                    alert_new_signal(sig, settings, prof.base_minutes, prof.name, prof.short, prof.tested,
                                     getattr(snap.spec, "digits", None))
                if send_alerts and settings.alerts.telegram_enabled:
                    ok, msg = send_telegram(format_signal(sig), settings.alerts)
                    if not ok:
                        logger.log_event(f"Alert for {symbol} not sent: {msg}", "WARNING")
        results.append(sig)
    return results


def _print(results: List[Signal], label: str = "") -> None:
    stamp = datetime_text(datetime.now(), seconds=True)
    for s in results:
        line = f"[{stamp}] {label + ' ' if label else ''}{s.symbol:<8} {s.decision:<8}"
        if s.plan:
            p = s.plan
            line += (f" entry {p.entry:.6g} stop {p.stop:.6g} target {p.target:.6g} size {p.quantity:g} {p.unit}"
                     f" | loss {p.planned_loss:.2f} / profit {p.planned_profit:.2f} (net {p.reward_risk_net:.2f}R)")
        else:
            line += f" {s.reason}"
        if s.actionable:
            line = "\a" + line + "   <-- NEW SIGNAL"  # \a = beep in the console window
        print(line, flush=True)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Personal trading bot scanner (signals only, no orders)")
    parser.add_argument("--source", choices=["demo", "mt5", "csv"], help="override data.source")
    parser.add_argument("--symbols", nargs="*", help="symbols to scan (default: enabled in settings)")
    parser.add_argument("--once", action="store_true", help="scan once and exit")
    parser.add_argument("--no-alerts", action="store_true")
    parser.add_argument("--profile", choices=MODE_KEYS, help="override data.strategy_profile (both = swing + medium)")
    args = parser.parse_args(argv)

    ensure_dirs()
    settings = load_settings()
    if args.source:
        settings.data.source = args.source
    if args.profile:
        settings.data.strategy_profile = args.profile
    symbols = args.symbols or settings.enabled_symbols()
    logger = TradeLogger()
    try:
        source = get_source(settings)
    except MarketDataError as exc:
        print(f"Cannot start: {exc}")
        return 1
    keys = lanes(settings.data.strategy_profile)
    print(f"Scanning {', '.join(symbols)} from '{settings.data.source}' data with the "
          f"{mode_name(settings.data.strategy_profile)} rules. The scanner never places orders. Ctrl+C to stop.")
    if settings.alerts.desktop_popup:
        print(f"A new signal opens a {'full-screen ' if settings.alerts.fullscreen_popup else ''}pop-up on top of "
              "your screen. Keep this window open (it can be minimised).")
    if settings.data.source == "demo":
        print("DEMO DATA: prices are synthetic, not the real market.")
    logger.log_event(f"Scanner started ({settings.data.source}: {', '.join(symbols)})")
    due = {k: 0.0 for k in keys}  # when each strategy scans next: a few seconds after its next candle closes
    try:
        while True:
            stopped, why = emergency_stop_status()
            if stopped:
                print(f"EMERGENCY STOP active ({why}). No signals will be issued.", flush=True)
            now = time.time()
            for k in keys:
                if now >= due[k]:
                    prof = get_profile(k)
                    _print(scan_symbols(source, settings, logger, symbols, send_alerts=not args.no_alerts, profile=k),
                           prof.short if len(keys) > 1 else "")
                    period = 60.0 * prof.base_minutes
                    due[k] = (time.time() // period + 1) * period + 3.0
            if args.once:
                break
            time.sleep(max(1.0, min(due.values()) - time.time()))
    except KeyboardInterrupt:
        print("Stopped.")
    finally:
        logger.log_event("Scanner stopped")
        source.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
