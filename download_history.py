"""
Download as much history as MetaTrader 5 allows for every enabled symbol, for backtesting
and research. Read-only.

    python download_history.py      (or double-click download_history.bat)

Saves:
    data/csv/<SYMBOL>.csv      1-minute candles (~3 months by default; up to ~1.5 years, see below)
    data/csv/<SYMBOL>_M5.csv   5-minute candles (~1.4 years by default; more, see below)

MT5 only hands out as many candles as its "Max bars in chart" setting allows (100,000 by default).
For long 1-minute history: in MT5 open Tools > Options > Charts, set "Max bars in chart" to Unlimited,
press OK, restart MT5, then run this again.
"""
import sys
import time
import traceback
from datetime import datetime

from config import DATA_DIR, ensure_dirs, load_settings
from market_data import MarketDataError, MT5Source

LOG = DATA_DIR / "download_log.txt"


def say(text: str) -> None:
    """Print and also append to data/download_log.txt so problems can be checked later."""
    print(text, flush=True)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(f"{datetime.now():%Y-%m-%d %H:%M:%S}  {text}\n")

# (candle minutes, days to try in order). The long requests only work after "Max bars in chart" is raised.
PLAN = [(1, (540, 365, 180, 69, 45, 30, 14)), (5, (730, 540, 343, 250, 180, 90))]
LIMIT_HINT = ('To get more: in MT5 open Tools > Options > Charts, set "Max bars in chart" to Unlimited, '
              "press OK, restart MT5, and run the download again.")


def download_all(src, symbols, say=say) -> int:
    """Download every PLAN entry for each symbol. Returns the number of files saved."""
    ok = 0
    for symbol in symbols:
        for minutes, tries in PLAN:
            last_err = None
            for days in tries:
                try:
                    bars = src.download_history(symbol, days=days, minutes=minutes)
                    span = (bars["time"].iloc[-1] - bars["time"].iloc[0]).days if len(bars) else 0
                    if 0 < span < 0.8 * days and days > 100:
                        time.sleep(3)  # MT5 may still be fetching older history from the server: ask once more
                        again = src.download_history(symbol, days=days, minutes=minutes)
                        bars = again if len(again) > len(bars) else bars
                except MarketDataError as exc:
                    last_err = exc
                    continue  # longer requests fail while "Max bars in chart" is limited; try a shorter one
                except Exception as exc:  # e.g. a market this broker does not offer: skip it, keep going
                    last_err = exc
                    break
                if len(bars) > 1000:
                    span = (bars["time"].iloc[-1] - bars["time"].iloc[0]).days
                    say(f"{symbol:<8} {minutes}-minute: {len(bars):>7} candles  "
                        f"{bars['time'].iloc[0]:%Y-%m-%d} to {bars['time'].iloc[-1]:%Y-%m-%d} ({span} days)")
                    if minutes == 1 and span < 120:
                        say(f"{'':<8} Only {span} days of 1-minute candles. {LIMIT_HINT}")
                    ok += 1
                    break
            else:
                say(f"{symbol:<8} {minutes}-minute: FAILED: {last_err}")
    say(f"Done: {ok} files saved in data\\csv")
    return ok


def main() -> int:
    ensure_dirs()
    settings = load_settings()
    say("Download started")
    try:
        src = MT5Source(settings)
        acct = src.account()
        say(f"Connected to MT5 account {acct['login']} on {acct['server']}")
    except MarketDataError as exc:
        say(f"Cannot connect to MT5: {exc}")
        return 1
    # every market the bot knows (so each can be tested), or only the ones named: download_history.py XAUUSD
    symbols = [a.upper() for a in sys.argv[1:]] or list(settings.instruments)
    download_all(src, symbols)
    src.close()
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        say("Unexpected error:\n" + traceback.format_exc())
        sys.exit(2)
