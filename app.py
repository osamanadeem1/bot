"""
Dashboard:  streamlit run app.py   (or double-click run_dashboard.bat)

Tabs: Desk | Journal | Backtest | Calculator | System
The bot never trades by itself. With MT5 and "Order buttons" switched on, a live
signal gets a Place order button; nothing is sent until you click Confirm (broker.py).
"""
from __future__ import annotations

import copy
import json
import time
from datetime import datetime, timedelta

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from streamlit.errors import StreamlitAPIException

import alerts
import ui
from backtest import results_by, split_report
from broker import Trader
from candles import build_timeframes, closed_bars
from config import CSV_DIR, LIVE_TRADING_ENABLED, ensure_dirs, load_settings, save_alert_settings
from fullscreen_alert import TEST as TEST_ALERT
from indicators import add_indicators
from market_data import MarketDataError, MT5Source, generate_demo_bars, get_source, load_csv_bars
from position_sizing import build_trade_plan, scenario, size_position, value_per_price_unit
from profiles import (MODE_KEYS, PROFILES, engine_bars, get_profile, half_level, hold_hours, lanes, mode_name,
                      mode_note, profile_settings, replay, takes_half, to_engine, to_real)
from risk_manager import activate_emergency_stop, clear_emergency_stop, emergency_stop_status
from scanner import scan_symbols
from signal_status import follow, running_r
from strategy import fmt
from timefmt import date_text, datetime_text, format_columns, time_text, when_text
from trade_logger import TradeLogger
from ui import money

st.set_page_config(page_title="Signal desk", page_icon="📈", layout="wide")
ensure_dirs()
st.markdown(ui.CSS, unsafe_allow_html=True)


@st.cache_resource
def get_logger() -> TradeLogger:
    return TradeLogger()


@st.cache_resource(show_spinner="Connecting to the data source...")
def get_cached_source(name: str):
    s = load_settings()
    s.data.source = name
    return get_source(s)


try:
    settings = load_settings()
except Exception as exc:  # bad settings.json should be visible, not silent
    st.error(f"settings.json problem: {exc}")
    st.stop()
logger = get_logger()
CCY = settings.risk.account_currency

def save_alerts() -> None:
    """Keep the Alerts switches after a restart (settings.json)."""
    try:
        save_alert_settings({"desktop_popup": st.session_state["al_pop"],
                             "fullscreen_popup": st.session_state["al_full"],
                             "popup_watch_only": st.session_state["al_watch"]})
        st.session_state.pop("al_error", None)
    except Exception as exc:
        st.session_state["al_error"] = str(exc)


alerts.DESK_URL = f"http://localhost:{st.get_option('server.port') or 8501}"

# ------------------------------------------------------------------ sidebar
with st.sidebar:
    st.markdown("### Controls")
    stopped, why = emergency_stop_status()
    if stopped:
        st.error(f"Emergency stop is on. No signals until you resume.\n\n{why}")
        if st.button("Resume signals", width="stretch"):
            clear_emergency_stop()
            logger.log_event("Emergency stop cleared from dashboard", "WARNING")
            st.rerun()
    else:
        if st.button("Emergency stop", type="primary", width="stretch",
                     help="Stops all signals immediately, including after a restart, until you resume."):
            activate_emergency_stop("Stopped from dashboard")
            logger.log_event("Emergency stop activated from dashboard", "WARNING")
            st.rerun()
    st.caption("The bot never trades by itself" + ("" if not LIVE_TRADING_ENABLED else " (AUTOMATIC ORDERS ON)") + ".")
    st.divider()
    sources = ["demo", "mt5", "csv"]
    source_name = st.selectbox("Data source", sources, index=sources.index(settings.data.source),
                               format_func=lambda s: {"demo": "Demo (synthetic prices)", "mt5": "MetaTrader 5 (live)",
                                                      "csv": "CSV files (replay)"}[s])
    profile_key = st.selectbox("Strategy", MODE_KEYS, index=MODE_KEYS.index(settings.data.strategy_profile),
                               format_func=mode_name,
                               help="The same five-timeframe rules on 5-, 3- or 1-minute candles. Both runs the "
                                    "5-minute swing and 3-minute rules together, each with its own trades.")
    LANES = lanes(profile_key)  # the strategies this mode runs
    MAIN = LANES[0]  # the one the half-profit switch is about
    prof = get_profile(MAIN)
    st.caption(mode_note(profile_key))
    lvl = half_level(MAIN)
    for_what = f" ({prof.short})" if len(LANES) > 1 else ""
    half = st.toggle(f"Take half profit at {lvl:g}R{for_what}", value=takes_half(MAIN), key=f"half_{MAIN}",
                     help=f"Half the position takes profit when the trade is {lvl:g}R in profit ({lvl:g} times the "
                          "stop-loss distance, the other way); the stop on the rest then moves to your entry price. "
                          "More trades end in profit and fewer hit the full stop-loss, but the winners are smaller. "
                          "Gold 5-minute swing over 3 years: on = 28% full stop-losses, +0.08R per trade; "
                          "off = 49% full stop-losses, +0.13R per trade. Gold 3-minute: on cuts the profit by more "
                          "than half (README section 7e).")
    settings.risk.take_half_at_1r = half
    all_symbols = list(settings.instruments)
    symbols = st.multiselect("Markets", all_symbols, default=settings.enabled_symbols())
    tested = [s for s in all_symbols if settings.instruments[s].tested_profitable]
    st.caption(f"Tested profitable in backtests: {', '.join(tested) or 'none'}. "
               "Other markets lost money in testing, so their signals are marked Untested.")
    settings.risk.account_equity = st.number_input(
        f"Account equity ({CCY})", min_value=1.0, value=float(settings.risk.account_equity), step=100.0,
        help="Used when no broker account is connected. With MT5 the live account equity is used.")
    settings.risk.risk_per_trade_pct = st.number_input(
        "Risk per trade (%)", min_value=0.05, max_value=2.0, value=float(settings.risk.risk_per_trade_pct), step=0.05)
    auto = st.toggle("Refresh every minute", value=True,
                     help="A signal is live only until the next candle closes. Keep this on.")
    settings.data.source = source_name
    st.divider()
    st.markdown("### Orders")
    trade_on = st.toggle("Order buttons", value=False, key="trade_on", disabled=source_name != "mt5",
                         help="Adds a Place order button to live signals. Nothing is sent until you click Confirm.")
    if source_name != "mt5":
        trade_on = False
        st.caption("Order buttons need MetaTrader 5 (live) as the data source.")
    else:
        st.caption("Off each time the dashboard opens. Every order needs your Confirm click and goes to MT5 with "
                   "its stop-loss and target. Demo accounts only, unless settings.json allows real money."
                   + (" Real money is ALLOWED in settings.json." if settings.trading.allow_real_account else ""))
    st.divider()
    st.markdown("### Alerts")
    al = settings.alerts
    pop_on = st.toggle("Pop-up for new signals", value=al.desktop_popup, key="al_pop", on_change=save_alerts,
                       help="A window on top of everything else when a new signal comes, with a sound.")
    full_on = st.toggle("Full screen", value=al.fullscreen_popup, key="al_full", on_change=save_alerts,
                        disabled=not pop_on,
                        help="The pop-up covers the whole screen until you close it (Esc or Close). Off: a small "
                             "message box instead.")
    watch_on = st.toggle("Also for watch-only markets", value=al.popup_watch_only, key="al_watch",
                         on_change=save_alerts, disabled=not pop_on,
                         help="Markets marked Untested lost money in testing. Off: only signals you can trade from "
                              "the desk (gold) pop up; the others still show on the desk and in Latest signals.")
    al.desktop_popup, al.fullscreen_popup, al.popup_watch_only = pop_on, full_on, watch_on
    if st.session_state.get("al_error"):
        st.caption(f"Could not save: {st.session_state['al_error']}")
    if st.button("Show a test alert", width="stretch", disabled=not pop_on,
                 help="Shows what a new signal looks like, clearly marked as a test."):
        test = {**TEST_ALERT, "desk_url": alerts.DESK_URL, "signalled": time_text(datetime.now())}
        shown = (alerts.show_fullscreen_alert(test) if full_on else
                 alerts.show_desktop_popup("Signal desk: test alert", "Test alert: this is not a real signal."))
        if not shown:
            st.warning("Pop-ups open on the Windows PC that runs the dashboard. They could not open here.")
    st.caption("Switches here are saved in settings.json.")


def lane_settings(lane: str):
    """The settings one strategy runs with. In both mode the half-profit switch is about the 5-minute swing only:
    the 3-minute rules keep their own tested choice (no half)."""
    s = copy.deepcopy(settings)
    if lane != MAIN:
        s.risk.take_half_at_1r = None
    return s


LANE_CFG = {lane: lane_settings(lane) for lane in LANES}


def lane_name(lane: str) -> str:
    return get_profile(lane).name


tab_desk, tab_journal, tab_bt, tab_calc, tab_sys = st.tabs(["Desk", "Journal", "Backtest", "Calculator", "System"])


# ------------------------------------------------------------------ desk helpers
def candle_chart(sig, snap, lane: str):
    """The setup timeframe (5-minute candles, or 25-minute for the swing rules), with the plan's levels."""
    base = snap.base_minutes
    now = snap.now if snap.now is not None else snap.bars_1m["time"].iloc[-1] + pd.Timedelta(minutes=base)
    frames = build_timeframes(engine_bars(snap.bars_1m, base), to_engine(now, base))
    df = add_indicators(frames["5m"], profile_settings(LANE_CFG[lane], lane).strategy).tail(96)
    df["time"] = _to_local(to_real(df["time"], base), get_cached_source(source_name))  # this PC's clock
    fig = go.Figure(go.Candlestick(
        x=df["time"], open=df["open"], high=df["high"], low=df["low"], close=df["close"], name=f"{5 * base}-minute",
        increasing_line_color=ui.BUY_C, increasing_fillcolor=ui.BUY_C,
        decreasing_line_color=ui.SELL_C, decreasing_fillcolor=ui.SELL_C))
    fig.add_scatter(x=df["time"], y=df["ema_fast"], name=f"EMA {settings.strategy.ema_fast}",
                    line=dict(width=1.5, color=ui.BRASS))
    fig.add_scatter(x=df["time"], y=df["ema_slow"], name=f"EMA {settings.strategy.ema_slow}",
                    line=dict(width=1.5, color=ui.SLATE))
    if sig.plan:
        for y, name, color in [(sig.plan.target, "Target", ui.BUY_C), (sig.plan.entry, "Entry", ui.INK),
                               (sig.plan.stop, "Stop", ui.SELL_C)]:
            fig.add_hline(y=y, line_dash="dot", line_color=color, annotation_text=f"{name} {fmt(y)}",
                          annotation_font_color=color)
    fig.update_layout(height=390, margin=dict(l=6, r=6, t=6, b=6), paper_bgcolor="white", plot_bgcolor="white",
                      font=dict(family="Archivo, Segoe UI, sans-serif", color=ui.INK, size=12),
                      xaxis_rangeslider_visible=False, legend=dict(orientation="h", y=1.0, x=0, yanchor="bottom"),
                      xaxis=dict(gridcolor="#EEF1F4", tickformat="%I:%M%p", hoverformat="%d-%m-%Y %I:%M%p"),
                      yaxis=dict(gridcolor="#EEF1F4", side="right"))
    return fig


def outcome_text(reason: str, lane: str) -> str:
    h = hold_hours(LANE_CFG[lane], lane)
    return {"target": "Target", "stop": "Stop", "stop (gap)": "Stop", "breakeven": "Break-even",
            "time": f"{h:g}h exit", "end of data": "Running",
            "partial+target": "Half + target", "partial+breakeven": "Half, rest at entry",
            "partial+time": f"Half + {h:g}h exit", "partial+stop (gap)": "Half, rest gapped",
            "partial+end of data": "Running"}.get(reason, reason)


RECENT_HOURS = 24 if all(get_profile(lane).base_minutes == 1 for lane in LANES) else 72
DAY_HISTORY_DAYS = 14  # Results by day: today and the 13 days before


def to_pc(ts, offset_h):
    """Data-clock time -> this PC's clock. offset_h: the data clock's hours ahead of UTC (MT5 server time;
    0 for demo prices, which run on UTC). Works on a Timestamp or a Series."""
    local = datetime.now().astimezone().utcoffset()
    return ts - pd.Timedelta(hours=offset_h or 0) + pd.Timedelta(local)


def trading_day_start() -> datetime:
    """When today's trading day began, on this PC's clock: the broker server's midnight (when gold's daily session
    closes and reopens, about 2-3am in Pakistan), or this PC's midnight when the server clock is not known."""
    now = datetime.now()
    off = getattr(get_cached_source("mt5"), "_offset_hours", None) if source_name == "mt5" else None
    if off is None:
        return now.replace(hour=0, minute=0, second=0, microsecond=0)
    local = now.astimezone().utcoffset()
    server_now = pd.Timestamp(now) - pd.Timedelta(local) + pd.Timedelta(hours=off)
    return to_pc(server_now.normalize(), off).to_pydatetime()


def _to_local(ts, source) -> pd.Timestamp:
    """Data-clock time (broker server time for MT5, UTC otherwise) -> this computer's local time."""
    return to_pc(ts, getattr(source, "_offset_hours", None))


def history_bars(snap, source) -> pd.DataFrame:
    """Enough candles for the day-by-day replay. The live scan loads fewer (about 17 days of 5-minute candles,
    3.5 days of 1-minute ones), so the history is fetched separately."""
    base = snap.base_minutes
    days = DAY_HISTORY_DAYS + 9  # MT5 counts candles, not calendar days: ~23 trading days = 15 days + warm-up
    try:
        if hasattr(source, "download_history"):
            return source.download_history(snap.symbol, days=days, minutes=base, save=False)
        if getattr(source, "name", "") == "csv":
            return load_csv_bars(snap.symbol, snap.spec, minutes=base).tail(int(days * 1440 / base)).reset_index(drop=True)
    except Exception:  # fall back to the scan's own candles
        pass
    return snap.bars_1m


def recent_signals(snap, source, lane: str):
    """Replay the last DAY_HISTORY_DAYS + 1 days of closed candles through the same engine and strategy.
    Returns (trades, time of the last closed candle)."""
    bars = history_bars(snap, source)
    if len(bars) < 1500:
        return pd.DataFrame(), None
    clock = snap.now if snap.now is not None else bars["time"].iloc[-1]
    if snap.tick_time is not None and snap.tick_time < clock:
        clock = snap.tick_time
    closed = closed_bars(bars, clock, snap.base_minutes)
    last = closed["time"].iloc[-1]
    start = last - pd.Timedelta(days=DAY_HISTORY_DAYS + 1)
    equity = snap.account_equity or settings.risk.account_equity
    return replay(closed, snap.spec, LANE_CFG[lane], lane, equity, start).trades, last


def recent_rows(data, lane: str) -> list:
    """Every replayed signal of the last two weeks for one strategy, newest first. "Last 3 days" and
    "Results by day" both use these rows, so they always agree."""
    cache = st.session_state.setdefault("history_cache", {})
    rows = []
    base = get_profile(lane).base_minutes
    label = get_profile(lane).short if len(LANES) > 1 else ""
    for sym, snap in data["snaps"].items():
        stamp = (snap.bars_1m["time"].iloc[-1].floor("10min"), settings.risk.risk_per_trade_pct,
                 LANE_CFG[lane].risk.take_half_at_1r)
        key = (source_name, lane, sym)
        if key not in cache or cache[key][0] != stamp:
            try:
                cache[key] = (stamp, *recent_signals(snap, data["source"], lane))
            except Exception:  # never let the history view break the live desk
                cache[key] = (stamp, pd.DataFrame(), None)
        _, trades, last = cache[key]
        for _, r in trades.iterrows():
            when = _to_local(r["signal_time"] + pd.Timedelta(minutes=base), data["source"])
            rows.append({"ts": when, "When": time_text(when), "Day": date_text(when), "date": when.date(),
                         "recent": last is not None and r["signal_time"] >= last - pd.Timedelta(hours=RECENT_HOURS),
                         "Symbol": sym, "Signal": r["side"], "lane": lane, "Lane": label,
                         "Outcome": outcome_text(r["exit_reason"], lane), "R": float(r["r_multiple"]),
                         "pnl": float(r["pnl"]), "entry": float(r["entry"]), "stop": float(r["stop"]),
                         "target": float(r["target"]), "digits": snap.spec.digits})
    return sorted(rows, key=lambda r: r["ts"], reverse=True)


def day_label(d, today) -> str:
    if d == today:
        return "Today"
    if d == today - timedelta(days=1):
        return "Yesterday"
    return pd.Timestamp(d).strftime("%a")


def day_results_panel(rows: list, data) -> None:
    """Pick one day: how many signals it had, how many won and how many lost, and the list. Below, the last
    two weeks one line per day."""
    if source_name == "csv":  # a replay of a saved file: its last day stands in for today
        lasts = [s.bars_1m["time"].iloc[-1] for s in data["snaps"].values() if len(s.bars_1m)]
        today = _to_local(max(lasts), data["source"]).date() if lasts else datetime.now().date()
    else:
        today = datetime.now().date()
    days = [today - timedelta(days=i) for i in range(DAY_HISTORY_DAYS)]
    with st.container(border=True):
        st.markdown('<div class="ptitle">Results by day</div><div class="psub">Every signal the rules gave, replayed on '
                    "the same prices as Last 3 days. Won = closed in profit, lost = closed at a loss; running ones are "
                    "counted once they close. Days are on your PC's clock.</div>", unsafe_allow_html=True)
        c1, c2, _ = st.columns([1.1, 1, 1.6])
        idx = c1.selectbox("Day", list(range(len(days))), key="day_pick",
                           format_func=lambda i: f"{day_label(days[i], today)}, {date_text(pd.Timestamp(days[i]))}")
        markets = ["All markets"] + list(dict.fromkeys([*symbols, *(r["Symbol"] for r in rows)]))
        prev = st.session_state.get("day_market_v")
        mk = c2.selectbox("Market", markets, index=markets.index(prev) if prev in markets else 0)
        st.session_state["day_market_v"] = mk
        mine = [r for r in rows if mk == "All markets" or r["Symbol"] == mk]
        pick = days[idx]
        day_rows = live_rows([r for r in mine if r["date"] == pick])
        body = ui.day_summary(day_rows, CCY)
        if day_rows:
            body += ui.recent_table(day_rows)
        else:
            closed_day = pd.Timestamp(pick).dayofweek >= 5
            body += (f'<p class="psub">No signals on this day'
                     f'{" (the market is closed on weekends)" if closed_day else ""}.</p>')
        items = [{"label": day_label(d, today), "small": date_text(pd.Timestamp(d)),
                  "rows": [r for r in mine if r["date"] == d], "picked": d == pick,
                  "closed_market": pd.Timestamp(d).dayofweek >= 5} for d in days]
        st.markdown(body + '<div class="ptitle" style="margin-top:14px">Last two weeks</div>' + ui.days_table(items),
                    unsafe_allow_html=True)


LATEST_MAX = 8  # Latest signals: how many of today's are listed


def lane_half(lane: str):
    """Where this strategy's first half takes profit (in R), or None when it keeps the whole trade."""
    flag = LANE_CFG[lane].risk.take_half_at_1r if lane in LANE_CFG else None
    return half_level(lane) if (takes_half(lane) if flag is None else flag) else None


def latest_row(s) -> dict:
    """One saved signal (a Journal row) with what happened since: running, target, stop-loss or time limit."""
    lane = s["profile"] if s["profile"] in PROFILES else MAIN
    lp = get_profile(lane)
    sym, side = s["symbol"], s["decision"]
    spec = settings.instruments.get(sym)
    when = pd.Timestamp(s["created_at"])
    row = {"ts": when, "When": time_text(when), "Day": "" if when.date() == datetime.now().date() else date_text(when),
           "Symbol": sym, "Signal": side, "Lane": lp.short if len(LANES) > 1 or lane not in LANES else "",
           "watch": not (spec is not None and spec.tested_profitable and lp.tested),
           "entry": s["entry"], "stop": s["stop"], "target": s["target"], "digits": spec.digits if spec else 2,
           "Outcome": "-", "R": None, "start": None, "base": lp.base_minutes}
    if any(pd.isna(s[k]) for k in ("entry", "stop", "target", "bar_time")) or not s["bar_time"]:
        return row
    if s["source"] and s["source"] != source_name:  # e.g. an MT5 signal while the desk shows demo prices
        names = {"mt5": "MT5", "demo": "demo", "csv": "CSV"}
        row["Outcome"] = f"Not followed: given on {names.get(s['source'], s['source'])} prices"
        return row
    snap = (scans().get(lane) or {}).get("snaps", {}).get(sym)
    if snap is None:  # its strategy is not running now: any scan of the market has the prices
        snap = next((d["snaps"][sym] for d in scans().values() if d and sym in d.get("snaps", {})), None)
    start = pd.Timestamp(s["bar_time"]) + pd.Timedelta(minutes=lp.base_minutes)  # when its signal candle closed
    row["start"] = start
    if snap is None or not len(snap.bars_1m) or snap.bars_1m["time"].iloc[0] > start:
        row["Outcome"] = "Not followed (market not on the desk)" if snap is None else "-"
        return row
    half = lane_half(lane)
    hold = hold_hours(LANE_CFG.get(lane, settings), lane) * 60
    f = follow(snap.bars_1m, side, float(s["entry"]), float(s["stop"]), float(s["target"]), start, hold, half)
    if f.running:
        q = get_quote(sym)
        price = (q.bid if side == "BUY" else q.ask) if q is not None else None
        row["now"] = price
        row["R"] = running_r(side, float(s["entry"]), float(s["stop"]), price, f.half_done, half) if price else None
        row["Outcome"] = "Running, half taken" if f.half_done else "Running"
    else:
        row["Outcome"], row["R"] = outcome_text(f.reason, lane), f.r
    return row


def latest_signals():
    """Every BUY or SELL the desk showed in today's trading day (the Journal's list), newest first, followed live.
    Returns (rows, how many today). With none today: the last one before, and 0."""
    sigs = logger.signals(limit=200)
    sigs = sigs[sigs["decision"].isin(["BUY", "SELL"])]
    if sigs.empty:
        return [], 0
    today = sigs[sigs["created_at"] >= trading_day_start().isoformat(timespec="seconds")]
    pick = today if not today.empty else sigs.head(1)
    return [latest_row(s) for _, s in pick.head(LATEST_MAX).iterrows()], len(today)


def latest_panel(data) -> None:
    rows, n_today = latest_signals()
    if not rows:
        body, sub = "", "No BUY or SELL signals yet. They show here the moment the desk gives one."
    else:
        sub = (f"{n_today} today, newest first: every signal the desk showed (the same list as the Journal), "
               "followed live on the prices. Results in R before costs."
               if n_today else "None yet today. This is the last one before today.")
        if n_today > LATEST_MAX:
            sub += f" Showing the newest {LATEST_MAX}."
        newest, live_until = rows[0], ""
        if newest["start"] is not None and newest["Outcome"].startswith("Running"):
            ends = _to_local(newest["start"] + pd.Timedelta(minutes=newest["base"]), data["source"])
            if pd.Timestamp(datetime.now()) < ends:
                live_until = time_text(ends)
        body = ui.latest_html(rows, live_until)
    with st.container(border=True, height=430 if len(rows) > 3 else None):
        st.markdown(f'<div class="ptitle">Latest signals</div><div class="psub">{ui.e(sub)}</div>{body}',
                    unsafe_allow_html=True)


def live_rows(rows: list) -> list:
    """Still-running signals get the live price, and their R is worked out from it (before costs)."""
    out = []
    for r in rows:
        if r["Outcome"] == "Running":
            q = get_quote(r["Symbol"])
            if q is not None:
                d = 1 if r["Signal"] == "BUY" else -1
                now = q.bid if d == 1 else q.ask
                risk = abs(r["entry"] - r["stop"])
                r = {**r, "now": now, "R": d * (now - r["entry"]) / risk if risk else r["R"]}
        out.append(r)
    return out


class _Collect:
    """Wraps a data source to keep each snapshot for charting."""

    def __init__(self, src):
        self.src, self.snaps, self.name = src, {}, src.name

    def snapshot(self, symbol, base_minutes=1):
        snap = self.src.snapshot(symbol, base_minutes)
        self.snaps[symbol] = snap
        return snap


# ------------------------------------------------------------------ scanning
def scan_key(lane: str) -> tuple:
    return (source_name, lane, tuple(symbols), settings.risk.risk_per_trade_pct, settings.risk.account_equity,
            LANE_CFG[lane].risk.take_half_at_1r)


def scan_due(last, lane: str) -> bool:
    """Scan again once the next candle has closed (3 seconds after it, so MT5 has finished writing it)."""
    if not last or last["key"] != scan_key(lane):
        return True
    period = 60.0 * get_profile(lane).base_minutes
    latest_close = (time.time() - 3.0) // period * period + 3.0
    return last["at"] < latest_close


def sig_key(sig) -> str:
    """One id per signal, without spaces (it is stored in the journal notes). Includes the strategy, so the
    5-minute swing and 3-minute rules never share an id."""
    tail = f"|{sig.profile}" if getattr(sig, "profile", "") else ""
    return f"{sig.symbol}|{sig.bar_time}|{sig.decision}{tail}".replace(" ", "T")


def scans() -> dict:
    """The latest scan of each strategy: {lane: data}."""
    return st.session_state.setdefault("scans", {})


def any_scan():
    """The latest scan of any running strategy (they share prices, the account and the clock)."""
    return next((scans()[lane] for lane in LANES if scans().get(lane)), None)


def run_scan(lane: str) -> dict:
    src = _Collect(get_cached_source(source_name))
    results = scan_symbols(src, LANE_CFG[lane], logger, symbols, profile=lane)
    data = {"key": scan_key(lane), "at": time.time(), "results": results, "snaps": src.snaps, "source": src.src,
            "lane": lane}
    scans()[lane] = data
    return data


# ------------------------------------------------------------------ pop-ups
# One queue of pop-ups, shown one at a time (Streamlit allows one open dialog):
#   signal  - a new BUY/SELL
#   close   - a desk trade reached its tested time limit: time to close it
#   closed  - MT5 closed a desk trade (stop-loss, target or elsewhere)
def push_alert(alert: dict) -> bool:
    shown = st.session_state.setdefault("alerted", set())
    if alert["id"] in shown:
        return False
    shown.add(alert["id"])
    queue = st.session_state.setdefault("alerts", [])
    if alert["kind"] == "signal":  # an older signal for the same market and strategy has expired by now
        queue[:] = [a for a in queue if not (a["kind"] == "signal" and a["symbol"] == alert["symbol"]
                                             and a.get("lane") == alert.get("lane"))]
    queue.append(alert)
    return True


def drop_alert() -> None:
    queue = st.session_state.get("alerts", [])
    if queue:
        queue.pop(0)


def note_new_signals(data) -> None:
    """A new BUY/SELL opens a pop-up, moves the trade box to that market and shows a notification."""
    lane = data["lane"]
    lp = get_profile(lane)
    for sig in data["results"]:
        if not sig.actionable:
            continue
        if (not sig.tested_market or not lp.tested) and not settings.trading.allow_untested_markets:
            shown = st.session_state.setdefault("alerted", set())
            if f"signal|{sig_key(sig)}" not in shown:
                shown.add(f"signal|{sig_key(sig)}")
                why = "untested market" if not sig.tested_market else f"{lp.name}"
                st.toast(f"{sig.decision} {sig.symbol} ({why}, information only)", icon="ℹ️")
            continue
        if push_alert({"id": f"signal|{sig_key(sig)}", "kind": "signal", "key": sig_key(sig),
                       "symbol": sig.symbol, "sig": sig, "lane": lane}):
            st.session_state["box_sym"] = sig.symbol
            st.toast(f"New signal: {sig.decision} {sig.symbol}" + (f" ({lp.short})" if len(LANES) > 1 else ""),
                     icon="🔔")


def note_due_trades(data, positions) -> bool:
    """Pop up once when a desk trade passes its tested time limit. Returns True if a new pop-up was queued."""
    new = False
    for p in positions:
        close_by, overdue = trade_deadline(p, data)
        if overdue and push_alert({"id": f"close|{p['ticket']}", "kind": "close", "ticket": p["ticket"],
                                   "symbol": p["symbol"], "side": p["side"], "close_by": close_by}):
            st.toast(f"Time to close {p['side']} {p['symbol']}", icon="⏰")
            new = True
    return new


def get_scan(lane: str, force: bool = False):
    """(latest scan of this strategy or None, whether it was just made). Rescans after each of its candles
    closes; after an error, tries again every 15 seconds."""
    last = scans().get(lane)
    current = last if last and last["key"] == scan_key(lane) else None
    if not force and not scan_due(last, lane):
        return last, False
    if not force and time.time() - st.session_state.get("scan_error_at", 0) < 15:
        return current, False
    try:
        data = run_scan(lane)
    except MarketDataError as exc:
        st.session_state["scan_error"] = str(exc)
        st.session_state["scan_error_at"] = time.time()
        return current, False
    st.session_state.pop("scan_error", None)
    note_new_signals(data)
    return data, True


def quote_stamp(quote, data) -> tuple:
    """('price at 5:31:07pm', False), or ('last price 27-10-2026 10:52pm', True) when MT5 has gone quiet."""
    if quote is None or quote.time is None:
        return "", False
    pc = _to_local(pd.Timestamp(quote.time), data["source"])
    if (pd.Timestamp(datetime.now()) - pc).total_seconds() > 120:
        return f"last price {when_text(pc)}", True
    return f"price at {time_text(pc, seconds=True)}", False


def newest_price_time(data):
    """When the most recent price arrived, on this PC's clock (None if unknown)."""
    ticks = [s.tick_time for s in data["snaps"].values() if s.tick_time is not None]
    return _to_local(max(ticks), data["source"]) if ticks else None


def feed_stalled(data) -> bool:
    last = newest_price_time(data)
    return source_name == "mt5" and last is not None and \
        (pd.Timestamp(datetime.now()) - last).total_seconds() > 300


def get_quote(symbol):
    try:
        return get_cached_source(source_name).quote(symbol)
    except Exception:
        return None


def feed_pill(results, at: float) -> str:
    stamp = time_text(datetime.fromtimestamp(at), seconds=True)
    if source_name == "demo":
        return ui.pill(f"Demo prices, {stamp}", "warn")
    if source_name == "csv":
        return ui.pill(f"CSV replay, {stamp}", "mute")
    reasons = " ".join(r.reason for r in results)
    if results and all(r.reason.startswith("Data error") for r in results):
        return ui.pill("MT5 not responding", "bad")
    data = any_scan()
    if data is not None and feed_stalled(data):
        return ui.pill(f"MT5 prices stopped at {when_text(newest_price_time(data))}", "bad")
    if any(w in reasons for w in ("Stale", "Bad price", "lost its connection", "not logged in")):
        return ui.pill(f"MT5 prices delayed, {stamp}", "warn")
    return ui.pill(f"MT5 live, {stamp}", "ok")


def get_trader():
    return Trader(get_cached_source("mt5"), settings)


def desk_positions() -> list:
    if source_name != "mt5":
        return []
    try:
        return get_trader().positions()
    except Exception:
        return []


def rerun_desk() -> None:
    """Refresh just this part of the page; fall back to a full refresh outside a fragment."""
    try:
        st.rerun(scope="fragment")
    except StreamlitAPIException:
        st.rerun()


def signal_already_traded(key: str) -> bool:
    """True if an order for this exact signal is already in the journal (survives page reloads and other tabs)."""
    try:
        notes = logger.trades(200)["notes"].fillna("")
        return bool(notes.str.contains(f"signal {key}", regex=False).any())
    except Exception:
        return False


def risk_state_now(data):
    equity = next((s.account_equity for s in data["snaps"].values() if s.account_equity),
                  settings.risk.account_equity)
    return logger.risk_state(equity, emergency=emergency_stop_status())


def local_hm(ts, data) -> str:
    """A data-clock time shown on this PC's clock: 5:20pm today, 10-27-2026 10:57pm on other days."""
    return when_text(_to_local(ts, data["source"])) if ts is not None else "-"


def order_panel(sig, data, state, where: str, lane: str) -> None:
    """Place order -> Confirm / Cancel for a live signal. Sends nothing until Confirm.
    `where` keeps the trade box's and the pop-up's buttons apart; `lane` is the strategy (tagged on the order)."""
    lp = get_profile(lane)
    if source_name != "mt5":
        st.caption("Order buttons work with MetaTrader 5 (live) as the data source.")
        return
    if not trade_on:
        st.caption("To buy or sell from here, switch on Order buttons in the sidebar.")
        return
    spec = settings.instruments.get(sig.symbol)
    if spec is not None and not spec.tested_profitable and not settings.trading.allow_untested_markets:
        st.caption("Watch only: this market lost money in testing, so the desk does not offer orders for it. "
                   "(To allow them anyway, set trading.allow_untested_markets to true in settings.json.)")
        return
    if not lp.tested and not settings.trading.allow_untested_markets:
        st.caption(f"Watch only: the {lp.name.replace(' (watch only)', '')} rules lost money on 2 years of history, "
                   "so the desk does not offer orders with them. Choose Gold 5-minute swing under Strategy.")
        return
    key = sig_key(sig)
    sent = st.session_state.setdefault("sent", {})
    if key in sent:
        st.success(sent[key])
        return
    if signal_already_traded(key):
        st.info("An order for this signal was already sent. See Open trades and the Journal.")
        return
    side, sym = sig.decision.lower(), sig.symbol
    try:
        trader = get_trader()
        chk = trader.check(sig, state, data["at"], tag=lane)
    except Exception as exc:  # never let the order button break the desk
        st.warning(f"Order button unavailable: {exc}")
        return
    if not chk.ok:
        more = f" ({len(chk.blocks) - 1} more reason{'s' if len(chk.blocks) > 2 else ''})" if len(chk.blocks) > 1 else ""
        st.warning(f"Can't place this order: {chk.blocks[0]}{more}")
        return
    pending = st.session_state.get("confirm")
    if pending and pending["key"] != key and pending["key"].startswith(f"{sym}|") and pending.get("lane") == lane:
        st.session_state.pop("confirm", None)  # a new candle closed: the old plan no longer applies
        st.info("A new candle closed and the plan was updated, so the confirmation was reset. "
                "Check the plan and click again.")
        pending = None
    limit = settings.trading.confirm_seconds
    if pending and pending["key"] == key and pending["where"] == where and time.time() - pending["at"] <= limit:
        with st.container(border=True):
            st.markdown(ui.confirm_html(chk, CCY, limit), unsafe_allow_html=True)
            ok_col, no_col, _ = st.columns([1.3, 0.8, 1.6])
            if ok_col.button(f"Confirm {sig.decision}", type="primary", key=f"{side}_ok_{where}_{sym}_{lane}",
                             width="stretch"):
                st.session_state.pop("confirm", None)
                try:
                    fill = trader.place(sig, state, data["at"], tag=lane)
                except Exception as exc:
                    try:
                        logger.log_event(f"Order refused for {sym}: {exc}", "WARNING")
                    except Exception:
                        pass
                    st.error(f"Order not sent. {exc}")
                    return
                legs = fill.legs or [fill]
                if len(legs) > 1:
                    msg = (f"Order filled: {fill.side} {sum(x.volume for x in legs):g} {chk.unit} {sym} in two halves: "
                           + ", ".join(f"{x.volume:g} at {fmt(x.price)} taking profit at {fmt(x.target)}" for x in legs)
                           + f". Stop-loss {fmt(fill.stop)} on both, set in MT5.")
                else:
                    msg = (f"Order filled: {fill.side} {fill.volume:g} {chk.unit} {sym} at {fmt(fill.price)}, MT5 ticket "
                           f"{fill.ticket}. Stop-loss {fmt(fill.stop)} and target {fmt(fill.target)} are set in MT5.")
                msg += f" {fill.note}" if fill.note else ""
                sent[key] = msg  # recorded before anything else can fail
                try:
                    for x in legs:
                        part = f", part {x.part}" if x.part else ""
                        logger.add_trade(sym, x.side, x.volume, x.price, None, None,
                                         notes=f"MT5 ticket {x.ticket} (signal desk, signal {key}{part})")
                    logger.log_event(msg, "WARNING")
                except Exception as exc:  # the next scan adds it to the journal from MT5 instead
                    sent[key] = msg + f" (Journal not updated yet: {exc}. It will be added on the next scan.)"
                st.toast(f"{fill.side} {sym} order filled ({lp.short or lp.name})", icon="✅")
                st.rerun()
            if no_col.button("Cancel", key=f"cancel_{where}_{sym}_{lane}", width="stretch"):
                st.session_state.pop("confirm", None)
                rerun_desk()
        return
    if pending and pending["key"] == key and pending["where"] == where:
        st.info("The confirmation expired, so nothing was sent. Check the price and click again.")
    btn_col, note_col = st.columns([1.3, 2.4], vertical_alignment="center")
    if btn_col.button(f"{sig.decision} {chk.volume:g} {chk.unit}", type="primary",
                      key=f"{side}_place_{where}_{sym}_{lane}", width="stretch"):
        st.session_state["confirm"] = {"key": key, "at": time.time(), "where": where, "lane": lane}
        rerun_desk()
    note_col.caption(f"At market, about {fmt(chk.price)}, with the stop-loss and target above. "
                     "You confirm on the next step.")


def close_button(p: dict, key: str, label: str = "Close trade", after=None) -> None:
    if st.button(label, key=key, width="stretch", type="primary" if after else "secondary"):
        try:
            price, vol = 0.0, 0.0
            for ticket in p.get("tickets", [p["ticket"]]):  # both halves of a split order
                price, v = get_trader().close(ticket)
                vol += v
        except Exception as exc:
            st.error(str(exc))
            return
        try:
            logger.log_event(f"Closed {p['side']} {vol:g} {p['symbol']} (ticket {p['ticket']}) at "
                             f"{fmt(price)} from the signal desk", "WARNING")
        except Exception:
            pass
        for ticket in p.get("tickets", [p["ticket"]]):  # you closed it: no "trade closed" pop-up
            st.session_state.setdefault("alerted", set()).add(f"closed|{ticket}")
        st.toast(f"{p['symbol']} trade closed", icon="✅")
        if after:
            after()
        st.rerun()


def trade_deadline(p: dict, data):
    words = p["comment"].split()
    limit_h = hold_hours(settings, next((k for k in PROFILES if k in words), "fast"))
    deadline = p["opened"] + pd.Timedelta(hours=limit_h)
    server_now = next((s.now for s in data["snaps"].values() if s.now is not None), None)
    return local_hm(deadline, data), server_now is not None and server_now >= deadline


def open_trades_panel(data, positions) -> None:
    """All desk trades open in MT5, with Close buttons. Results of closed trades go into the journal."""
    if not positions and not trade_on:
        return
    with st.container(border=True):
        total = sum(p["profit"] for p in positions)
        sub = (f"{len(positions)} open, {money(total, CCY)} floating." if positions
               else "No trades from this desk are open.")
        st.markdown(f'<div class="ptitle">Open trades</div><div class="psub">{ui.e(sub)}</div>',
                    unsafe_allow_html=True)
        for p in positions:
            row, btn = st.columns([1, 0.36], vertical_alignment="center")
            close_by, overdue = trade_deadline(p, data)
            snap = data["snaps"].get(p["symbol"])
            digits = snap.spec.digits if snap is not None else 5
            tag = get_profile(lane_of(p)).short if len(LANES) > 1 and lane_of(p) else ""
            row.markdown(ui.position_row(p, CCY, local_hm(p["opened"], data), close_by, overdue, digits,
                                         levels_moved(p), tag), unsafe_allow_html=True)
            with btn:
                close_button(p, f"close_{p['ticket']}", "Close")


def levels_moved(p: dict) -> dict:
    """If the stop-loss or target was changed in MT5 after the desk first saw the trade: {level: old value}."""
    first = st.session_state.setdefault("first_levels", {}).setdefault(p["ticket"], (p["stop"], p["target"]))
    out = {}
    if first[0] and abs(first[0] - p["stop"]) > 1e-9:
        out["stop"] = first[0]
    if first[1] and abs(first[1] - p["target"]) > 1e-9:
        out["target"] = first[1]
    return out


def combine_legs(ps: list) -> dict:
    """The two halves of a split order shown as one trade (total size and profit, both take-profits)."""
    if len(ps) == 1:
        return ps[0]
    first = next((p for p in ps if p["comment"].endswith("1/2")), None)
    runner = next((p for p in ps if p["comment"].endswith("2/2")), ps[-1])
    vol = sum(p["volume"] for p in ps)
    sign = 1 if runner["side"] == "BUY" else -1
    # Money at each level, leg by leg: the halves have different targets (and may have different stops).
    units = {"loss_units": (sum(sign * (p["open_price"] - p["stop"]) * p["volume"] for p in ps)
                            if all(p["stop"] for p in ps) else None),
             "gain_units": (sum(abs(p["target"] - p["open_price"]) * p["volume"] for p in ps)
                            if all(p["target"] for p in ps) else None)}
    return {**runner, **units, "volume": vol, "profit": sum(p["profit"] for p in ps),
            "open_price": sum(p["open_price"] * p["volume"] for p in ps) / vol,
            "half_tp": first["target"] if first else None, "tickets": [p["ticket"] for p in ps],
            "opened": min(p["opened"] for p in ps)}


def lane_of(p: dict):
    """Which strategy opened a desk trade, from its MT5 comment ("Signal desk BUY swing 1/2" -> "swing")."""
    words = p.get("comment", "").split()
    return next((k for k in PROFILES if k in words), None)


def owns(p: dict, lane: str) -> bool:
    """Does this strategy's trade box show this desk trade? With one strategy running: every desk trade."""
    if len(LANES) == 1:
        return True
    found = lane_of(p)
    return found == lane or (found not in LANES and lane == LANES[0])


def box_focus(datas: dict, positions) -> str:
    """Which market the trade box shows: the one you picked, else a live signal, an open trade, gold, the first."""
    picked = st.session_state.get("box_sym")
    if picked in symbols:
        return picked
    live = [s.symbol for d in datas.values() if d for s in d["results"] if s.actionable]
    held = [p["symbol"] for p in positions if p["symbol"] in symbols]
    tested = [s for s in symbols if settings.instruments[s].tested_profitable]
    return (live or held or tested or symbols)[0]


def box_content(sym, data, positions, where: str) -> None:
    """The trade box body for one market and one strategy (also used inside the pop-up)."""
    lane = data["lane"]
    lp = get_profile(lane)
    sig = next((s for s in data["results"] if s.symbol == sym), None)
    snap = data["snaps"].get(sym)
    spec = snap.spec if snap is not None else settings.instruments[sym]
    quote = get_quote(sym)
    base = lp.base_minutes
    stamp, stale = quote_stamp(quote, data)
    mine = [p for p in positions if p["symbol"] == sym and owns(p, lane)]
    pos = combine_legs(mine) if mine else None
    if pos is not None and where == "box":
        close_by, overdue = trade_deadline(pos, data)
        vpu = snap.value_per_unit if snap is not None else None
        st.markdown(ui.box_open(pos, quote, spec.digits, CCY, vpu, close_by, overdue, stamp, stale,
                                levels_moved(pos)), unsafe_allow_html=True)
        b, _ = st.columns([1, 2.5])
        with b:
            close_button(pos, f"close_box_{pos['ticket']}")
        return
    if sig is None:
        st.info("No data for this market yet.")
        return
    if sig.actionable:
        until = sig.bar_time + pd.Timedelta(minutes=2 * base) if sig.bar_time is not None else None
        sub = (f"{(sig.setup_type or 'trade').capitalize()} setup, {lp.name} rules. Signalled "
               f"{local_hm(sig.time, data)}; valid until {local_hm(until, data)}, when the next candle closes.")
        hold = hold_hours(LANE_CFG[lane], lane)
        close_by = local_hm(sig.time + pd.Timedelta(hours=hold), data) if sig.time is not None else ""
        st.markdown(ui.box_signal(sig, quote, spec.digits, CCY, sub, close_by, stamp, stale), unsafe_allow_html=True)
        if not sig.tested_market:
            st.caption("This market lost money in testing. Treat this signal as information only.")
        order_panel(sig, data, risk_state_now(data), where, lane)
        return
    nxt = sig.bar_time + pd.Timedelta(minutes=2 * base) if sig.bar_time is not None else None
    sub = f"{lp.name} rules. Next check at {local_hm(nxt, data)}, when the next candle closes."
    last = next((r for r in st.session_state.get("recent_rows", [])
                 if r["Symbol"] == sym and r.get("lane") == lane), None)
    extra = (f'<p>Last signal: {ui.e(last["Signal"])} {ui.e(when_text(last["ts"]))}, {ui.e(last["Outcome"].lower())} '
             f'{last["R"]:+.2f}R.</p>' if last else "")
    st.markdown(ui.box_wait(sig, quote, spec.digits, sub, sig.reason, extra, stamp, stale), unsafe_allow_html=True)


@st.fragment(run_every=3 if auto else None)
def trade_box() -> None:
    """One box: the live price, the signal, its stop-loss and target, and the order button (one part per
    strategy in both mode). Updates every 3 seconds; scans again as soon as each candle closes."""
    datas, fresh = {}, False
    for lane in LANES:
        datas[lane], new = get_scan(lane)
        fresh = fresh or new
    if fresh:
        st.rerun()  # a new candle: refresh the whole desk (and open the pop-up for a new signal)
    data = next((d for d in datas.values() if d), None)
    if data is None:
        st.info("Waiting for prices...")
        return
    positions = desk_positions()
    if note_due_trades(data, positions):
        st.rerun()  # open the "time to close" pop-up
    sym = box_focus(datas, positions)
    with st.container(border=True):
        left_col, right_col = st.columns([3, 1], vertical_alignment="center")
        if len(symbols) > 1:
            st.session_state["box_sym"] = sym
            left_col.segmented_control("Market", symbols, key="box_sym", label_visibility="collapsed")
        else:
            left_col.markdown(f'<div class="ptitle">{ui.e(sym)}</div>', unsafe_allow_html=True)
        label = f"Your PC: {datetime_text(datetime.now())}" + ("" if auto else ", auto refresh off")
        right_col.markdown(f'<div style="text-align:right">{ui.pill(label, "ok" if auto else "warn")}</div>',
                           unsafe_allow_html=True)
        for i, lane in enumerate(LANES):
            if len(LANES) > 1:
                st.markdown(f'<div class="lanehead{" first" if i == 0 else ""}">{ui.e(lane_name(lane))}</div>',
                            unsafe_allow_html=True)
            if datas.get(lane) is None:
                st.info("Waiting for prices...")
                continue
            box_content(st.session_state.get("box_sym") or sym, datas[lane], positions, "box")


def _dismiss_button(label: str = "Close") -> None:
    if st.button(label, key="alert_dismiss"):
        drop_alert()
        st.rerun()


@st.dialog("New signal", width="large", on_dismiss=drop_alert)
def signal_popup(a: dict) -> None:
    lane = a.get("lane", LANES[0])
    data = scans().get(lane)
    if len(LANES) > 1:
        st.caption(f"{lane_name(lane)} rules")
    cur = next((s for s in data["results"] if s.symbol == a["symbol"]), None) if data else None
    if cur is not None and cur.actionable and sig_key(cur) == a["key"]:
        box_content(a["symbol"], data, [], "dlg")
    else:
        sig = a["sig"]
        st.markdown(f"**{sig.decision} {sig.symbol}**: this signal has ended because the next candle closed. "
                    "Its plan no longer applies; wait for the next signal.")
    _dismiss_button()


@st.dialog("Time to close a trade", width="large", on_dismiss=drop_alert)
def close_popup(a: dict) -> None:
    data = any_scan()
    pos = next((p for p in desk_positions() if p["ticket"] == a["ticket"]), None)
    if pos is None or data is None:
        st.markdown(f"**{a['side']} {a['symbol']}** is already closed.")
        _dismiss_button()
        return
    st.markdown(f"Neither the stop-loss nor the target was hit by **{a['close_by']}**. In testing, trades were "
                "closed at this point, so the rules say close it now.")
    snap = data["snaps"].get(pos["symbol"])
    spec = snap.spec if snap is not None else settings.instruments.get(pos["symbol"], settings.instruments["XAUUSD"])
    st.markdown(ui.box_open(pos, get_quote(pos["symbol"]), spec.digits, CCY,
                            snap.value_per_unit if snap is not None else None, a["close_by"], False),
                unsafe_allow_html=True)
    c1, c2, _ = st.columns([1.2, 1, 1.4])
    with c1:
        close_button(pos, f"close_dlg_{pos['ticket']}", "Close trade now", after=drop_alert)
    with c2:
        if st.button("Keep it open", key="alert_keep", width="stretch"):
            drop_alert()
            st.rerun()


@st.dialog("Trade closed", on_dismiss=drop_alert)
def closed_popup(a: dict) -> None:
    pnl = a["pnl"]
    tone = "green" if pnl > 0 else "red" if pnl < 0 else "gray"
    st.markdown(f"**{a['side']} {a['symbol']}** {a['reason']} at {fmt(a['exit_price'])}.")
    st.markdown(f"### :{tone}[{'+' if pnl > 0 else ''}{money(pnl, CCY)}]")
    st.caption("The result is in the Journal and counts toward today's loss limit.")
    _dismiss_button("OK")


@st.dialog("First half took profit", on_dismiss=drop_alert)
def moved_popup(a: dict) -> None:
    st.markdown(f"**{a['side']} {a['symbol']}**: the first half took its profit "
                f"(**+{money(a['first_half_pnl'], CCY)}**).")
    st.markdown(f"The stop-loss on the other half is now at your entry price, **{fmt(a['entry'])}**, so this trade "
                "can no longer lose. The other half stays open for the full target.")
    _dismiss_button("OK")


POPUPS = {"signal": signal_popup, "close": close_popup, "closed": closed_popup, "moved": moved_popup}


@st.fragment(run_every=5 if auto else None)
def side_panel() -> None:
    """Latest signals, open trades (read from MT5) and recent signals. Refreshes every 5 seconds, so a new
    signal, a stop-loss or target you change in MT5, the live price and the running result show straight away."""
    data = any_scan()
    if data is None:
        return
    try:
        latest_panel(data)
    except Exception as exc:  # never let this list break the live desk
        st.caption(f"Could not show the latest signals: {exc}")
    if source_name == "mt5":
        try:
            new = False
            for mv in get_trader().manage_partials(logger):  # queue every pop-up first, then refresh once
                logger.log_event(f"First half of {mv['side']} {mv['symbol']} took profit; the other half's stop-loss "
                                 f"was moved to the entry price {fmt(mv['entry'])}.", "WARNING")
                new = push_alert({"id": f"moved|{mv['ticket']}", "kind": "moved", **mv}) or new
            if new:
                st.rerun()
        except Exception as exc:
            st.caption(f"Could not move the stop-loss after the first half: {exc}")
        open_trades_panel(data, desk_positions())
    rows = live_rows([r for r in st.session_state.get("recent_rows", []) if r.get("recent")])
    with st.container(border=True, height=470):
        span = "24 hours" if RECENT_HOURS == 24 else f"{RECENT_HOURS // 24} days"
        if rows:
            running = sum(r["Outcome"] == "Running" for r in rows)
            won = sum(r["R"] > 0 and r["Outcome"] != "Running" for r in rows)
            summary = (f"{len(rows)} signal{'s' if len(rows) != 1 else ''}: {won} won, "
                       f"{len(rows) - won - running} lost, {running} running.")
            body = ui.recent_table(rows)
        else:
            summary, body = f"No signals in the last {span} for these markets.", ""
        st.markdown(f'<div class="ptitle">Last {span}</div><div class="psub">{ui.e(summary)} '
                    "Replayed with the same rules on the same prices, one trade at a time per market and strategy "
                    "as in testing, so a signal that came while an earlier one was still running is only in Latest "
                    f'signals. Running ones update live.</div>{body}', unsafe_allow_html=True)


# ------------------------------------------------------------------ desk
with tab_desk:
    if not symbols:
        st.info("Choose at least one market in the sidebar.")
    else:
        head, btn = st.columns([1, 0.16], vertical_alignment="center")
        force = btn.button("Scan now", type="primary", width="stretch")
        datas = {lane: get_scan(lane, force)[0] for lane in LANES}
        data = next((d for d in datas.values() if d), None)
        if data is None:
            head.markdown(ui.strip(ui.pill("Not connected", "bad"), []), unsafe_allow_html=True)
            if st.session_state.get("scan_error"):
                st.error(st.session_state["scan_error"])
            if source_name == "mt5":
                st.info("You can keep exploring with Demo (synthetic prices) in the sidebar while you fix MT5. "
                        "The desk retries by itself every 15 seconds.")
            trade_box()
        else:
            results = [s for d in datas.values() if d for s in d["results"]]
            snaps = data["snaps"]
            equity = next((s.account_equity for s in snaps.values() if s.account_equity),
                          settings.risk.account_equity)
            acct_pill = ""
            if source_name == "mt5":
                try:  # record results of desk trades that MT5 closed, so the daily limits see them
                    for c in get_trader().sync_journal(logger):
                        if c.get("part") == "1/2" and c["pnl"] > 0:
                            st.toast(f"First half of {c['side']} {c['symbol']} took profit: {money(c['pnl'], CCY)}",
                                     icon="✅")
                            continue
                        push_alert({"id": f"closed|{c['ticket']}", "kind": "closed", **c})
                    mode = get_cached_source("mt5").account()["mode"]
                    acct_pill = ui.pill(f"{mode.capitalize()} account", "mute" if mode == "demo" else "bad")
                except Exception as exc:
                    st.caption(f"Could not update the journal from MT5: {exc}")
            today = logger.risk_state(equity, emergency=emergency_stop_status())
            pnl = today.realized_pnl_today
            stopped_now, _ = emergency_stop_status()
            head.markdown(ui.strip(
                feed_pill(results, data["at"]),
                [ui.kv("Equity", money(equity, CCY)),
                 ui.kv("Today", money(pnl, CCY), "pos" if pnl > 0 else "neg" if pnl < 0 else ""),
                 ui.kv("Trades today", str(today.trades_today), optional=True)],
                acct_pill + (ui.pill("Emergency stop on", "bad") if stopped_now else "")), unsafe_allow_html=True)

            if feed_stalled(data):
                last = newest_price_time(data)
                weekend = datetime.now().weekday() >= 5
                st.error(f"MetaTrader 5 has not sent a new price since {datetime_text(last)} (your PC's time). "
                         + ("Markets are closed for the weekend." if weekend else
                            "The market is open now, so MT5 has probably lost its connection to the broker. "
                            "Look at the bottom-right corner of MT5: if it says No connection, use "
                            "File > Login to Trade Account and log in again. The desk updates by itself once "
                            "prices come back."))
            rows = sorted((r for lane, d in datas.items() if d for r in recent_rows(d, lane)),
                          key=lambda r: r["ts"], reverse=True)
            st.session_state["recent_rows"] = rows
            trade_box()

            left, right = st.columns([1.75, 1], gap="medium")
            with left:
                with st.container(border=True):
                    html = '<div class="ptitle">Markets</div>'
                    for lane, d in datas.items():
                        lp = get_profile(lane)
                        sub = (f"{lp.name} rules. A signal needs all five timeframes and every risk rule to agree, "
                               f"which happens {lp.how_often or 'now and then'} per market.")
                        mrows = "".join(ui.market_row(s, settings.instruments.get(s.symbol))
                                        for s in (d["results"] if d else []))
                        if len(LANES) > 1:
                            html += f'<div class="lanehead">{ui.e(lp.name)}</div>'
                        html += f'<div class="psub">{ui.e(sub)}</div>{mrows}'
                    st.markdown(html, unsafe_allow_html=True)
            with right:
                side_panel()
            day_results_panel(rows, data)

            with st.container(border=True):
                if len(LANES) > 1:
                    lane_col, pick_col, info_col = st.columns([1, 1, 2.2], vertical_alignment="bottom")
                    in_lane = lane_col.selectbox("Strategy", [l for l in LANES if datas.get(l)], format_func=lane_name,
                                                 key="inspect_lane")
                else:
                    pick_col, info_col = st.columns([1, 3], vertical_alignment="bottom")
                    in_lane = LANES[0]
                lane_results = datas[in_lane]["results"]
                names = [s.symbol for s in lane_results]
                default = next((i for i, s in enumerate(lane_results) if s.actionable), 0)
                pick = pick_col.selectbox("Inspect a market", names, index=default)
                sig = lane_results[names.index(pick)]
                snap = datas[in_lane]["snaps"].get(pick)
                info_col.markdown(f'<div class="psub">{ui.e(sig.explanation)}</div>', unsafe_allow_html=True)
                chart_col, check_col = st.columns([1.65, 1], gap="medium")
                if snap is not None and len(snap.bars_1m):
                    chart_col.plotly_chart(candle_chart(sig, snap, in_lane), width="stretch",
                                           config={"displaylogo": False})
                else:
                    chart_col.info("No price data for this market right now.")
                extra = "" if sig.actionable or not sig.reasons else ui.reasons_list(sig.reasons)
                check_col.markdown('<div class="ptitle">Five-timeframe checks</div>' + ui.checks_list(sig) + extra,
                                   unsafe_allow_html=True)
                for n in sig.notes:
                    check_col.caption(n)

        queue = st.session_state.get("alerts", [])
        if queue:
            POPUPS[queue[0]["kind"]](queue[0])


# ------------------------------------------------------------------ calculator
with tab_calc:
    st.subheader("Profit/loss scenario")
    st.caption("Fixed risk and reward, independent outcomes. A mathematical scenario - not a forecast.")
    a, b, c = st.columns(3)
    eq = a.number_input("Account equity", min_value=1.0, value=float(settings.risk.account_equity), step=100.0, key="sc_eq")
    rp = b.slider("Risk per trade (%)", 0.1, 1.0, float(min(max(settings.risk.risk_per_trade_pct, 0.1), 1.0)), 0.05)
    rr = c.slider("Reward-to-risk", 1.0, 4.0, 2.0, 0.1)
    wr = a.slider("Assumed winning trades (%)", 10, 90, 40, 1)
    nt = b.slider("Number of trades", 10, 500, 100, 10)
    cost = c.number_input("Costs per trade", min_value=0.0, value=0.0, step=0.1)
    res = scenario(eq, rp, rr, wr / 100.0, nt, cost)
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Risk per trade", money(res["risk_per_trade"], CCY))
    m2.metric("Target profit per win", money(res["profit_per_win"], CCY))
    m3.metric("Break-even win rate", f"{100 * res['break_even_win_rate']:.1f}%")
    m4.metric(f"Expected P&L over {nt} trades", money(res["expected_total"], CCY))
    st.caption("Break-even before costs = 1 / (1 + R:R). Costs and execution push the required win rate higher.")

    st.divider()
    st.subheader("Position size calculator")
    a, b, c = st.columns(3)
    sym = a.selectbox("Instrument", all_symbols)
    spec = settings.instruments[sym]
    side = b.radio("Direction", ["BUY", "SELL"], horizontal=True)
    entry = c.number_input("Entry price", value=float(spec.demo_start_price), format="%.5f")
    stop = a.number_input("Stop-loss price", value=float(spec.demo_start_price * (0.998 if side == "BUY" else 1.002)),
                          format="%.5f")
    rr2 = b.number_input("Target (reward-to-risk)", min_value=0.5, value=2.0, step=0.1)
    spread = c.number_input("Spread (price)", min_value=0.0, value=float(spec.typical_spread), format="%.5f")
    try:
        vpu = value_per_price_unit(spec, entry, CCY, settings.risk.fx_rates_to_account)
        sz = size_position(eq, rp, entry, stop, spec, vpu, settings.risk.max_leverage)
        if (side == "BUY" and stop >= entry) or (side == "SELL" and stop <= entry):
            st.warning("Stop must be below entry for BUY and above entry for SELL.")
        elif not sz.ok:
            st.error(sz.reject_reason)
        else:
            dist = abs(entry - stop)
            target = entry + rr2 * dist if side == "BUY" else entry - rr2 * dist
            plan = build_trade_plan(side, entry, stop, target, sz.quantity, spec, vpu, spread, eq, sz.risk_budget, sz.notes)
            m1, m2, m3, m4 = st.columns(4)
            m1.metric("Quantity", f"{plan.quantity:g} {plan.unit}")
            m2.metric("Planned loss", money(-plan.planned_loss, CCY))
            m3.metric("Planned profit", money(plan.planned_profit, CCY))
            m4.metric("Net reward:risk", f"{plan.reward_risk_net:.2f}")
            st.caption(f"Target {fmt(target)} | Risk budget {money(sz.risk_budget, CCY)} | loss per {spec.unit[:-1]} at stop "
                       f"{money(sz.loss_per_unit, CCY)} | Uses default contract specs - with MT5 the broker's are used.")
    except ValueError as exc:
        st.error(str(exc))

# ------------------------------------------------------------------ backtest
with tab_bt:
    st.subheader("Backtest")
    if len(LANES) > 1:
        bt_key = st.selectbox("Strategy to test", LANES, format_func=lane_name, key="bt_lane",
                              help="Both mode runs these two strategies; test one at a time here.")
    else:
        bt_key = LANES[0]
    bt_prof = get_profile(bt_key)
    st.caption(f"Strategy: {bt_prof.name} (change it in the sidebar). Same engine as live. Fills at the next candle "
               "open with spread, commission and slippage. Past results do not guarantee future results.")
    a, b, c = st.columns(3)
    bt_sym = a.selectbox("Symbol", all_symbols, key="bt_sym")
    csv_files = sorted(p.name for p in CSV_DIR.glob("*.csv*")) if CSV_DIR.exists() else []
    data_choice = b.selectbox("Data", ["Demo (synthetic)", "CSV file (data/csv)"])
    days = c.slider("Days (demo / MT5 download)", 5, 120, 30)
    last_scan = any_scan() or {}
    live_eq = next((s.account_equity for s in last_scan.get("snaps", {}).values() if s.account_equity), None)
    bt_eq = a.number_input("Starting equity", min_value=1.0, value=float(live_eq or settings.risk.account_equity),
                           step=100.0, key="bt_eq", help="Defaults to your MT5 account equity once the desk has scanned.")
    do_oos = b.checkbox("In-sample / out-of-sample split (60/40)", value=True)
    wf = c.number_input("Walk-forward windows", min_value=0, max_value=12, value=4)
    if source_name == "mt5" and st.button("Download history from MT5 to data/csv"):
        try:
            src = get_cached_source("mt5")
            got = src.download_history(bt_sym, days) if isinstance(src, MT5Source) else None
            st.success(f"Saved {len(got)} candles to data/csv/{bt_sym}.csv")
        except MarketDataError as exc:
            st.error(str(exc))
    if csv_files:
        st.caption("CSV files: " + ", ".join(csv_files) + (
            f". {bt_prof.name} uses the _M5 files." if bt_prof.base_minutes == 5 else
            f". {bt_prof.name} builds {bt_prof.base_minutes}-minute candles from the 1-minute files."
            if bt_prof.base_minutes > 1 else ""))
    if st.button("Run backtest", type="primary"):
        spec = settings.instruments[bt_sym]
        try:
            if data_choice.startswith("Demo"):
                end = pd.Timestamp.now().floor("min")
                bars = generate_demo_bars(spec, end - pd.Timedelta(days=days), end, skip_weekends=True)
                if bt_prof.base_minutes > 1:
                    from candles import resample_bars
                    bars = resample_bars(bars, bt_prof.base_minutes)
            else:
                bars = load_csv_bars(bt_sym, spec, minutes=bt_prof.base_minutes)
        except (MarketDataError, ValueError) as exc:
            st.error(str(exc))
            st.stop()
        # Demo prices run on UTC; MT5 history is on the MT5 server clock. Show both on this PC's clock.
        bt_offset = 0.0
        if not data_choice.startswith("Demo"):
            bt_offset = getattr(get_cached_source("mt5"), "_offset_hours", None) if source_name == "mt5" else None
            bt_offset = bt_offset if bt_offset is not None else settings.data.mt5_server_utc_offset_hours
        on_pc = bt_offset is not None
        clock_note = "" if on_pc else (" Times are MT5 server time (connect MT5 as the data source to show "
                                       "them on your PC's clock).")
        st.caption(f"{len(bars):,} candles of {bt_prof.base_minutes} minute(s), "
                   f"{date_text(bars['time'].iloc[0])} to {date_text(bars['time'].iloc[-1])}.{clock_note}")
        budget = bt_eq * settings.risk.risk_per_trade_pct / 100
        if bt_prof.base_minutes > 1 and budget < 50:
            st.warning(f"At {settings.risk.risk_per_trade_pct}% risk, {money(bt_eq, CCY)} allows {money(budget, CCY)} "
                       "per trade. The swing rules' stops are wide, so even 0.01 lot of gold often risks more than "
                       "that and the signal is skipped. Use a starting equity of 20,000 or more to see full results.")
        bar = st.progress(0.0, text="Running...")
        result = replay(bars, spec, LANE_CFG[bt_key], bt_key, bt_eq,
                        progress=lambda f: bar.progress(min(f, 1.0), text="Running..."))
        bar.empty()
        m = result.metrics
        if result.data_report and not result.data_report.ok:
            st.warning("Data issues: " + result.data_report.summary())
        k = st.columns(6)
        k[0].metric("Trades", m["trades"])
        k[1].metric("Win rate", f"{m['win_rate_pct']:.1f}%" if m["win_rate_pct"] is not None else "-")
        k[2].metric("Net P&L", money(m["net_pnl"], CCY))
        k[3].metric("Expectancy / trade", money(m["expectancy"], CCY) if m["expectancy"] is not None else "-")
        k[4].metric("Profit factor", f"{m['profit_factor']:.2f}" if m["profit_factor"] else "-")
        k[5].metric("Max drawdown", f"{m['max_drawdown_pct']:.2f}%")
        if not result.equity_curve.empty:
            ec = result.equity_curve
            x = to_pc(ec["time"], bt_offset) if on_pc else ec["time"]
            fig = go.Figure(go.Scatter(x=x, y=ec["equity"], mode="lines", name="Equity",
                                       line=dict(shape="hv")))
            fig.update_layout(height=320, margin=dict(l=10, r=10, t=30, b=10),
                              title="Equity after each closed trade", yaxis_title=CCY,
                              xaxis=dict(tickformat="%d-%m-%Y", hoverformat="%d-%m-%Y %I:%M%p"))
            st.plotly_chart(fig, width="stretch")
        if not result.trades.empty:
            c1, c2, c3 = st.columns(3)
            c1.dataframe(results_by(result.trades, "setup"), hide_index=True)
            c2.dataframe(results_by(result.trades, "side"), hide_index=True)
            c3.dataframe(results_by(result.trades, "exit_reason"), hide_index=True)
            shown = result.trades.copy()
            tcols = ["signal_time", "entry_time", "exit_time"]
            if on_pc:
                for col in tcols:
                    shown[col] = to_pc(shown[col], bt_offset)
            st.dataframe(format_columns(shown, tcols), hide_index=True, width="stretch")
            st.download_button("Download trades CSV", result.trades.to_csv(index=False), f"backtest_{bt_sym}.csv")
        ins, oos, wf_df = split_report(result.trades, bars["time"].iloc[0], bars["time"].iloc[-1], bt_eq,
                                       0.6, int(wf) or 1)
        if do_oos:
            st.markdown("**In-sample vs out-of-sample** (the rules were chosen on the first 60%; judge them on the last 40%)")
            st.dataframe(pd.DataFrame([{"period": "in-sample (first 60%)", **ins},
                                       {"period": "out-of-sample (last 40%)", **oos}]), hide_index=True)
        if wf:
            st.markdown("**Walk-forward windows** (fixed rules, successive periods)")
            st.dataframe(wf_df.assign(**{c: wf_df[c].map(date_text) for c in ("from", "to") if c in wf_df}),
                         hide_index=True)
        if data_choice.startswith("Demo"):
            st.info("Demo data is random. Results on it say nothing about real markets - use MT5 history.")

# ------------------------------------------------------------------ journal
with tab_journal:
    st.subheader("Today")
    st_today = logger.risk_state(settings.risk.account_equity, emergency=emergency_stop_status())
    limit = st_today.equity * settings.risk.daily_loss_limit_pct / 100.0
    j = st.columns(4)
    j[0].metric("Realised P&L today", money(st_today.realized_pnl_today, CCY))
    j[1].metric("Loss allowance left", money(max(0.0, limit + st_today.realized_pnl_today), CCY))
    j[2].metric("Trades today", f"{st_today.trades_today} / {settings.risk.max_trades_per_day}")
    j[3].metric("Losses in a row", f"{st_today.consecutive_losses} / {settings.risk.max_consecutive_losses}")
    st.caption("Daily limits use the trades recorded here. Orders placed from the desk are recorded automatically, "
               "with their result once MT5 closes them. Record only trades you place in MT5 yourself.")

    with st.form("add_trade", clear_on_submit=True):
        st.markdown("**Record a trade you took**")
        f = st.columns(4)
        t_sym = f[0].selectbox("Symbol", all_symbols)
        t_side = f[1].selectbox("Side", ["BUY", "SELL"])
        t_qty = f[2].number_input("Quantity", min_value=0.0, value=0.01, format="%.4f")
        t_sig = f[3].number_input("Signal # (optional)", min_value=0, value=0)
        g = st.columns(4)
        t_entry = g[0].number_input("Entry", min_value=0.0, value=0.0, format="%.5f")
        t_exit = g[1].number_input("Exit (0 if still open)", min_value=0.0, value=0.0, format="%.5f")
        t_pnl = g[2].number_input(f"Net P&L in {CCY} (closed trades)", value=0.0, step=0.01)
        t_closed = g[3].checkbox("Trade is closed", value=True)
        t_notes = st.text_input("Notes")
        if st.form_submit_button("Save trade"):
            logger.add_trade(t_sym, t_side, t_qty, t_entry or None, (t_exit or None) if t_closed else None,
                             t_pnl if t_closed else None, signal_id=t_sig or None, notes=t_notes)
            st.success("Saved")
            st.rerun()
    day0 = trading_day_start()
    since = day0.isoformat(timespec="seconds")
    show_all = st.toggle("Show older days", value=False, key="journal_all",
                         help="Off: only today's trading day. Older days are kept (the loss limits and Reproduce "
                              "need them), just not shown.")
    st.caption(f"Today's trading day began {datetime_text(day0)} on your PC, when the market's day closes and "
               "reopens. The lists below start empty again after the next daily close."
               + (" Showing every day." if show_all else ""))
    trades = logger.trades()
    if not show_all and not trades.empty:
        still_open = trades["pnl"].isna() & trades["closed_at"].isna()
        trades = trades[(trades["opened_at"] >= since) | (trades["closed_at"].fillna("") >= since) | still_open]
    if trades.empty:
        st.caption("No trades recorded today.")
    else:
        st.dataframe(format_columns(trades, ["opened_at", "closed_at"]), hide_index=True, width="stretch")

    st.subheader("Signal history: today" if not show_all else "Signal history: all days")
    sigs = logger.signals()
    shown = sigs if show_all or sigs.empty else sigs[sigs["created_at"] >= since]
    if shown.empty:
        last = datetime_text(pd.Timestamp(sigs["created_at"].iloc[0])) if not sigs.empty else "none yet"
        st.info(f"No BUY or SELL signals yet in today's trading day. The last one was {last}. "
                "For won and lost signals by day, see Results by day on the Desk.")
    else:
        n_buy, n_sell = int((shown["decision"] == "BUY").sum()), int((shown["decision"] == "SELL").sum())
        st.caption(f"{len(shown)} signal{'s' if len(shown) != 1 else ''} ({n_buy} BUY, {n_sell} SELL), newest first, "
                   "on your PC's clock. For won and lost, see Results by day on the Desk.")
        shown_sigs = format_columns(shown, ["created_at"]).drop(columns=["data_time", "bar_time"], errors="ignore")
        if "profile" in shown_sigs:
            shown_sigs["profile"] = shown_sigs["profile"].map(
                lambda k: get_profile(k).short if k in PROFILES else "").rename("strategy")
            shown_sigs = shown_sigs.rename(columns={"profile": "strategy"})
        st.dataframe(shown_sigs.rename(columns={"created_at": "time (your PC)"}), hide_index=True, width="stretch")
    if not sigs.empty:
        rid = st.number_input("Reproduce signal #", min_value=1, value=int(sigs["id"].iloc[0]))
        if st.button("Reproduce from saved data"):
            try:
                row, new, same = logger.reproduce_signal(int(rid))
                (st.success if same else st.error)(
                    f"Saved: {row['decision']} entry {fmt(row['entry'])} | Re-run: {new.decision} "
                    f"{'entry ' + fmt(new.plan.entry) if new.plan else ''} -> {'MATCH' if same else 'DIFFERENT'}")
            except Exception as exc:
                st.error(str(exc))

# ------------------------------------------------------------------ system
with tab_sys:
    st.subheader("Research history download")
    st.caption("Saves up to 1.5 years of 1-minute and 2 years of 5-minute MT5 prices for every market the bot "
               "knows into data/csv, for strategy testing. Needs MT5 open and logged in. Takes 5-10 minutes.")
    if st.button("Download history for research"):
        if source_name != "mt5":
            st.warning("Choose MetaTrader 5 (live) as the data source first.")
        else:
            from download_history import download_all
            lines = []
            box = st.empty()

            def show(text):
                lines.append(text)
                box.code("\n".join(lines))
            try:
                with st.spinner("Downloading from MT5..."):
                    n = download_all(get_cached_source("mt5"), list(settings.instruments), say=show)
                (st.success if n else st.error)(f"{n} files saved." if n else "Nothing was saved - see the messages above.")
            except MarketDataError as exc:
                st.error(str(exc))
    st.divider()
    st.subheader("Safety status")
    stopped, why = emergency_stop_status()
    st.write(f"- Automatic orders (bot trading by itself): **{'ENABLED' if LIVE_TRADING_ENABLED else 'DISABLED (not implemented)'}**")
    st.write(f"- Order buttons this session: **{'on' if trade_on else 'off'}** | real-money accounts: "
             f"**{'allowed' if settings.trading.allow_real_account else 'blocked (demo only)'}** | max open trades "
             f"{settings.trading.max_open_positions} | refuse if price moved over "
             f"{settings.trading.max_price_move_fraction:.0%} of the stop distance")
    st.write(f"- Strategy: **{mode_name(profile_key)}** ({mode_note(profile_key)})")
    st.write(f"- Emergency stop: **{'ACTIVE - ' + why if stopped else 'off'}**")
    st.write(f"- Risk per trade {settings.risk.risk_per_trade_pct}% | daily loss limit {settings.risk.daily_loss_limit_pct}% "
             f"| max {settings.risk.max_trades_per_day} trades/day | stop after {settings.risk.max_consecutive_losses} losses in a row")
    st.write(f"- Minimum net reward:risk {settings.risk.min_net_reward_risk} | target {settings.risk.target_reward_risk}R "
             f"| stale data after {settings.risk.stale_bar_seconds}s | max spread {int(100 * settings.risk.max_spread_to_stop_ratio)}% of stop")
    if source_name == "mt5":
        try:
            src = get_cached_source("mt5")
            st.json(src.account())
        except MarketDataError as exc:
            st.error(str(exc))
    st.subheader("Settings in use")
    shown = settings.to_dict()
    shown["alerts"]["telegram_bot_token"] = "***" if shown["alerts"]["telegram_bot_token"] else ""
    shown["data"]["mt5_password"] = "***" if shown["data"]["mt5_password"] else ""
    st.code(json.dumps(shown, indent=2, default=str), language="json")
    st.subheader("Event log")
    st.dataframe(format_columns(logger.events(), ["created_at"], seconds=True).rename(
        columns={"created_at": "time (your PC)"}), hide_index=True, width="stretch")
