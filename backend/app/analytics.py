"""Paper-portfolio analytics — pure / synchronous, no network.

Everything here operates on the SQLite store (``app.db``) plus a caller-supplied
``latest`` map of the most-recent quotes.  The map is the daemon's in-memory
latest-quote cache:  canonical symbol -> ``Quote.to_dict()`` (keys include
``last``, ``market``, ``name``, ``change_pct``, ``currency``).

The two entry points:

    compute_portfolio(latest) -> dict   # full marked-to-market snapshot
    snapshot_nav(latest)      -> dict   # persist one NAV point + return it

No vendor calls happen here, so this is safe to call from request handlers and
from the daemon loop without ``run_in_threadpool``.  We never raise for a single
bad row — we degrade (fall back to avg_cost, skip bad entries) and keep going.
"""
from __future__ import annotations

import time
from typing import Any

from . import db, symbols


def _round(x: float, dp: int = 2) -> float:
    """Defensive round that never blows up on None / non-numeric input."""
    try:
        return round(float(x), dp)
    except (TypeError, ValueError):
        return 0.0


def compute_portfolio(latest: dict) -> dict:
    """Mark the paper portfolio to market against ``latest`` quotes.

    ``latest``: dict[str, dict] canonical-symbol -> Quote.to_dict().  Missing or
    null ``last`` falls back to the position's average cost (zero P&L for that
    leg).  Returns a fully-rounded, JSON-friendly dict; an empty portfolio
    yields all-zeros and empty lists.
    """
    latest = latest or {}

    positions = db.list_positions()        # [{symbol, qty, avg_cost, id}]

    enriched: list[dict[str, Any]] = []
    exposure: dict[str, float] = {}        # market -> summed market value

    holdings_value = 0.0
    cost_basis = 0.0

    for p in positions:
        try:
            sym = p["symbol"]
            qty = float(p.get("qty") or 0.0)
            avg_cost = float(p.get("avg_cost") or 0.0)
        except Exception as exc:  # noqa: BLE001 - never let one row kill the report
            print(f"[analytics] skipping bad position row {p!r}: {exc}")
            continue

        q = latest.get(sym) or {}
        last = q.get("last")
        if last is None:
            last = avg_cost
        try:
            last = float(last)
        except (TypeError, ValueError):
            last = avg_cost

        # Market: trust the cached quote, else derive from the canonical symbol.
        market = q.get("market")
        if not market:
            try:
                market, _ = symbols.parse(sym)
            except Exception:  # noqa: BLE001
                market = ""

        name = q.get("name") or ""

        value = last * qty
        cost = avg_cost * qty
        unreal = value - cost
        unreal_pct = (unreal / cost * 100) if cost else 0.0

        holdings_value += value
        cost_basis += cost
        if market:
            exposure[market] = exposure.get(market, 0.0) + value

        enriched.append({
            "symbol": sym,
            "market": market,
            "name": name,
            "qty": _round(qty, 4),
            "avg_cost": _round(avg_cost, 4),
            "last": _round(last, 4),
            "value": _round(value, 2),
            "unrealized": _round(unreal, 2),
            "unrealized_pct": _round(unreal_pct, 2),
        })

    enriched.sort(key=lambda r: r["value"], reverse=True)

    unrealized = holdings_value - cost_basis
    unrealized_pct = (unrealized / cost_basis * 100) if cost_basis else 0.0

    realized = db.realized_summary()       # {realized_pnl, win_rate, ...}
    realized_pnl = float(realized.get("realized_pnl") or 0.0)

    # Equity proxy: marked-to-market positions + banked realized P&L.
    nav = holdings_value + realized_pnl

    exposure_pct = {
        m: _round(v / holdings_value * 100, 2) if holdings_value else 0.0
        for m, v in exposure.items()
    }
    exposure = {m: _round(v, 2) for m, v in exposure.items()}

    return {
        "nav": _round(nav, 2),
        "holdings_value": _round(holdings_value, 2),
        "cost_basis": _round(cost_basis, 2),
        "unrealized": _round(unrealized, 2),
        "unrealized_pct": _round(unrealized_pct, 2),
        "realized": realized,
        "positions": enriched,
        "exposure": exposure,
        "exposure_pct": exposure_pct,
    }


def snapshot_nav(latest: dict) -> dict:
    """Compute current portfolio state and persist a single NAV history point.

    Returns the recorded point so callers can echo it without a re-read.
    """
    p = compute_portfolio(latest)
    ts = time.time()
    realized_cum = float(p["realized"].get("realized_pnl") or 0.0)

    try:
        db.record_nav(ts, p["nav"], p["holdings_value"], p["unrealized"], realized_cum)
    except Exception as exc:  # noqa: BLE001 - persistence failure must not crash the daemon
        print(f"[analytics] record_nav failed: {exc}")

    return {
        "ts": ts,
        "nav": p["nav"],
        "holdings_value": p["holdings_value"],
        "unrealized": p["unrealized"],
        "realized_cum": _round(realized_cum, 2),
    }
