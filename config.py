"""
Central configuration for the personal trading bot.

Edit settings.json (copy settings.example.json) to change values without
touching this file. Anything in settings.json overrides the defaults below.

SAFETY: the bot never trades by itself. LIVE_TRADING_ENABLED (automatic,
unattended orders) is a hard switch that stays False and is not implemented.
Orders can only be sent by you, one at a time, with the dashboard's Place order
and Confirm buttons (broker.py). Those buttons work on MT5 demo accounts only,
unless you set trading.allow_real_account to true in settings.json.
"""
from __future__ import annotations

import copy
import json
import os
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Dict, List, Optional

PROJECT_DIR = Path(__file__).resolve().parent
DATA_DIR = PROJECT_DIR / "data"
CSV_DIR = DATA_DIR / "csv"
SNAPSHOT_DIR = DATA_DIR / "snapshots"
DB_PATH = DATA_DIR / "trading_bot.sqlite3"
SETTINGS_FILE = PROJECT_DIR / "settings.json"
EMERGENCY_STOP_FILE = DATA_DIR / "EMERGENCY_STOP"

LIVE_TRADING_ENABLED = False  # hard-coded: automatic (unattended) orders are not implemented

# Timeframe label -> minutes. Order matters: highest first is how the bot reads them.
# 60m is only used when StrategySettings.use_hourly_trend is switched on.
TIMEFRAMES: Dict[str, int] = {"60m": 60, "15m": 15, "10m": 10, "5m": 5, "3m": 3, "1m": 1}

TIMEFRAME_ROLES = {
    "15m": "Main trend",
    "10m": "Trend agreement",
    "5m": "Trade setup",
    "3m": "Setup confirmation",
    "1m": "Entry timing",
}


@dataclass
class InstrumentSpec:
    """Contract specification. With MT5 connected, broker values replace these."""

    symbol: str
    asset_class: str  # forex | metal | energy | index | stock
    base_currency: str
    quote_currency: str  # currency P&L is calculated in before conversion
    contract_size: float  # units per 1 lot (1 for shares)
    tick_size: float
    volume_min: float
    volume_step: float
    volume_max: float
    unit: str = "lots"  # "lots" or "shares"
    digits: int = 5
    typical_spread: float = 0.0  # price units; used for demo data / when feed has none
    max_spread: float = 0.0  # spread filter, price units
    slippage: float = 0.0  # assumed adverse slippage per fill, price units
    commission_per_unit_side: float = 0.0  # account currency per lot (or share) per side
    enabled: bool = True
    broker_symbol: str = ""  # symbol name at your broker if different (e.g. "EURUSD.m")
    demo_start_price: float = 1.0
    demo_volatility: float = 0.0001  # per-minute standard deviation as a fraction of price
    tested_profitable: bool = False  # passed the out-of-sample backtests (see README section 7)
    test_note: str = "Not tested profitable - treat its signals as information only"

    def to_dict(self) -> dict:
        return asdict(self)


FX_NOTE = ("Lost money after costs with every rule set tested: 1-minute (Jul-Oct 2026), 5-minute swing and "
           "3-minute (2-3 years to Nov 2026) - information only")


def _fx(symbol, base, quote, price, spread, max_spread, slip, digits=5, tick=0.00001, enabled=False, vol=0.00009):
    return InstrumentSpec(
        symbol=symbol, asset_class="forex", base_currency=base, quote_currency=quote,
        contract_size=100_000, tick_size=tick, volume_min=0.01, volume_step=0.01, volume_max=50,
        digits=digits, typical_spread=spread, max_spread=max_spread, slippage=slip,
        commission_per_unit_side=3.5, enabled=enabled, demo_start_price=price, demo_volatility=vol,
        test_note=FX_NOTE,
    )


# Typical values only. Brokers differ (contract size for oil and indices especially),
# so verify against your broker's contract specification before trusting sizes.
DEFAULT_INSTRUMENTS: Dict[str, InstrumentSpec] = {
    "EURUSD": _fx("EURUSD", "EUR", "USD", 1.0850, 0.00008, 0.00025, 0.00003),
    "GBPUSD": _fx("GBPUSD", "GBP", "USD", 1.2700, 0.00010, 0.00030, 0.00003),
    "USDJPY": _fx("USDJPY", "USD", "JPY", 150.00, 0.010, 0.030, 0.003, digits=3, tick=0.001),
    "XAUUSD": InstrumentSpec(
        symbol="XAUUSD", asset_class="metal", base_currency="XAU", quote_currency="USD",
        contract_size=100, tick_size=0.01, volume_min=0.01, volume_step=0.01, volume_max=50,
        digits=2, typical_spread=0.25, max_spread=0.60, slippage=0.08, commission_per_unit_side=3.5,
        enabled=True, demo_start_price=2400.0, demo_volatility=0.00022, tested_profitable=True,
        test_note="Gold: 5-minute swing and 3-minute rules slightly profitable over 2-3 years to Nov 2026; the "
                  "1-minute rules lost money (README sections 7d-7f). The only market that passed. Not proven.",
    ),
    "XAGUSD": InstrumentSpec(
        symbol="XAGUSD", asset_class="metal", base_currency="XAG", quote_currency="USD",
        contract_size=5000, tick_size=0.001, volume_min=0.01, volume_step=0.01, volume_max=50,
        digits=3, typical_spread=0.025, max_spread=0.06, slippage=0.005, commission_per_unit_side=3.5,
        enabled=False, demo_start_price=30.0, demo_volatility=0.0003,
        test_note="Silver lost money in the Nov 2026 tests on 2-3 years of MT5 history (swing -0.12R, 3-minute -0.12R per trade) - information only",
    ),
    "USOIL": InstrumentSpec(
        symbol="USOIL", asset_class="energy", base_currency="WTI", quote_currency="USD",
        contract_size=1000, tick_size=0.01, volume_min=0.01, volume_step=0.01, volume_max=50,
        digits=2, typical_spread=0.03, max_spread=0.08, slippage=0.01, commission_per_unit_side=0.0,
        enabled=False, demo_start_price=75.0, demo_volatility=0.0003,
        test_note="Lost money in the Nov 2026 tests. On MetaQuotes-Demo the oil symbol found (price about 3) is not WTI crude oil - information only",
    ),
    "US500": InstrumentSpec(
        symbol="US500", asset_class="index", base_currency="SPX", quote_currency="USD",
        contract_size=1, tick_size=0.01, volume_min=0.1, volume_step=0.1, volume_max=100,
        digits=2, typical_spread=0.5, max_spread=1.5, slippage=0.2, commission_per_unit_side=0.0,
        enabled=False, demo_start_price=5500.0, demo_volatility=0.00015,
        test_note="US500 lost money in the Nov 2026 tests on 3 years of MT5 history (swing -0.07R, 3-minute -0.07R per trade) - information only",
    ),
    "NAS100": InstrumentSpec(
        symbol="NAS100", asset_class="index", base_currency="NDX", quote_currency="USD",
        contract_size=1, tick_size=0.01, volume_min=0.1, volume_step=0.1, volume_max=100,
        digits=2, typical_spread=1.5, max_spread=3.0, slippage=0.5, commission_per_unit_side=0.0,
        enabled=False, demo_start_price=19000.0, demo_volatility=0.0002,
        test_note="NAS100 lost money with the swing rules (-0.05R per trade); 3-minute about break-even (+0.02R) and losing in 2026 - information only",
    ),
    "AAPL": InstrumentSpec(
        symbol="AAPL", asset_class="stock", base_currency="AAPL", quote_currency="USD",
        contract_size=1, tick_size=0.01, volume_min=1, volume_step=1, volume_max=10_000, unit="shares",
        digits=2, typical_spread=0.02, max_spread=0.06, slippage=0.01, commission_per_unit_side=0.005,
        enabled=False, demo_start_price=200.0, demo_volatility=0.0004,
        test_note="Apple lost money in the Nov 2026 tests on 8-10 years of MT5 history (swing -0.20R, 3-minute -0.17R per trade) - information only",
    ),
    "MSFT": InstrumentSpec(
        symbol="MSFT", asset_class="stock", base_currency="MSFT", quote_currency="USD",
        contract_size=1, tick_size=0.01, volume_min=1, volume_step=1, volume_max=10_000, unit="shares",
        digits=2, typical_spread=0.03, max_spread=0.08, slippage=0.01, commission_per_unit_side=0.005,
        enabled=False, demo_start_price=420.0, demo_volatility=0.0004,
        test_note="Microsoft lost money in the Nov 2026 tests on 8-10 years of MT5 history (swing -0.42R, 3-minute -0.18R per trade) - information only",
    ),
}


@dataclass
class DataSettings:
    source: str = "demo"  # demo | mt5 | csv
    strategy_profile: str = "both"  # both = swing + medium together | swing | medium | fast (watch only); profiles.py
    history_minutes: int = 5000  # candles fetched per scan (enough to warm up the slowest EMA 50)
    mt5_terminal_path: str = ""  # leave blank to use the running/default MT5 terminal
    mt5_login: int = 0  # 0 = use the account already logged in to the terminal
    mt5_password: str = ""
    mt5_server: str = ""
    mt5_server_utc_offset_hours: Optional[float] = None  # None = detect from latest tick


@dataclass
class RiskSettings:
    account_currency: str = "USD"
    account_equity: float = 1000.0  # used when the broker account is not connected
    risk_per_trade_pct: float = 0.25
    daily_loss_limit_pct: float = 1.0
    max_trades_per_day: int = 5
    max_consecutive_losses: int = 3
    target_reward_risk: float = 2.0  # target distance as a multiple of the stop distance
    min_net_reward_risk: float = 1.6  # after commission and slippage
    obstacle_min_reward_risk: float = 1.5  # opposing 15m swing must leave at least this much room
    max_spread_to_stop_ratio: float = 0.20
    max_leverage: float = 10.0
    stale_bar_seconds: int = 150  # last closed 1m candle must have closed within this time
    stale_tick_seconds: int = 90
    max_gap_minutes: int = 3  # missing-candle tolerance inside the recent window
    gap_check_minutes: int = 30
    signal_cooldown_minutes: int = 15  # same symbol + direction is not re-alerted inside this window
    max_hold_minutes: int = 240  # backtest time stop
    breakeven_at_r: float = 0.0  # move the stop to the entry price once the trade is this many R in profit (0 = off)
    partial_at_r: float = 0.0  # take part of the profit at this many R, then move the stop to entry (0 = off)
    partial_fraction: float = 0.5  # share of the position closed at partial_at_r
    take_half_at_1r: Optional[bool] = None  # dashboard switch: True = half at the strategy's level (1R if none), False = off
    blocked_hours: List[int] = field(default_factory=list)  # broker-clock hours with no new trades, e.g. [22, 23] (1-minute rules only)
    fx_rates_to_account: Dict[str, float] = field(default_factory=lambda: {
        "USD": 1.0, "EUR": 1.085, "GBP": 1.27, "JPY": 1 / 150.0, "CHF": 1.12, "CAD": 0.73, "AUD": 0.66,
    })


@dataclass
class StrategySettings:
    ema_fast: int = 20
    ema_slow: int = 50
    rsi_period: int = 14
    atr_period: int = 14
    swing_strength: int = 3  # bars on each side that define a swing high/low
    trend_slope_bars: int = 3
    pullback_bars: int = 6
    pullback_atr_tolerance: float = 0.25
    breakout_bars: int = 20
    max_breakout_extension_atr: float = 1.0
    retest_bars: int = 8
    retest_atr_tolerance: float = 0.30
    stop_lookback_bars: int = 6
    stop_buffer_atr: float = 0.50  # 0.5 beat 0.2 in both halves of 3 months of MT5 history (Oct 2026 study)
    min_stop_atr: float = 0.5
    max_stop_atr: float = 3.0
    rsi_mid: float = 50.0
    rejection_wick_ratio: float = 0.40
    max_entry_candle_atr: float = 2.5
    use_hourly_trend: bool = False  # also require the 1-hour trend to agree (1-minute rules only)
    min_trend_strength_atr: float = 0.0  # 15m EMA20-EMA50 gap must be at least this many ATR (0 = off)
    min_adx: float = 0.0  # main-trend ("15m") ADX(14) must be at least this (trend strong enough; 0 = off)


@dataclass
class AlertSettings:
    telegram_enabled: bool = False
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
    desktop_popup: bool = True  # Windows pop-up on top of every window for each new signal you can trade
    popup_watch_only: bool = False  # also pop up for watch-only signals (untested markets or strategies)
    fullscreen_popup: bool = True  # the pop-up covers the whole screen until closed (false = small message box)


@dataclass
class TradingSettings:
    """Manual orders from the dashboard (you click Place order, then Confirm). See broker.py."""
    allow_real_account: bool = False  # False = order buttons only work on MT5 demo accounts
    max_open_positions: int = 2  # open trades from this desk, all markets together
    max_price_move_fraction: float = 0.25  # refuse if price moved more than this share of the stop distance
    max_signal_age_seconds: int = 120  # refuse if the prices behind the signal are older than this
    confirm_seconds: int = 30  # the Confirm button expires after this
    deviation_points: int = 20  # largest slippage MT5 may accept when filling, in points
    magic_number: int = 260427  # tags this desk's orders in MT5 so it only manages its own trades
    allow_untested_markets: bool = False  # False = order buttons and pop-ups only for markets and strategies that passed testing
    move_stop_after_half: bool = True  # after the first half takes profit, move the other half's stop to entry


@dataclass
class Settings:
    data: DataSettings = field(default_factory=DataSettings)
    risk: RiskSettings = field(default_factory=RiskSettings)
    strategy: StrategySettings = field(default_factory=StrategySettings)
    alerts: AlertSettings = field(default_factory=AlertSettings)
    trading: TradingSettings = field(default_factory=TradingSettings)
    instruments: Dict[str, InstrumentSpec] = field(default_factory=lambda: copy.deepcopy(DEFAULT_INSTRUMENTS))

    def enabled_symbols(self):
        return [s for s, spec in self.instruments.items() if spec.enabled]

    def to_dict(self) -> dict:
        return asdict(self)


def _apply(obj, overrides: dict, path: str):
    known = {f.name for f in fields(obj)}
    for key, value in overrides.items():
        if key not in known:
            raise ValueError(f"Unknown setting '{path}.{key}' in settings.json")
        setattr(obj, key, value)


def load_settings(path: Optional[Path] = None) -> Settings:
    """Defaults, then settings.json overrides, then environment variables for secrets."""
    settings = Settings()
    path = Path(path) if path else SETTINGS_FILE
    if path.exists():
        raw = json.loads(path.read_text(encoding="utf-8"))
        for section in ("data", "risk", "strategy", "alerts", "trading"):
            if section in raw:
                _apply(getattr(settings, section), raw[section], section)
        for symbol, spec_over in raw.get("instruments", {}).items():
            if symbol in settings.instruments:
                _apply(settings.instruments[symbol], spec_over, f"instruments.{symbol}")
            else:
                settings.instruments[symbol] = InstrumentSpec(symbol=symbol, **spec_over)
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat = os.getenv("TELEGRAM_CHAT_ID")
    if token:
        settings.alerts.telegram_bot_token = token
    if chat:
        settings.alerts.telegram_chat_id = chat
    validate_settings(settings)
    return settings


def settings_from_dict(raw: dict) -> Settings:
    """Rebuild Settings from Settings.to_dict() output (used to reproduce saved signals)."""
    s = Settings(
        data=DataSettings(**raw["data"]),
        risk=RiskSettings(**raw["risk"]),
        strategy=StrategySettings(**raw["strategy"]),
        alerts=AlertSettings(**{**raw.get("alerts", {}), "telegram_bot_token": "", "telegram_chat_id": ""}),
        trading=TradingSettings(**raw.get("trading", {})),
        instruments={k: InstrumentSpec(**v) for k, v in raw["instruments"].items()},
    )
    return s


def validate_settings(s: Settings) -> None:
    r = s.risk
    if not (0 < r.risk_per_trade_pct <= 2.0):
        raise ValueError("risk_per_trade_pct must be between 0 and 2 (percent)")
    if not (0 < r.daily_loss_limit_pct <= 10):
        raise ValueError("daily_loss_limit_pct must be between 0 and 10 (percent)")
    if r.target_reward_risk < r.min_net_reward_risk:
        raise ValueError("target_reward_risk cannot be below min_net_reward_risk")
    if s.strategy.ema_fast >= s.strategy.ema_slow:
        raise ValueError("ema_fast must be shorter than ema_slow")
    if not (0 <= r.partial_at_r < r.target_reward_risk):
        raise ValueError("partial_at_r must be 0 (off) or below target_reward_risk")
    if not (0.1 <= r.partial_fraction <= 0.9):
        raise ValueError("partial_fraction must be between 0.1 and 0.9")
    t = s.trading
    for flag in ("allow_real_account", "allow_untested_markets", "move_stop_after_half"):
        if not isinstance(getattr(t, flag), bool):
            raise ValueError(f"trading.{flag} must be true or false (without quotes)")
    for flag in ("desktop_popup", "popup_watch_only", "fullscreen_popup"):
        if not isinstance(getattr(s.alerts, flag), bool):
            raise ValueError(f"alerts.{flag} must be true or false (without quotes)")
    if not isinstance(t.allow_real_account, bool):
        raise ValueError("trading.allow_real_account must be true or false (without quotes)")
    for name in ("max_open_positions", "max_signal_age_seconds", "confirm_seconds", "deviation_points", "magic_number"):
        if not isinstance(getattr(t, name), int) or isinstance(getattr(t, name), bool):
            raise ValueError(f"trading.{name} must be a whole number")
    if not isinstance(t.max_price_move_fraction, (int, float)) or isinstance(t.max_price_move_fraction, bool):
        raise ValueError("trading.max_price_move_fraction must be a number")
    if not (1 <= t.max_open_positions <= 10):
        raise ValueError("trading.max_open_positions must be between 1 and 10")
    if not (0 < t.max_price_move_fraction <= 1):
        raise ValueError("trading.max_price_move_fraction must be above 0 and at most 1")
    if s.data.strategy_profile not in ("both", "fast", "medium", "swing"):
        raise ValueError("data.strategy_profile must be both, swing, medium or fast")
    if s.data.source not in ("demo", "mt5", "csv"):
        raise ValueError("data.source must be demo, mt5 or csv")
    for sym, spec in s.instruments.items():
        if spec.volume_step <= 0 or spec.volume_min <= 0 or spec.contract_size <= 0 or spec.tick_size <= 0:
            raise ValueError(f"{sym}: contract size, tick size and volume limits must be positive")


def save_alert_settings(values: dict, path: Optional[Path] = None) -> None:
    """Write alert switches (desktop_popup, fullscreen_popup, popup_watch_only) into settings.json, keeping
    everything else in the file as it is. Creates the file if there is none."""
    path = Path(path) if path else SETTINGS_FILE
    raw = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    alerts = raw.setdefault("alerts", {})
    for key, value in values.items():
        if key not in ("desktop_popup", "fullscreen_popup", "popup_watch_only") or not isinstance(value, bool):
            raise ValueError(f"alerts.{key} cannot be saved from the dashboard")
        alerts[key] = value
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(raw, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def ensure_dirs() -> None:
    for d in (DATA_DIR, CSV_DIR, SNAPSHOT_DIR):
        d.mkdir(parents=True, exist_ok=True)
