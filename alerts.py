"""
Alerts for a new signal:
- a pop-up on top of every window (on by default, alerts.desktop_popup): full screen until you close it
  (alerts.fullscreen_popup, fullscreen_alert.py), or a small Windows message box when that is off or cannot
  open, and
- optional Telegram messages (free). Create a bot with @BotFather, then put the token and your chat id in
  settings.json ("alerts") or the TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID environment variables, and set
  telegram_enabled to true.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from typing import Tuple

from pathlib import Path

from config import DATA_DIR, AlertSettings, Settings
from strategy import fmt
from timefmt import datetime_text, time_text

# Windows message box style: OK button, information icon and sound, on top of every window
MB_OK, MB_ICONINFORMATION, MB_SYSTEMMODAL, MB_SETFOREGROUND, MB_TOPMOST = 0x0, 0x40, 0x1000, 0x10000, 0x40000


def format_signal(sig) -> str:
    p = sig.plan
    if p is None:
        return f"{sig.symbol}: {sig.decision} - {sig.reason}"
    lines = [
        f"{sig.decision} {sig.symbol} ({sig.setup_type})",
        f"Entry {fmt(p.entry)} | Stop {fmt(p.stop)} | Target {fmt(p.target)}",
        f"Size {p.quantity:g} {p.unit}",
        f"Planned loss {p.planned_loss:.2f} | Planned profit {p.planned_profit:.2f} | Net R:R {p.reward_risk_net:.2f}",
        f"Costs ~{p.total_costs:.2f} | Risk {p.risk_pct_of_equity:.2f}% of equity",
        *([f"Move stop to entry ({fmt(p.entry)}) once price reaches {fmt(p.breakeven_trigger)}"]
          if p.breakeven_trigger is not None else []),
        f"Data: {sig.source}, sent {datetime_text(datetime.now())} (your PC's clock)",
        "Signal only - you decide whether to trade.",
    ]
    return "\n".join(lines)


def should_popup(sig, settings: Settings, strategy_tested: bool = True) -> bool:
    """Pop up for signals you can trade from the desk (a tested market and strategy). Watch-only signals pop
    up too only if alerts.popup_watch_only is true."""
    if not settings.alerts.desktop_popup or not sig.actionable:
        return False
    tradeable = (sig.tested_market and strategy_tested) or settings.trading.allow_untested_markets
    return tradeable or settings.alerts.popup_watch_only


def popup_text(sig, base_minutes: int = 1, ccy: str = "", strategy: str = "") -> str:
    p = sig.plan
    lines = [f"{sig.decision} {sig.symbol}" + (f"  ({sig.setup_type} setup)" if sig.setup_type else "")]
    lines += [f"{strategy} rules", ""] if strategy else [""]
    if p is not None:
        lines += [f"Entry about:  {fmt(p.entry)}", f"Stop-loss:    {fmt(p.stop)}", f"Target:       {fmt(p.target)}"]
        if p.partial_target is not None:
            lines.append(f"Half takes profit at {fmt(p.partial_target)}")
        lines += ["", f"Size {p.quantity:g} {p.unit}, risk {p.planned_loss:,.2f} {ccy}".rstrip()]
    if not sig.tested_market:
        lines += ["", "Watch only: this market lost money in testing."]
    lines += ["", f"Signalled {time_text(datetime.now())} on your PC. It is valid until the next "
                  f"{base_minutes}-minute candle closes.",
              "Open the Signal desk to check it and place the order.",
              "Signal only: you decide whether to trade."]
    return "\n".join(lines)


def show_desktop_popup(title: str, text: str) -> bool:
    """A Windows message box on top of every window, with the system sound. It runs in its own thread, so
    scanning carries on while it is open. Does nothing on other systems (returns False)."""
    if sys.platform != "win32":
        return False
    try:
        import ctypes
        box = ctypes.windll.user32.MessageBoxW
    except (ImportError, AttributeError, OSError):
        return False
    flags = MB_OK | MB_ICONINFORMATION | MB_SYSTEMMODAL | MB_SETFOREGROUND | MB_TOPMOST
    threading.Thread(target=box, args=(None, text, title, flags), daemon=True).start()
    return True


ALERT_SCRIPT = Path(__file__).with_name("fullscreen_alert.py")
ALERT_LOG = DATA_DIR / "alert_errors.log"
DESK_URL = "http://localhost:8501"  # the dashboard sets its real address
_TK = None


def tk_available() -> bool:
    """Can this Python draw windows (tkinter, part of the normal Windows Python install)?"""
    global _TK
    if _TK is None:
        try:
            import tkinter  # noqa: F401
            _TK = True
        except Exception:
            _TK = False
    return _TK


def signal_info(sig, base_minutes: int = 1, ccy: str = "", strategy: str = "", watch: bool = False,
                test: bool = False, digits=None) -> dict:
    """What the full-screen alert shows. A signal is valid until its strategy's next candle closes.
    digits: decimals of the market's prices (4049.80 rather than 4049.8)."""
    def px(x):
        return f"{x:.{digits}f}" if digits is not None else fmt(x)

    period = 60 * max(1, int(base_minutes))
    until = (time.time() // period + 1) * period
    info = {"side": sig.decision, "symbol": sig.symbol, "strategy": strategy, "setup": sig.setup_type or "",
            "signalled": time_text(datetime.now()), "valid_until": until,
            "valid_text": time_text(datetime.fromtimestamp(until)), "watch": bool(watch), "desk_url": DESK_URL,
            "test": bool(test)}
    p = sig.plan
    if p is not None:
        info.update(entry=px(p.entry), stop=px(p.stop), target=px(p.target),
                    half=px(p.partial_target) if p.partial_target is not None else "",
                    size=f"{p.quantity:g} {p.unit}", risk=f"{p.planned_loss:,.2f} {ccy}".strip())
    return info


def show_fullscreen_alert(info: dict, fallback_title: str = "", fallback_text: str = "") -> bool:
    """Open the full-screen alert in its own window process, so the scan carries on while it is open. If the
    window cannot open (it closes with an error within a few seconds), the small message box is shown instead.
    Returns False when no window can be drawn here (then the caller shows the message box)."""
    if sys.platform != "win32" and not os.environ.get("DISPLAY"):
        return False
    if not ALERT_SCRIPT.exists() or not tk_available():
        return False
    exe = Path(sys.executable)
    windowed = exe.with_name("pythonw.exe")  # no black console window
    cmd = [str(windowed if windowed.exists() else exe), str(ALERT_SCRIPT), json.dumps(info)]
    try:
        ALERT_LOG.parent.mkdir(parents=True, exist_ok=True)
        log = open(ALERT_LOG, "ab")
        proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=log,
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), close_fds=True)
    except OSError:
        return False

    def watch():
        try:
            code = proc.wait(timeout=6)
            if code != 0 and fallback_text:
                show_desktop_popup(fallback_title, fallback_text)
        except subprocess.TimeoutExpired:
            pass  # the window is open
        finally:
            log.close()

    threading.Thread(target=watch, daemon=True).start()
    return True


def alert_new_signal(sig, settings: Settings, base_minutes: int = 1, strategy: str = "", short: str = "",
                     strategy_tested: bool = True, digits=None) -> bool:
    """The pop-up for a new signal: full screen, or the small message box. Returns True if one was shown."""
    title = f"Signal desk: {sig.decision} {sig.symbol} ({short or strategy})"
    text = popup_text(sig, base_minutes, settings.risk.account_currency, strategy)
    if settings.alerts.fullscreen_popup:
        watch = not (sig.tested_market and strategy_tested)
        info = signal_info(sig, base_minutes, settings.risk.account_currency, strategy, watch, digits=digits)
        if show_fullscreen_alert(info, title, text):
            return True
    return show_desktop_popup(title, text)


def send_telegram(text: str, cfg: AlertSettings, timeout: float = 10.0) -> Tuple[bool, str]:
    if not cfg.telegram_enabled:
        return False, "Telegram alerts disabled"
    if not cfg.telegram_bot_token or not cfg.telegram_chat_id:
        return False, "Telegram token or chat id missing"
    url = f"https://api.telegram.org/bot{cfg.telegram_bot_token}/sendMessage"
    data = urllib.parse.urlencode({"chat_id": cfg.telegram_chat_id, "text": text}).encode()
    try:
        with urllib.request.urlopen(urllib.request.Request(url, data=data), timeout=timeout) as resp:
            body = json.loads(resp.read().decode())
            return bool(body.get("ok")), "sent" if body.get("ok") else str(body)
    except (urllib.error.URLError, TimeoutError, ValueError) as exc:
        return False, f"Telegram error: {exc}"
