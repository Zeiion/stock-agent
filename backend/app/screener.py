"""Stock screener — scan a universe of symbols and filter by live quote +
technical-indicator metrics. Powers the platform's "选股/筛选" feature.

A universe is the watchlist, a curated popular list (all / per-market), or an
explicit symbol list. Filters are generic {field, op, value} conditions AND-ed
together, evaluated against each symbol's quote + indicator snapshot.
"""
from __future__ import annotations

import asyncio
from typing import Any, Optional

from . import db
from .config import settings
from .datahub import datahub
from .indicators import compute_indicators
from .search import POPULAR
from .symbols import canonical, parse

# fields the user can filter on -> where to read them from
_QUOTE_FIELDS = {"last", "change_pct", "open", "high", "low", "volume"}
_IND_FIELDS = {
    "rsi14", "j", "k", "d", "macd", "macd_signal", "macd_hist",
    "ma5", "ma10", "ma20", "ma60", "boll_upper", "boll_lower", "atr14",
    "vol", "vol_ma20", "close",
}
FILTER_FIELDS = sorted(_QUOTE_FIELDS | _IND_FIELDS)

_OPS = {
    ">": lambda a, b: a > b,
    "<": lambda a, b: a < b,
    ">=": lambda a, b: a >= b,
    "<=": lambda a, b: a <= b,
    "==": lambda a, b: abs(a - b) < 1e-9,
    "!=": lambda a, b: abs(a - b) >= 1e-9,
}


def _universe(name: str, custom: Optional[list[str]]) -> list[str]:
    if custom:
        out = []
        for s in custom:
            try:
                out.append(canonical(s))
            except Exception:
                continue
        return out
    if name == "watchlist":
        return db.watch_symbols()
    pref = {"popular_us": "US", "popular_hk": "HK", "popular_cn": "CN"}.get(name)
    syms = [s for s, _ in POPULAR]
    if pref:
        syms = [s for s in syms if parse(s)[0] == pref]
    return syms


def _metric(field: str, quote: dict, ind: dict) -> Optional[float]:
    if field in _QUOTE_FIELDS:
        v = quote.get(field)
    else:
        v = ind.get(field)
    try:
        return None if v is None else float(v)
    except (TypeError, ValueError):
        return None


async def _eval_symbol(sym: str, filters: list[dict]) -> Optional[dict]:
    q = await datahub.get_quote(sym)
    if not q:
        return None
    qd = q.to_dict()
    try:
        df = await datahub.get_history(sym, days=settings.history_lookback_days)
        ind = compute_indicators(df) if not df.empty else {}
    except Exception:
        ind = {}

    for f in filters:
        val = _metric(f.get("field", ""), qd, ind)
        op = _OPS.get(f.get("op", ""))
        if val is None or op is None:
            return None
        try:
            target = float(f.get("value"))
        except (TypeError, ValueError):
            return None
        if not op(val, target):
            return None

    return {
        "symbol": sym, "market": qd.get("market"), "name": qd.get("name", ""),
        "last": qd.get("last"), "change_pct": qd.get("change_pct"),
        "rsi14": ind.get("rsi14"), "j": ind.get("j"),
        "macd_hist": ind.get("macd_hist"),
        "ma5": ind.get("ma5"), "ma20": ind.get("ma20"),
        "tags": ind.get("tags", []),
    }


async def screen(universe: str = "watchlist", symbols: Optional[list[str]] = None,
                 filters: Optional[list[dict]] = None, limit: int = 60) -> dict:
    syms = _universe(universe, symbols)[:limit]
    filters = filters or []
    sem = asyncio.Semaphore(8)

    async def run(s: str) -> Optional[dict]:
        async with sem:
            try:
                return await _eval_symbol(s, filters)
            except Exception as e:
                print(f"[screener] {s} failed: {e}")
                return None

    results = await asyncio.gather(*[run(s) for s in syms])
    matches = [r for r in results if r]
    # sort by absolute % move (most active first)
    matches.sort(key=lambda r: abs(r.get("change_pct") or 0), reverse=True)
    return {"universe": universe, "scanned": len(syms),
            "matched": len(matches), "matches": matches}
