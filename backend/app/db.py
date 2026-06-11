"""SQLite persistence: watchlist, rules, alerts, decisions, positions, paper orders.

Uses the stdlib sqlite3 with a process-wide lock so it is safe to call from the
asyncio daemon, FastAPI request handlers (via run_in_threadpool), and routines.
Rows are returned as plain dicts.
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Optional

from .config import settings
from .models import Alert, Decision, PaperOrder, Position, Rule

_LOCK = threading.RLock()
_CONN: Optional[sqlite3.Connection] = None


SCHEMA = """
CREATE TABLE IF NOT EXISTS watchlist (
    symbol      TEXT PRIMARY KEY,
    name        TEXT DEFAULT '',
    market      TEXT NOT NULL,
    added_ts    REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS rules (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol      TEXT NOT NULL,
    type        TEXT NOT NULL,
    params      TEXT NOT NULL,
    severity    TEXT NOT NULL DEFAULT 'normal',
    cooldown_s  INTEGER NOT NULL DEFAULT 300,
    active      INTEGER NOT NULL DEFAULT 1,
    note        TEXT DEFAULT '',
    last_fired  REAL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS alerts (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol      TEXT NOT NULL,
    rule_id     INTEGER,
    rule_type   TEXT NOT NULL,
    severity    TEXT NOT NULL,
    message     TEXT NOT NULL,
    snapshot    TEXT NOT NULL,
    ts          REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS decisions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol      TEXT NOT NULL,
    action      TEXT NOT NULL,
    conviction  INTEGER NOT NULL,
    horizon     TEXT NOT NULL,
    rationale   TEXT NOT NULL,
    key_risks   TEXT NOT NULL,
    entry_zone  TEXT,
    stop_loss   REAL,
    take_profit TEXT,
    data_freshness_ok INTEGER NOT NULL DEFAULT 1,
    strategy    TEXT DEFAULT 'balanced',
    provider    TEXT NOT NULL,
    model       TEXT DEFAULT '',
    ensemble    TEXT,
    snapshot    TEXT NOT NULL,
    realized_return REAL,
    ts          REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS positions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol      TEXT NOT NULL,
    account     TEXT NOT NULL DEFAULT 'default',
    qty         REAL NOT NULL,
    avg_cost    REAL NOT NULL,
    UNIQUE(symbol, account)
);
CREATE TABLE IF NOT EXISTS paper_orders (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol      TEXT NOT NULL,
    account     TEXT NOT NULL DEFAULT 'default',
    side        TEXT NOT NULL,
    qty         REAL NOT NULL,
    limit_price REAL,
    status      TEXT NOT NULL DEFAULT 'pending',
    fill_price  REAL,
    source      TEXT NOT NULL DEFAULT 'ai',
    note        TEXT DEFAULT '',
    ts          REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS kv (
    k           TEXT PRIMARY KEY,
    v           TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS nav_history (
    ts              REAL NOT NULL,
    account         TEXT NOT NULL DEFAULT 'default',
    nav             REAL NOT NULL,
    holdings_value  REAL NOT NULL,
    unrealized      REAL NOT NULL,
    realized_cum    REAL NOT NULL,
    PRIMARY KEY(ts, account)
);
CREATE TABLE IF NOT EXISTS realized_trades (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol      TEXT NOT NULL,
    account     TEXT NOT NULL DEFAULT 'default',
    qty         REAL NOT NULL,
    avg_cost    REAL NOT NULL,
    exit_price  REAL NOT NULL,
    pnl         REAL NOT NULL,
    ret_pct     REAL NOT NULL,
    ts          REAL NOT NULL
);
"""


def _conn() -> sqlite3.Connection:
    global _CONN
    if _CONN is None:
        Path(settings.db_path).parent.mkdir(parents=True, exist_ok=True)
        _CONN = sqlite3.connect(settings.db_path, check_same_thread=False)
        _CONN.row_factory = sqlite3.Row
        _CONN.executescript(SCHEMA)
        _migrate(_CONN)
        _CONN.commit()
    return _CONN


def _migrate(c: sqlite3.Connection) -> None:
    """Add the per-account column to pre-existing tables (older DBs)."""
    def cols(tbl: str) -> list[str]:
        return [r[1] for r in c.execute(f"PRAGMA table_info({tbl})")]

    for tbl in ("paper_orders", "realized_trades", "nav_history"):
        if "account" not in cols(tbl):
            c.execute(f"ALTER TABLE {tbl} ADD COLUMN account TEXT NOT NULL "
                      f"DEFAULT 'default'")
    if "strategy" not in cols("decisions"):
        c.execute("ALTER TABLE decisions ADD COLUMN strategy TEXT DEFAULT 'balanced'")
    # positions needs composite UNIQUE(symbol, account) -> rebuild if old schema
    if "account" not in cols("positions"):
        c.execute("ALTER TABLE positions RENAME TO positions_old")
        c.executescript("""
            CREATE TABLE positions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL,
                account TEXT NOT NULL DEFAULT 'default',
                qty REAL NOT NULL,
                avg_cost REAL NOT NULL,
                UNIQUE(symbol, account)
            );
        """)
        c.execute("INSERT INTO positions(symbol,account,qty,avg_cost) "
                  "SELECT symbol,'default',qty,avg_cost FROM positions_old")
        c.execute("DROP TABLE positions_old")


def init_db() -> None:
    with _LOCK:
        _conn()


# --------------------------------------------------------------------------- #
# Accounts (paper trading books)
# --------------------------------------------------------------------------- #
def current_account() -> str:
    return kv_get("current_account", "default") or "default"


def set_current_account(name: str) -> None:
    name = (name or "default").strip() or "default"
    accs = list_accounts()
    if name not in accs:
        accs.append(name)
        kv_set("accounts", accs)
    kv_set("current_account", name)


def list_accounts() -> list[str]:
    accs = kv_get("accounts", None)
    if not accs:
        accs = ["default"]
        kv_set("accounts", accs)
    if "default" not in accs:
        accs = ["default"] + accs
    return accs


def add_account(name: str) -> None:
    name = (name or "").strip()
    if not name:
        return
    accs = list_accounts()
    if name not in accs:
        accs.append(name)
        kv_set("accounts", accs)


def delete_account(name: str) -> None:
    name = (name or "").strip()
    if not name or name == "default":
        return
    reset_account(name)
    accs = [a for a in list_accounts() if a != name]
    kv_set("accounts", accs)
    if current_account() == name:
        kv_set("current_account", "default")


def reset_account(name: Optional[str] = None) -> None:
    """Wipe all paper records (positions/orders/realized/nav) for one account."""
    acct = name or current_account()
    with _LOCK:
        c = _conn()
        for tbl in ("positions", "paper_orders", "realized_trades", "nav_history"):
            c.execute(f"DELETE FROM {tbl} WHERE account=?", (acct,))
        c.commit()


def _rows(cur) -> list[dict[str, Any]]:
    return [dict(r) for r in cur.fetchall()]


# --------------------------------------------------------------------------- #
# Watchlist
# --------------------------------------------------------------------------- #
def add_watch(symbol: str, market: str, name: str = "") -> None:
    with _LOCK:
        c = _conn()
        c.execute(
            "INSERT OR REPLACE INTO watchlist(symbol,name,market,added_ts) "
            "VALUES(?,?,?,?)",
            (symbol, name, market, time.time()),
        )
        c.commit()


def remove_watch(symbol: str) -> None:
    with _LOCK:
        c = _conn()
        c.execute("DELETE FROM watchlist WHERE symbol=?", (symbol,))
        c.execute("DELETE FROM rules WHERE symbol=?", (symbol,))
        c.commit()


def list_watch() -> list[dict[str, Any]]:
    with _LOCK:
        return _rows(_conn().execute(
            "SELECT * FROM watchlist ORDER BY added_ts"))


def watch_symbols() -> list[str]:
    return [r["symbol"] for r in list_watch()]


# --------------------------------------------------------------------------- #
# Rules
# --------------------------------------------------------------------------- #
def add_rule(rule: Rule) -> int:
    with _LOCK:
        c = _conn()
        cur = c.execute(
            "INSERT INTO rules(symbol,type,params,severity,cooldown_s,active,note) "
            "VALUES(?,?,?,?,?,?,?)",
            (rule.symbol, rule.type, json.dumps(rule.params), rule.severity,
             rule.cooldown_s, int(rule.active), rule.note),
        )
        c.commit()
        return int(cur.lastrowid)


def list_rules(symbol: Optional[str] = None, active_only: bool = False
               ) -> list[dict[str, Any]]:
    q = "SELECT * FROM rules"
    args: list[Any] = []
    conds = []
    if symbol:
        conds.append("symbol=?"); args.append(symbol)
    if active_only:
        conds.append("active=1")
    if conds:
        q += " WHERE " + " AND ".join(conds)
    with _LOCK:
        rows = _rows(_conn().execute(q, args))
    for r in rows:
        r["params"] = json.loads(r["params"])
        r["active"] = bool(r["active"])
    return rows


def set_rule_active(rule_id: int, active: bool) -> None:
    with _LOCK:
        c = _conn()
        c.execute("UPDATE rules SET active=? WHERE id=?", (int(active), rule_id))
        c.commit()


def delete_rule(rule_id: int) -> None:
    with _LOCK:
        c = _conn()
        c.execute("DELETE FROM rules WHERE id=?", (rule_id,))
        c.commit()


def mark_rule_fired(rule_id: int, ts: float) -> None:
    with _LOCK:
        c = _conn()
        c.execute("UPDATE rules SET last_fired=? WHERE id=?", (ts, rule_id))
        c.commit()


def rule_last_fired(rule_id: int) -> float:
    with _LOCK:
        row = _conn().execute(
            "SELECT last_fired FROM rules WHERE id=?", (rule_id,)).fetchone()
        return float(row["last_fired"]) if row else 0.0


# --------------------------------------------------------------------------- #
# Alerts
# --------------------------------------------------------------------------- #
def add_alert(alert: Alert) -> int:
    with _LOCK:
        c = _conn()
        cur = c.execute(
            "INSERT INTO alerts(symbol,rule_id,rule_type,severity,message,snapshot,ts) "
            "VALUES(?,?,?,?,?,?,?)",
            (alert.symbol, alert.rule_id, alert.rule_type, alert.severity,
             alert.message, json.dumps(alert.snapshot), alert.ts),
        )
        c.commit()
        return int(cur.lastrowid)


def list_alerts(limit: int = 100) -> list[dict[str, Any]]:
    with _LOCK:
        rows = _rows(_conn().execute(
            "SELECT * FROM alerts ORDER BY ts DESC LIMIT ?", (limit,)))
    for r in rows:
        r["snapshot"] = json.loads(r["snapshot"])
    return rows


# --------------------------------------------------------------------------- #
# Decisions
# --------------------------------------------------------------------------- #
def add_decision(d: Decision) -> int:
    with _LOCK:
        c = _conn()
        cur = c.execute(
            "INSERT INTO decisions(symbol,action,conviction,horizon,rationale,"
            "key_risks,entry_zone,stop_loss,take_profit,data_freshness_ok,strategy,"
            "provider,model,ensemble,snapshot,ts) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (d.symbol, d.action, d.conviction, d.horizon, d.rationale,
             json.dumps(d.key_risks),
             json.dumps(d.entry_zone) if d.entry_zone is not None else None,
             d.stop_loss,
             json.dumps(d.take_profit) if d.take_profit is not None else None,
             int(d.data_freshness_ok), getattr(d, "strategy", "balanced"),
             d.provider, d.model,
             json.dumps(d.ensemble) if d.ensemble is not None else None,
             json.dumps(d.snapshot), d.ts),
        )
        c.commit()
        return int(cur.lastrowid)


def list_decisions(symbol: Optional[str] = None, limit: int = 100
                   ) -> list[dict[str, Any]]:
    q = "SELECT * FROM decisions"
    args: list[Any] = []
    if symbol:
        q += " WHERE symbol=?"; args.append(symbol)
    q += " ORDER BY ts DESC LIMIT ?"; args.append(limit)
    with _LOCK:
        rows = _rows(_conn().execute(q, args))
    for r in rows:
        r["key_risks"] = json.loads(r["key_risks"]) if r["key_risks"] else []
        r["entry_zone"] = json.loads(r["entry_zone"]) if r["entry_zone"] else None
        r["take_profit"] = json.loads(r["take_profit"]) if r["take_profit"] else None
        r["ensemble"] = json.loads(r["ensemble"]) if r["ensemble"] else None
        r["snapshot"] = json.loads(r["snapshot"]) if r["snapshot"] else {}
        r["data_freshness_ok"] = bool(r["data_freshness_ok"])
    return rows


# --------------------------------------------------------------------------- #
# Positions
# --------------------------------------------------------------------------- #
def upsert_position(symbol: str, qty: float, avg_cost: float) -> None:
    acct = current_account()
    with _LOCK:
        c = _conn()
        if qty == 0:
            c.execute("DELETE FROM positions WHERE symbol=? AND account=?",
                      (symbol, acct))
        else:
            c.execute(
                "INSERT INTO positions(symbol,account,qty,avg_cost) VALUES(?,?,?,?) "
                "ON CONFLICT(symbol,account) DO UPDATE SET qty=excluded.qty, "
                "avg_cost=excluded.avg_cost",
                (symbol, acct, qty, avg_cost))
        c.commit()


def list_positions() -> list[dict[str, Any]]:
    with _LOCK:
        return _rows(_conn().execute(
            "SELECT * FROM positions WHERE account=?", (current_account(),)))


def get_position(symbol: str) -> Optional[dict[str, Any]]:
    with _LOCK:
        row = _conn().execute(
            "SELECT * FROM positions WHERE symbol=? AND account=?",
            (symbol, current_account())).fetchone()
        return dict(row) if row else None


# --------------------------------------------------------------------------- #
# Paper orders
# --------------------------------------------------------------------------- #
def add_paper_order(o: PaperOrder) -> int:
    with _LOCK:
        c = _conn()
        cur = c.execute(
            "INSERT INTO paper_orders(symbol,account,side,qty,limit_price,status,"
            "fill_price,source,note,ts) VALUES(?,?,?,?,?,?,?,?,?,?)",
            (o.symbol, current_account(), o.side, o.qty, o.limit_price, o.status,
             o.fill_price, o.source, o.note, o.ts))
        c.commit()
        return int(cur.lastrowid)


def update_paper_order(order_id: int, **fields: Any) -> None:
    if not fields:
        return
    cols = ", ".join(f"{k}=?" for k in fields)
    with _LOCK:
        c = _conn()
        c.execute(f"UPDATE paper_orders SET {cols} WHERE id=?",
                  (*fields.values(), order_id))
        c.commit()


def list_paper_orders(status: Optional[str] = None, limit: int = 200
                      ) -> list[dict[str, Any]]:
    q = "SELECT * FROM paper_orders WHERE account=?"
    args: list[Any] = [current_account()]
    if status:
        q += " AND status=?"; args.append(status)
    q += " ORDER BY ts DESC LIMIT ?"; args.append(limit)
    with _LOCK:
        return _rows(_conn().execute(q, args))


def get_paper_order(order_id: int) -> Optional[dict[str, Any]]:
    with _LOCK:
        row = _conn().execute(
            "SELECT * FROM paper_orders WHERE id=?", (order_id,)).fetchone()
        return dict(row) if row else None


# --------------------------------------------------------------------------- #
# KV (small settings / state)
# --------------------------------------------------------------------------- #
def kv_set(k: str, v: Any) -> None:
    with _LOCK:
        c = _conn()
        c.execute("INSERT INTO kv(k,v) VALUES(?,?) "
                  "ON CONFLICT(k) DO UPDATE SET v=excluded.v",
                  (k, json.dumps(v)))
        c.commit()


def kv_get(k: str, default: Any = None) -> Any:
    with _LOCK:
        row = _conn().execute("SELECT v FROM kv WHERE k=?", (k,)).fetchone()
        return json.loads(row["v"]) if row else default


# --------------------------------------------------------------------------- #
# NAV history (paper portfolio equity curve)
# --------------------------------------------------------------------------- #
def record_nav(ts: float, nav: float, holdings_value: float,
               unrealized: float, realized_cum: float) -> None:
    with _LOCK:
        c = _conn()
        c.execute(
            "INSERT OR REPLACE INTO nav_history(ts,account,nav,holdings_value,"
            "unrealized,realized_cum) VALUES(?,?,?,?,?,?)",
            (ts, current_account(), nav, holdings_value, unrealized, realized_cum))
        c.commit()


def list_nav(limit: int = 2000) -> list[dict[str, Any]]:
    with _LOCK:
        rows = _rows(_conn().execute(
            "SELECT * FROM nav_history WHERE account=? ORDER BY ts DESC LIMIT ?",
            (current_account(), limit)))
    return list(reversed(rows))


# --------------------------------------------------------------------------- #
# Realized trades (closed paper positions)
# --------------------------------------------------------------------------- #
def add_realized_trade(symbol: str, qty: float, avg_cost: float,
                       exit_price: float, ts: float) -> int:
    pnl = (exit_price - avg_cost) * qty
    ret_pct = ((exit_price - avg_cost) / avg_cost * 100) if avg_cost else 0.0
    with _LOCK:
        c = _conn()
        cur = c.execute(
            "INSERT INTO realized_trades(symbol,account,qty,avg_cost,exit_price,"
            "pnl,ret_pct,ts) VALUES(?,?,?,?,?,?,?,?)",
            (symbol, current_account(), qty, avg_cost, exit_price,
             round(pnl, 4), round(ret_pct, 4), ts))
        c.commit()
        return int(cur.lastrowid)


def list_realized_trades(limit: int = 500) -> list[dict[str, Any]]:
    with _LOCK:
        return _rows(_conn().execute(
            "SELECT * FROM realized_trades WHERE account=? ORDER BY ts DESC LIMIT ?",
            (current_account(), limit)))


def realized_summary() -> dict[str, Any]:
    with _LOCK:
        rows = _rows(_conn().execute(
            "SELECT pnl, ret_pct FROM realized_trades WHERE account=?",
            (current_account(),)))
    n = len(rows)
    wins = sum(1 for r in rows if r["pnl"] > 0)
    total = sum(r["pnl"] for r in rows)
    return {
        "closed_trades": n,
        "wins": wins,
        "losses": n - wins,
        "win_rate": round(wins / n * 100, 1) if n else 0.0,
        "realized_pnl": round(total, 2),
        "avg_return_pct": round(sum(r["ret_pct"] for r in rows) / n, 2) if n else 0.0,
        "best": round(max((r["pnl"] for r in rows), default=0.0), 2),
        "worst": round(min((r["pnl"] for r in rows), default=0.0), 2),
    }


def update_decision_realized(decision_id: int, realized_return: float) -> None:
    with _LOCK:
        c = _conn()
        c.execute("UPDATE decisions SET realized_return=? WHERE id=?",
                  (realized_return, decision_id))
        c.commit()
