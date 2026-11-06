"""
Market data connectors.

- MT5Source:  live bid/ask and 1-minute (or 5-minute) candles from a MetaTrader 5 terminal
               (Windows, free with a broker demo or live account). Read-only.
- CsvSource:   candles from data/csv/<SYMBOL>.csv (1-minute) or <SYMBOL>_M5.csv (5-minute).
- DemoSource:  SYNTHETIC prices for trying the software without a broker.
               Demo prices are random and are NOT real market data.

Every source returns a MarketSnapshot whose times all use one clock
(broker server time for MT5, UTC for demo/CSV).
"""
from __future__ import annotations

import functools
import threading
import time
import zlib
from dataclasses import dataclass, field, replace
from typing import List, Optional

import numpy as np
import pandas as pd

from candles import normalize_bars, resample_bars
from config import CSV_DIR, InstrumentSpec, Settings
from position_sizing import value_per_price_unit


class MarketDataError(RuntimeError):
    pass


def _locked(method):
    """One MT5 call at a time: the dashboard's live price box and its scans run on different threads."""
    @functools.wraps(method)
    def wrapper(self, *args, **kwargs):
        with self.lock:
            return method(self, *args, **kwargs)
    return wrapper


@dataclass
class Quote:
    bid: float
    ask: float
    time: Optional[pd.Timestamp]  # on the data clock (broker server time for MT5)

    @property
    def spread(self) -> float:
        return self.ask - self.bid


@dataclass
class MarketSnapshot:
    symbol: str
    bars_1m: pd.DataFrame  # base candles (1-minute, or 5-minute when base_minutes=5), bid prices, may include the forming bar
    bid: float
    ask: float
    tick_time: Optional[pd.Timestamp]
    now: Optional[pd.Timestamp]  # current time on the same clock as bar times
    spec: InstrumentSpec
    value_per_unit: float  # account currency per 1.0 price move per lot/share
    source: str
    account_equity: Optional[float] = None
    account_currency: Optional[str] = None
    notes: List[str] = field(default_factory=list)
    base_minutes: int = 1

    @property
    def spread(self) -> float:
        return self.ask - self.bid


# ----------------------------------------------------------------------------- demo data

DEMO_EPOCH = pd.Timestamp("2026-01-01 00:00:00")


def generate_demo_bars(spec: InstrumentSpec, start: pd.Timestamp, end: pd.Timestamp,
                       skip_weekends: bool = False, seed_salt: int = 0) -> pd.DataFrame:
    """
    Deterministic synthetic 1-minute candles with trending and ranging regimes.
    The same symbol always produces the same path from DEMO_EPOCH, so history
    is stable between refreshes and new candles are appended as time passes.
    """
    end = pd.Timestamp(end).floor("min")
    n_total = int((end - DEMO_EPOCH) / pd.Timedelta(minutes=1))
    if n_total <= 0:
        raise ValueError("Demo end time must be after 2026-01-01")
    seed = zlib.crc32(spec.symbol.encode()) + seed_salt
    vol = spec.demo_volatility
    noise = np.random.default_rng([seed, 1]).standard_normal(n_total)
    wick = np.abs(np.random.default_rng([seed, 2]).standard_normal((2, n_total)))
    spread_noise = np.abs(np.random.default_rng([seed, 3]).standard_normal(n_total))
    reg_rng = np.random.default_rng([seed, 4])
    lengths = reg_rng.integers(90, 480, size=6000)
    directions = reg_rng.choice([-1.0, 0.0, 1.0], size=6000, p=[0.35, 0.3, 0.35])
    drift = np.repeat(directions * 0.12 * vol, lengths)
    if len(drift) < n_total:
        drift = np.concatenate([drift, np.zeros(n_total - len(drift))])
    drift = drift[:n_total]
    log_ret = drift + vol * noise
    close = spec.demo_start_price * np.exp(np.cumsum(log_ret))
    open_ = np.concatenate([[spec.demo_start_price], close[:-1]])
    high = np.maximum(open_, close) * (1 + 0.5 * vol * wick[0])
    low = np.minimum(open_, close) * (1 - 0.5 * vol * wick[1])
    times = DEMO_EPOCH + pd.to_timedelta(np.arange(n_total), unit="min")
    df = pd.DataFrame({
        "time": times,
        "open": np.round(open_, spec.digits),
        "high": np.round(high, spec.digits),
        "low": np.round(low, spec.digits),
        "close": np.round(close, spec.digits),
        "spread": np.round(spec.typical_spread * (0.8 + 0.3 * spread_noise), spec.digits + 1),
        "volume": 100 + (50 * spread_noise).round(),
    })
    df["high"] = df[["open", "high", "close"]].max(axis=1)
    df["low"] = df[["open", "low", "close"]].min(axis=1)
    df = df[df["time"] >= pd.Timestamp(start)]
    if skip_weekends:
        df = df[df["time"].dt.dayofweek < 5]
    return df.reset_index(drop=True)


class DemoSource:
    name = "demo"

    def __init__(self, settings: Settings):
        self.settings = settings

    def snapshot(self, symbol: str, base_minutes: int = 1) -> MarketSnapshot:
        spec = self.settings.instruments[symbol]
        now = pd.Timestamp.now(tz="UTC").tz_localize(None)
        end = now.floor("min")  # bars up to the last fully closed minute
        start = end - pd.Timedelta(minutes=self.settings.data.history_minutes * base_minutes)
        bars = normalize_bars(generate_demo_bars(spec, start, end), spec.typical_spread)
        if base_minutes > 1:
            bars = resample_bars(bars, base_minutes)
        bid = float(bars["close"].iloc[-1])
        ask = bid + float(bars["spread"].iloc[-1])
        r = self.settings.risk
        return MarketSnapshot(
            symbol=symbol, bars_1m=bars, bid=bid, ask=ask, tick_time=now, now=now, spec=spec,
            value_per_unit=value_per_price_unit(spec, bid, r.account_currency, r.fx_rates_to_account),
            source=self.name, notes=["DEMO DATA: synthetic prices, not the real market"], base_minutes=base_minutes,
        )

    def quote(self, symbol: str) -> Quote:
        """Latest synthetic price (changes once a minute, so it is worked out once a minute)."""
        spec = self.settings.instruments[symbol]
        now = pd.Timestamp.now(tz="UTC").tz_localize(None)
        cache = self.__dict__.setdefault("_quotes", {})
        if symbol not in cache or cache[symbol][0] != now.floor("min"):
            bars = generate_demo_bars(spec, now - pd.Timedelta(minutes=3), now)
            bid = float(bars["close"].iloc[-1])
            cache[symbol] = (now.floor("min"), bid, bid + float(bars["spread"].iloc[-1]))
        _, bid, ask = cache[symbol]
        return Quote(bid, ask, now)

    def close(self):
        pass


# ----------------------------------------------------------------------------- CSV data

def load_csv_bars(symbol: str, spec: InstrumentSpec, folder=None, minutes: int = 1) -> pd.DataFrame:
    """1-minute candles from <SYMBOL>.csv, or 5-minute candles from <SYMBOL>_M5.csv (built from the
    1-minute file if there is no _M5 file)."""
    folder = folder or CSV_DIR
    if minutes > 1:
        for name in (f"{symbol}_M{minutes}.csv", f"{symbol}_M{minutes}.csv.gz"):
            if (folder / name).exists():
                return normalize_bars(pd.read_csv(folder / name), spec.typical_spread)
        return resample_bars(load_csv_bars(symbol, spec, folder, 1), minutes)
    for name in (f"{symbol}.csv", f"{symbol}.csv.gz", f"{spec.broker_symbol}.csv" if spec.broker_symbol else None):
        if name and (folder / name).exists():
            return normalize_bars(pd.read_csv(folder / name), spec.typical_spread)
    raise MarketDataError(f"No CSV file for {symbol} in {folder} (expected {symbol}.csv)")


class CsvSource:
    """Replays the end of a CSV file as if it were 'now'. Useful for checking decisions on saved data."""

    name = "csv"

    def __init__(self, settings: Settings):
        self.settings = settings

    def snapshot(self, symbol: str, base_minutes: int = 1) -> MarketSnapshot:
        spec = self.settings.instruments[symbol]
        bars = load_csv_bars(symbol, spec, minutes=base_minutes).tail(self.settings.data.history_minutes)
        bars = bars.reset_index(drop=True)
        if len(bars) == 0:
            raise MarketDataError(f"CSV file for {symbol} is empty")
        now = bars["time"].iloc[-1] + pd.Timedelta(minutes=base_minutes)
        bid = float(bars["close"].iloc[-1])
        ask = bid + float(bars["spread"].iloc[-1] or spec.typical_spread)
        r = self.settings.risk
        return MarketSnapshot(
            symbol=symbol, bars_1m=bars, bid=bid, ask=ask, tick_time=now, now=now, spec=spec,
            value_per_unit=value_per_price_unit(spec, bid, r.account_currency, r.fx_rates_to_account),
            source=self.name, notes=[f"CSV replay: decision as of {now:%Y-%m-%d %H:%M}"], base_minutes=base_minutes,
        )

    def quote(self, symbol: str) -> Quote:
        """Last price in the CSV file (a replay does not move)."""
        spec = self.settings.instruments[symbol]
        bars = load_csv_bars(symbol, spec).tail(1)
        if len(bars) == 0:
            raise MarketDataError(f"CSV file for {symbol} is empty")
        bid = float(bars["close"].iloc[-1])
        return Quote(bid, bid + float(bars["spread"].iloc[-1] or spec.typical_spread),
                     bars["time"].iloc[-1] + pd.Timedelta(minutes=1))

    def close(self):
        pass


# ----------------------------------------------------------------------------- MetaTrader 5

# Other names brokers commonly use for the same market.
SYMBOL_ALIASES = {
    "XAUUSD": ["GOLD"],
    "XAGUSD": ["SILVER"],
    "USOIL": ["XTIUSD", "WTI", "USOUSD", "WTICOUSD", "CL-OIL", "OIL"],
    "US500": ["SPX500", "SP500", "USA500", "SPX"],
    "NAS100": ["USTEC", "US100", "NDX100", "NQ100", "USTECH"],
}


class MT5Source:
    """Read-only MT5 connection. This class never sends orders."""

    name = "mt5"

    def __init__(self, settings: Settings):
        try:
            import MetaTrader5 as mt5  # type: ignore
        except ImportError as exc:
            raise MarketDataError(
                "The MetaTrader5 Python package is not installed. It only works on Windows; "
                "run setup.bat, and make sure the MT5 terminal is installed and logged in."
            ) from exc
        self.mt5 = mt5
        self.settings = settings
        self._offset_hours: Optional[float] = settings.data.mt5_server_utc_offset_hours
        self._offset_candidate: Optional[int] = None
        self._names = {}
        self.lock = threading.RLock()
        self.value_info = {}  # symbol -> how the value per price unit was determined
        d = settings.data
        self._kwargs = {}
        if d.mt5_terminal_path:
            self._kwargs["path"] = d.mt5_terminal_path
        if d.mt5_login:
            self._kwargs.update(login=int(d.mt5_login), password=d.mt5_password, server=d.mt5_server)
        self._connect()

    def _connect(self) -> None:
        mt5 = self.mt5
        if not mt5.initialize(**self._kwargs):
            err = mt5.last_error()
            code = err[0] if isinstance(err, tuple) and err else None
            if code == -10003 or "not found" in str(err).lower():
                raise MarketDataError(
                    "MetaTrader 5 was not found on this computer. Install the MT5 program (from your broker or "
                    "metatrader5.com), open it, log in to a demo account, then click Scan now. If MT5 is installed "
                    "in an unusual folder, put the full path to terminal64.exe in data.mt5_terminal_path in "
                    f"settings.json. (Details: {err})")
            if code == -6 or "authoriz" in str(err).lower():
                raise MarketDataError("MetaTrader 5 is open but not logged in to your broker, so no prices are arriving. In MT5 use "
                    "File > Login to Trade Account (tick Save password), or File > Open an Account for a new "
                    "demo account. The bot reconnects by itself once MT5 shows live prices again." + f" (Details: {err})")
            raise MarketDataError(
                f"Could not connect to MetaTrader 5: {err}. Make sure MT5 is open and logged in, then try again.")

    def _ensure_connected(self) -> None:
        """Re-open the link to the MT5 terminal if it dropped (for example after MT5 was restarted)."""
        try:
            alive = self.mt5.terminal_info() is not None
        except Exception:
            alive = False
        if not alive:
            try:
                self.mt5.shutdown()
            except Exception:
                pass
            self._connect()

    def close(self):
        try:
            self.mt5.shutdown()
        except Exception:
            pass

    @_locked
    def account(self) -> dict:
        info = self.mt5.account_info()
        if info is None:  # the link may have dropped: reconnect once and retry
            self._ensure_connected()
            info = self.mt5.account_info()
        if info is None:
            raise MarketDataError(f"MT5 account info unavailable: {self.mt5.last_error()}. Is MT5 open and logged in?")
        mode = {0: "demo", 1: "contest", 2: "real"}.get(getattr(info, "trade_mode", -1), "unknown")
        return {"equity": float(info.equity), "balance": float(info.balance), "currency": info.currency,
                "server": info.server, "login": info.login, "mode": mode}

    @_locked
    def resolve_symbol(self, symbol: str) -> str:
        if symbol in self._names:
            return self._names[symbol]
        spec = self.settings.instruments[symbol]
        candidates = [c for c in [spec.broker_symbol, symbol, *SYMBOL_ALIASES.get(symbol, [])] if c]
        for candidate in candidates:
            if self.mt5.symbol_info(candidate) is not None:
                self.mt5.symbol_select(candidate, True)
                self._names[symbol] = candidate
                return candidate
        for candidate in candidates:  # broker suffixes such as EURUSD.m or XAUUSD+
            matches = self.mt5.symbols_get(f"*{candidate}*") or []
            if matches:
                name = matches[0].name
                self.mt5.symbol_select(name, True)
                self._names[symbol] = name
                return name
        raise MarketDataError(f"{symbol} is not available at this broker - untick it in the sidebar, "
                              f"or set its broker_symbol in settings.json")

    def _value_checks(self, name: str, info, spec, account_ccy: str) -> dict:
        """
        Three independent measures of the account-currency value of a 1.0 price move on 1 lot:
        the broker's tick value, the broker's own profit calculator, and contract size x conversion.
        """
        out = {}
        tick_size = float(info.trade_tick_size or 0.0)
        tick_value = float(getattr(info, "trade_tick_value_loss", 0.0) or info.trade_tick_value or 0.0)
        if tick_size > 0 and tick_value > 0:
            out["broker tick value"] = tick_value / tick_size
        tick = self.mt5.symbol_info_tick(name)
        price = float(tick.bid) if tick is not None and tick.bid > 0 else 0.0
        calc = getattr(self.mt5, "order_calc_profit", None)  # a calculation only - it sends nothing
        if calc is not None and price > 0:
            delta = max(tick_size * 100, price * 0.001)
            vals = []
            for action, close in ((self.mt5.ORDER_TYPE_BUY, price - delta), (self.mt5.ORDER_TYPE_SELL, price + delta)):
                profit = calc(action, name, 1.0, price, close)
                if profit:
                    vals.append(abs(float(profit)) / delta)
            if vals:
                out["broker profit calculator"] = max(vals)
        a = account_ccy.upper()
        if price > 0 and a in (spec.quote_currency.upper(), spec.base_currency.upper()):
            out["contract size"] = value_per_price_unit(spec, price, a, {})
        return out

    @_locked
    def broker_spec(self, symbol: str, account_ccy: Optional[str] = None):
        name = self.resolve_symbol(symbol)
        info = self.mt5.symbol_info(name)
        if info is None:
            raise MarketDataError(f"No symbol info for {name}")
        base = self.settings.instruments[symbol]
        spec = replace(
            base, broker_symbol=name, contract_size=float(info.trade_contract_size),
            tick_size=float(info.trade_tick_size), volume_min=float(info.volume_min),
            volume_step=float(info.volume_step), volume_max=float(info.volume_max), digits=int(info.digits),
            quote_currency=info.currency_profit or base.quote_currency,
            base_currency=info.currency_base or base.base_currency,
        )
        account_ccy = account_ccy or self.account()["currency"]
        checks = self._value_checks(name, info, spec, account_ccy)
        if not checks:
            raise MarketDataError(f"{name}: broker returned no tick value (market closed or symbol disabled)")
        # Use the largest value = the smallest position size. Never size from an optimistic figure.
        value = max(checks.values())
        note = ""
        if value > min(checks.values()) * 1.05:
            detail = ", ".join(f"{k} {v:,.2f}" for k, v in checks.items())
            note = (f"{symbol}: broker figures for the value of a 1.0 price move disagree ({detail} {account_ccy} "
                    f"per lot). Using the most cautious, {value:,.2f}.")
        self.value_info[symbol] = {"checks": checks, "used": value, "note": note}
        return spec, value, info

    def _server_now(self, tick_time_s: float, notes: List[str]) -> Optional[pd.Timestamp]:
        utc_now = time.time()
        if self.settings.data.mt5_server_utc_offset_hours is None:
            # The broker clock is UTC plus whole hours. Only a FRESH tick (within 2 minutes of a whole hour away
            # from UTC) can tell us the offset; a known offset only changes after the same new value twice in a
            # row, so one old tick (e.g. after MT5 lost its connection) cannot shift every time on the desk.
            diff = tick_time_s - utc_now
            hours = round(diff / 3600.0)
            if abs(hours) <= 14 and abs(diff - hours * 3600.0) <= 120:
                if self._offset_hours is None or hours == self._offset_hours or hours == self._offset_candidate:
                    self._offset_hours, self._offset_candidate = hours, None
                else:
                    self._offset_candidate = hours
        if self._offset_hours is None:
            notes.append("Broker time offset unknown (no fresh tick). Set data.mt5_server_utc_offset_hours.")
            return None
        return pd.Timestamp(utc_now + self._offset_hours * 3600.0, unit="s")

    def timeframe(self, minutes: int):
        """MT5's constant for an N-minute candle (M1, M2, M3, M5, M15)."""
        try:
            return getattr(self.mt5, f"TIMEFRAME_M{int(minutes)}")
        except AttributeError:
            raise MarketDataError(f"MetaTrader 5 has no {minutes}-minute candles") from None

    @_locked
    def snapshot(self, symbol: str, base_minutes: int = 1) -> MarketSnapshot:
        mt5 = self.mt5
        self._ensure_connected()
        term = mt5.terminal_info()
        if term is not None and not getattr(term, "connected", True):
            raise MarketDataError("MetaTrader 5 has lost its connection to the broker server, so prices have "
                                  "stopped. Check the status in the bottom-right corner of MT5 and log in again "
                                  "(File > Login to Trade Account). The bot reconnects by itself.")
        acct = self.account()
        spec, value_per_unit, info = self.broker_spec(symbol, acct["currency"])
        tf = self.timeframe(base_minutes)
        rates = mt5.copy_rates_from_pos(spec.broker_symbol, tf, 0, int(self.settings.data.history_minutes) + 1)
        if rates is None or len(rates) == 0:
            raise MarketDataError(f"No candles for {spec.broker_symbol}: {mt5.last_error()}")
        df = pd.DataFrame(rates)
        df["spread"] = df["spread"].astype(float) * float(info.point)
        bars = normalize_bars(df, spec.typical_spread)
        tick = mt5.symbol_info_tick(spec.broker_symbol)
        if tick is None or tick.bid <= 0 or tick.ask <= 0:
            raise MarketDataError(f"No live price for {spec.broker_symbol}")
        notes: List[str] = []
        if self.value_info.get(symbol, {}).get("note"):
            notes.append(self.value_info[symbol]["note"])
        now = self._server_now(float(tick.time), notes)
        return MarketSnapshot(
            symbol=symbol, bars_1m=bars, bid=float(tick.bid), ask=float(tick.ask),
            tick_time=pd.Timestamp(int(tick.time_msc), unit="ms"), now=now, spec=spec,
            value_per_unit=value_per_unit, source=self.name,
            account_equity=acct["equity"], account_currency=acct["currency"],
            notes=notes + [f"MT5 {acct['mode']} account {acct['login']} on {acct['server']}"], base_minutes=base_minutes,
        )

    @_locked
    def quote(self, symbol: str) -> Quote:
        """Latest bid/ask straight from MT5 (cheap; used by the live price box every few seconds)."""
        self._ensure_connected()
        name = self.resolve_symbol(symbol)
        tick = self.mt5.symbol_info_tick(name)
        if tick is None or tick.bid <= 0 or tick.ask <= 0:
            raise MarketDataError(f"No live price for {name} (market closed?)")
        return Quote(float(tick.bid), float(tick.ask), pd.Timestamp(int(tick.time_msc), unit="ms"))

    @_locked
    def download_history(self, symbol: str, days: int = 30, minutes: int = 1, save: bool = True) -> pd.DataFrame:
        """
        Save history to data/csv/<SYMBOL>.csv (1-minute candles) or <SYMBOL>_M5.csv etc. (other sizes).
        MT5 keeps about 100,000 candles per chart by default: ~69 days of 1-minute or ~1.4 years of 5-minute.
        """
        self._ensure_connected()
        spec, _, info = self.broker_spec(symbol)
        tf = self.timeframe(minutes)
        rates = self.mt5.copy_rates_from_pos(spec.broker_symbol, tf, 0, int(days * 1440 / minutes))
        if rates is None or len(rates) == 0:
            raise MarketDataError(f"No history for {spec.broker_symbol}: {self.mt5.last_error()}")
        df = pd.DataFrame(rates)
        df["spread"] = df["spread"].astype(float) * float(info.point)
        bars = normalize_bars(df, spec.typical_spread)
        if save:
            CSV_DIR.mkdir(parents=True, exist_ok=True)
            bars.to_csv(CSV_DIR / (f"{symbol}.csv" if minutes == 1 else f"{symbol}_M{minutes}.csv"), index=False)
        return bars


def get_source(settings: Settings, source: Optional[str] = None):
    source = source or settings.data.source
    if source == "mt5":
        return MT5Source(settings)
    if source == "csv":
        return CsvSource(settings)
    return DemoSource(settings)
