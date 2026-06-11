"""Serialize platform data to CSV for download.

Pure / deterministic: reads from the SQLite layer (app.db) and renders flat
CSV via the stdlib csv module. No network, no LLM. Designed to degrade
gracefully -- a single malformed row never aborts the whole export.

Public entry point:
    export_csv(kind) -> (filename, csv_text)

Supported kinds: decisions, trades, alerts, positions, nav.
"""
from __future__ import annotations

import csv
import io
from datetime import datetime
from typing import Any, Callable

from . import db


# --------------------------------------------------------------------------- #
# Small helpers
# --------------------------------------------------------------------------- #
def _iso(ts: Any) -> str:
    """Epoch seconds -> local ISO-8601 string (second precision). '' on bad input."""
    try:
        return datetime.fromtimestamp(float(ts)).isoformat(timespec="seconds")
    except (TypeError, ValueError, OSError, OverflowError):
        return ""


def _s(v: Any) -> str:
    """Stringify a scalar; None / missing -> ''."""
    if v is None:
        return ""
    if isinstance(v, bool):
        return "true" if v else "false"
    return str(v)


def _join(v: Any, sep: str) -> str:
    """Join a list-like into a single cell; tolerate non-list / None."""
    if v is None:
        return ""
    if isinstance(v, (list, tuple)):
        return sep.join(_s(x) for x in v)
    return _s(v)


def _ensemble_action(v: Any) -> str:
    """Reduce the big ensemble object down to just its action, if any."""
    if isinstance(v, dict):
        return _s(v.get("action", ""))
    return ""


# --------------------------------------------------------------------------- #
# Per-kind specifications: (header columns, row-mapper)
# Each mapper takes a db row dict and returns a list[str] aligned to columns.
# --------------------------------------------------------------------------- #
def _decisions_rows() -> list[dict[str, Any]]:
    return db.list_decisions(limit=1000)


def _decisions_map(r: dict[str, Any]) -> list[str]:
    return [
        _iso(r.get("ts")),
        _s(r.get("symbol")),
        _s(r.get("action")),
        _s(r.get("conviction")),
        _s(r.get("horizon")),
        _s(r.get("provider")),
        _s(r.get("model")),
        _s(r.get("stop_loss")),
        _join(r.get("entry_zone"), "|"),
        _join(r.get("take_profit"), "|"),
        _s(r.get("data_freshness_ok")),
        _s(r.get("realized_return")),
        _s(r.get("rationale")),
        _join(r.get("key_risks"), " / "),
        _ensemble_action(r.get("ensemble")),
    ]


def _trades_map(r: dict[str, Any]) -> list[str]:
    return [
        _iso(r.get("ts")),
        _s(r.get("symbol")),
        _s(r.get("qty")),
        _s(r.get("avg_cost")),
        _s(r.get("exit_price")),
        _s(r.get("pnl")),
        _s(r.get("ret_pct")),
    ]


def _alerts_map(r: dict[str, Any]) -> list[str]:
    return [
        _iso(r.get("ts")),
        _s(r.get("symbol")),
        _s(r.get("rule_type")),
        _s(r.get("severity")),
        _s(r.get("message")),
    ]


def _positions_map(r: dict[str, Any]) -> list[str]:
    return [
        _s(r.get("symbol")),
        _s(r.get("qty")),
        _s(r.get("avg_cost")),
    ]


def _nav_map(r: dict[str, Any]) -> list[str]:
    return [
        _iso(r.get("ts")),
        _s(r.get("nav")),
        _s(r.get("holdings_value")),
        _s(r.get("unrealized")),
        _s(r.get("realized_cum")),
    ]


# kind -> (header, fetch_fn, row_mapper)
_SPECS: dict[str, tuple[list[str], Callable[[], list[dict[str, Any]]],
                        Callable[[dict[str, Any]], list[str]]]] = {
    "decisions": (
        ["ts", "symbol", "action", "conviction", "horizon", "provider", "model",
         "stop_loss", "entry_zone", "take_profit", "data_freshness_ok",
         "realized_return", "rationale", "key_risks", "ensemble_action"],
        _decisions_rows,
        _decisions_map,
    ),
    "trades": (
        ["ts", "symbol", "qty", "avg_cost", "exit_price", "pnl", "ret_pct"],
        lambda: db.list_realized_trades(limit=2000),
        _trades_map,
    ),
    "alerts": (
        ["ts", "symbol", "rule_type", "severity", "message"],
        lambda: db.list_alerts(limit=2000),
        _alerts_map,
    ),
    "positions": (
        ["symbol", "qty", "avg_cost"],
        lambda: db.list_positions(),
        _positions_map,
    ),
    "nav": (
        ["ts", "nav", "holdings_value", "unrealized", "realized_cum"],
        lambda: db.list_nav(limit=5000),
        _nav_map,
    ),
}


# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #
def export_csv(kind: str) -> tuple[str, str]:
    """Render `kind` data to CSV.

    Returns (filename, csv_text). Raises ValueError for an unknown kind.
    Bad individual rows are skipped rather than crashing the whole export.
    """
    spec = _SPECS.get((kind or "").strip().lower())
    if spec is None:
        raise ValueError(
            f"未知导出类型: {kind!r}（可选: {', '.join(sorted(_SPECS))}）")
    header, fetch, mapper = spec

    try:
        rows = fetch() or []
    except Exception:
        rows = []

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(header)
    for r in rows:
        try:
            if not isinstance(r, dict):
                continue
            writer.writerow(mapper(r))
        except Exception:
            # Never let one malformed row abort the export.
            continue

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"{kind}_{stamp}.csv"
    return filename, buf.getvalue()
