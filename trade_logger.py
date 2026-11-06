"""
SQLite record of signals, your trade journal, and system events.

- Every BUY/SELL signal is stored with the exact candles and inputs used, so it
  can be reproduced later (see reproduce_signal).
- The journal holds trades YOU took. Today's results feed the daily loss limit,
  max-trades and consecutive-loss checks - so record trades for those limits to work.
- State lives on disk, so a restart picks up where it left off.
"""
from __future__ import annotations

import json
import re
import sqlite3
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional, Tuple

import pandas as pd

from config import DB_PATH, SNAPSHOT_DIR, InstrumentSpec, Settings, settings_from_dict
from risk_manager import RiskState

SCHEMA = """
CREATE TABLE IF NOT EXISTS signals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    symbol TEXT NOT NULL,
    decision TEXT NOT NULL,
    setup_type TEXT,
    data_time TEXT,
    bar_time TEXT,
    entry REAL, stop REAL, target REAL,
    quantity REAL, unit TEXT,
    planned_loss REAL, planned_profit REAL, reward_risk_net REAL,
    explanation TEXT,
    checks_json TEXT,
    source TEXT,
    snapshot_path TEXT,
    inputs_json TEXT
);
CREATE TABLE IF NOT EXISTS trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    opened_at TEXT NOT NULL,
    closed_at TEXT,
    symbol TEXT NOT NULL,
    side TEXT NOT NULL,
    quantity REAL NOT NULL,
    entry REAL,
    exit REAL,
    pnl REAL,
    signal_id INTEGER,
    notes TEXT
);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    level TEXT NOT NULL,
    message TEXT NOT NULL
);
"""


def _iso(ts) -> Optional[str]:
    if ts is None:
        return None
    return pd.Timestamp(ts).isoformat()


class TradeLogger:
    def __init__(self, db_path: Path = DB_PATH, snapshot_dir: Path = SNAPSHOT_DIR):
        self.db_path = Path(db_path)
        self.snapshot_dir = Path(snapshot_dir)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.snapshot_dir.mkdir(parents=True, exist_ok=True)
        with self._conn() as c:
            c.executescript(SCHEMA)
            cols = {r[1] for r in c.execute("PRAGMA table_info(signals)")}
            if "profile" not in cols:  # databases made before strategies were recorded
                c.execute("ALTER TABLE signals ADD COLUMN profile TEXT DEFAULT ''")

    def _conn(self):
        conn = sqlite3.connect(self.db_path, timeout=10)
        conn.row_factory = sqlite3.Row
        return conn

    # ------------------------------------------------------------------ events
    def log_event(self, message: str, level: str = "INFO") -> None:
        with self._conn() as c:
            c.execute("INSERT INTO events (created_at, level, message) VALUES (?,?,?)",
                      (datetime.now().isoformat(timespec="seconds"), level, message))

    def events(self, limit: int = 100) -> pd.DataFrame:
        with self._conn() as c:
            return pd.read_sql_query("SELECT * FROM events ORDER BY id DESC LIMIT ?", c, params=(limit,))

    # ------------------------------------------------------------------ signals
    def log_signal(self, sig, bars_1m: pd.DataFrame, spec: InstrumentSpec, value_per_unit: float,
                   state: RiskState, settings: Settings, tick_time=None, now=None, profile: str = "fast") -> int:
        """Save the engine's exact inputs. For the swing profile these are on the engine clock (profiles.py),
        so `now` must be the engine-clock time; it defaults to the signal time."""
        created = datetime.now()
        snap_name = f"{sig.symbol}_{created:%Y%m%d_%H%M%S_%f}.csv.gz"
        snap_path = self.snapshot_dir / snap_name
        bars_1m.to_csv(snap_path, index=False, compression="gzip")
        inputs = {
            "symbol": sig.symbol, "now": _iso(now if now is not None else sig.time), "tick_time": _iso(tick_time),
            "profile": profile,
            "bid": sig.bid, "ask": sig.ask, "value_per_unit": value_per_unit,
            "spec": spec.to_dict(), "state": state.__dict__, "settings": settings.to_dict(),
            "source": sig.source,
        }
        inputs["settings"]["alerts"]["telegram_bot_token"] = ""
        inputs["settings"]["data"]["mt5_password"] = ""
        p = sig.plan
        with self._conn() as c:
            cur = c.execute(
                """INSERT INTO signals (created_at, symbol, decision, setup_type, data_time, bar_time, entry, stop,
                   target, quantity, unit, planned_loss, planned_profit, reward_risk_net, explanation, checks_json,
                   source, snapshot_path, inputs_json, profile) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (created.isoformat(timespec="seconds"), sig.symbol, sig.decision, sig.setup_type, _iso(sig.time),
                 _iso(sig.bar_time), p.entry if p else None, p.stop if p else None, p.target if p else None,
                 p.quantity if p else None, p.unit if p else None, p.planned_loss if p else None,
                 p.planned_profit if p else None, p.reward_risk_net if p else None, sig.explanation,
                 json.dumps([ch.to_dict() for ch in sig.checks]), sig.source, snap_name, json.dumps(inputs), profile),
            )
            return int(cur.lastrowid)

    def recent_signal_exists(self, symbol: str, decision: str, minutes: int, profile: Optional[str] = None) -> bool:
        """Was the same signal saved in the last `minutes`? With `profile`, only that strategy's signals count,
        so the 5-minute swing and 3-minute rules each get their own signal."""
        since = (datetime.now() - timedelta(minutes=minutes)).isoformat(timespec="seconds")
        sql, args = "SELECT 1 FROM signals WHERE symbol=? AND decision=? AND created_at>=?", [symbol, decision, since]
        if profile:
            sql += " AND profile=?"
            args.append(profile)
        with self._conn() as c:
            row = c.execute(sql + " LIMIT 1", args).fetchone()
        return row is not None

    def signals(self, limit: int = 200) -> pd.DataFrame:
        with self._conn() as c:
            return pd.read_sql_query(
                """SELECT id, created_at, symbol, decision, profile, setup_type, data_time, bar_time, entry, stop, target,
                   quantity, unit, planned_loss, planned_profit, reward_risk_net, source
                   FROM signals ORDER BY id DESC LIMIT ?""",
                c, params=(limit,))

    def reproduce_signal(self, signal_id: int) -> Tuple[dict, object, bool]:
        """Re-run the engine on the saved candles and inputs. Returns (saved_row, new_signal, matches)."""
        from engine import evaluate_bars  # local import avoids a circular import

        with self._conn() as c:
            row = c.execute("SELECT * FROM signals WHERE id=?", (signal_id,)).fetchone()
        if row is None:
            raise ValueError(f"Signal {signal_id} not found")
        row = dict(row)
        inputs = json.loads(row["inputs_json"])
        bars = pd.read_csv(self.snapshot_dir / row["snapshot_path"], compression="gzip", parse_dates=["time"])
        settings = settings_from_dict(inputs["settings"])
        spec = InstrumentSpec(**inputs["spec"])
        state = RiskState(**inputs["state"])
        now = pd.Timestamp(inputs["now"]) if inputs["now"] else None
        tick = pd.Timestamp(inputs["tick_time"]) if inputs["tick_time"] else None
        sig = evaluate_bars(inputs["symbol"], bars, now, inputs["bid"], inputs["ask"], spec,
                            inputs["value_per_unit"], state, settings, tick, inputs.get("source", ""))
        same = sig.decision == row["decision"]
        if same and sig.plan is not None and row["entry"] is not None:
            same = all(abs(a - b) <= 1e-9 * max(1.0, abs(b)) for a, b in [
                (sig.plan.entry, row["entry"]), (sig.plan.stop, row["stop"]),
                (sig.plan.target, row["target"]), (sig.plan.quantity, row["quantity"])])
        return row, sig, same

    # ------------------------------------------------------------------ journal
    def add_trade(self, symbol: str, side: str, quantity: float, entry: Optional[float], exit_price: Optional[float],
                  pnl: Optional[float], opened_at: Optional[datetime] = None, closed_at: Optional[datetime] = None,
                  signal_id: Optional[int] = None, notes: str = "") -> int:
        opened_at = opened_at or datetime.now()
        if pnl is not None and closed_at is None:
            closed_at = datetime.now()
        with self._conn() as c:
            cur = c.execute(
                """INSERT INTO trades (opened_at, closed_at, symbol, side, quantity, entry, exit, pnl, signal_id, notes)
                   VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (opened_at.isoformat(timespec="seconds"), closed_at.isoformat(timespec="seconds") if closed_at else None,
                 symbol, side, quantity, entry, exit_price, pnl, signal_id, notes))
            return int(cur.lastrowid)

    def close_trade(self, trade_id: int, exit_price: float, pnl: float, closed_at: Optional[datetime] = None) -> None:
        closed_at = closed_at or datetime.now()
        with self._conn() as c:
            c.execute("UPDATE trades SET exit=?, pnl=?, closed_at=? WHERE id=?",
                      (exit_price, pnl, closed_at.isoformat(timespec="seconds"), trade_id))

    def trades(self, limit: int = 500) -> pd.DataFrame:
        with self._conn() as c:
            return pd.read_sql_query("SELECT * FROM trades ORDER BY id DESC LIMIT ?", c, params=(limit,))

    def risk_state(self, equity: float, day: Optional[date] = None, emergency: Tuple[bool, str] = (False, "")) -> RiskState:
        day = day or date.today()
        d = day.isoformat()
        with self._conn() as c:
            rows = c.execute("SELECT id, opened_at, closed_at, pnl, notes FROM trades WHERE substr(opened_at,1,10)=? "
                             "OR (pnl IS NOT NULL AND substr(closed_at,1,10)=?) ORDER BY id", (d, d)).fetchall()

        def group(r):  # the two halves of one split order belong to the same signal
            m = re.search(r"signal (\S+)", r["notes"] or "")
            return m.group(1) if m else f"row{r['id']}"

        opened = {group(r) for r in rows if (r["opened_at"] or "")[:10] == d}
        closed_rows = [r for r in rows if r["pnl"] is not None and (r["closed_at"] or "")[:10] == d]
        realized = float(sum(r["pnl"] for r in closed_rows))
        open_groups = {group(r) for r in rows if r["pnl"] is None}
        results = {}
        for r in sorted(closed_rows, key=lambda r: (r["closed_at"], r["id"])):
            g = group(r)
            if g not in open_groups:  # only trades whose every part has closed count toward the losing streak
                results[g] = results.get(g, 0.0) + r["pnl"]
        streak = 0
        for p in reversed(list(results.values())):
            if p < 0:
                streak += 1
            else:
                break
        return RiskState(equity=equity, realized_pnl_today=realized, trades_today=len(opened),
                         consecutive_losses=streak, emergency_stop=emergency[0], emergency_reason=emergency[1])
