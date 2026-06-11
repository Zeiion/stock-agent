"""Display-name resolution for canonical symbols.

`name_for(sym)` is an instant, non-blocking best-effort lookup ('' if unknown),
backed by an in-memory cache that is:
  1. seeded from the curated bilingual POPULAR list (offline, instant), and
  2. warmed in the background from akshare's A-share / HK name tables.

DataHub enriches every Quote with this, so the watchlist + symbol header show
中文 names instead of bare codes — yfinance's `fast_info` carries no name, which
is why HK/CN rows (e.g. "00700") otherwise render nameless.
"""
from __future__ import annotations

import asyncio
import time
from typing import Any

_cache: dict[str, str] = {}          # canonical symbol -> short display name
_long: dict[str, str] = {}           # canonical symbol -> descriptive/long name
_seeded = False
_warming: set[str] = set()           # symbols with an in-flight warm
_attempted: dict[str, float] = {}    # symbol -> last warm attempt ts (retry backoff)
_RETRY_TTL = 600.0                    # don't re-warm a still-unresolved symbol within 10 min
_AK_TTL = 86400.0
_ak_a: dict[str, Any] = {"ts": 0.0, "map": None}    # 6-digit A-share code -> name
_ak_hk: dict[str, Any] = {"ts": 0.0, "map": None}   # 5-digit HK code -> name


def _seed_popular() -> None:
    global _seeded
    if _seeded:
        return
    _seeded = True
    try:
        from .search import POPULAR
        for sym, name in POPULAR:
            _cache.setdefault(sym, name)
            _long.setdefault(sym, name)
    except Exception:
        pass


def name_for(symbol: str) -> str:
    """Instant, non-blocking short-name lookup ('' if not yet known)."""
    if not _seeded:
        _seed_popular()
    return _cache.get(symbol, "")


def long_name_for(symbol: str) -> str:
    """Descriptive name if known, else the short name, else ''."""
    if not _seeded:
        _seed_popular()
    return _long.get(symbol) or _cache.get(symbol, "")


def set_name(symbol: str, name: str) -> None:
    name = (name or "").strip()
    if name:
        _cache.setdefault(symbol, name)
        _long.setdefault(symbol, name)


def _digits(s: str) -> str:
    return "".join(ch for ch in s if ch.isdigit())


def _warm_blocking(symbols: list[str]) -> None:
    """Load akshare name tables (cached 24h) and fill the cache for CN/HK."""
    try:
        import akshare as ak
    except Exception:
        return
    now = time.time()
    want_cn = [s for s in symbols if s.startswith("CN:")]
    want_hk = [s for s in symbols if s.startswith("HK:")]

    if want_cn:
        try:
            if _ak_a["map"] is None or now - _ak_a["ts"] > _AK_TTL:
                df = ak.stock_info_a_code_name()
                cc = "code" if "code" in df.columns else ("代码" if "代码" in df.columns else None)
                nc = "name" if "name" in df.columns else ("名称" if "名称" in df.columns else None)
                m: dict[str, str] = {}
                if cc and nc:
                    for _, r in df.iterrows():
                        m[str(r[cc]).zfill(6)] = str(r[nc])
                _ak_a["map"] = m
                _ak_a["ts"] = now
            table = _ak_a["map"] or {}
            for s in want_cn:
                nm = table.get(_digits(s)[-6:].zfill(6))
                if nm:
                    _cache.setdefault(s, nm)
                    _long.setdefault(s, nm)
        except Exception as e:
            print(f"[names] akshare A-list failed: {e}")

    if want_hk:
        try:
            if _ak_hk["map"] is None or now - _ak_hk["ts"] > _AK_TTL:
                df = ak.stock_hk_spot_em()
                cc = "代码" if "代码" in df.columns else ("symbol" if "symbol" in df.columns else None)
                nc = "名称" if "名称" in df.columns else ("name" if "name" in df.columns else None)
                m = {}
                if cc and nc:
                    for _, r in df.iterrows():
                        m[_digits(str(r[cc])).zfill(5)] = str(r[nc])
                _ak_hk["map"] = m
                _ak_hk["ts"] = now
            table = _ak_hk["map"] or {}
            for s in want_hk:
                nm = table.get(_digits(s)[-5:].zfill(5))
                if nm:
                    _cache.setdefault(s, nm)
                    _long.setdefault(s, nm)
        except Exception as e:
            print(f"[names] akshare HK-list failed: {e}")

    # Per-symbol yfinance fallback for the long tail akshare didn't cover
    # (US names, odd HK products, anything off the A-share table). Slow-ish but
    # this runs in the background and each result is cached, so it's paid once.
    still = [s for s in symbols if not _cache.get(s)][:25]
    if still:
        try:
            import yfinance as yf
            from .symbols import to_yfinance
        except Exception:
            return
        for s in still:
            try:
                info = yf.Ticker(to_yfinance(s)).info or {}
                short = (info.get("shortName") or info.get("longName") or "").strip()
                lng = (info.get("longName") or info.get("shortName") or "").strip()
                if short:
                    _cache.setdefault(s, short)
                if lng:
                    _long.setdefault(s, lng)
            except Exception:
                continue


async def ensure_warm(symbols: list[str]) -> None:
    """Best-effort background fill for any symbol lacking a cached name.

    Resolved names are cached forever; symbols that fail to resolve (flaky
    upstream) are retried at most once per _RETRY_TTL so polling can't hammer
    a permanently-nameless symbol on every cycle."""
    _seed_popular()
    now = time.time()
    todo = [s for s in symbols
            if not _cache.get(s) and s not in _warming
            and now - _attempted.get(s, 0.0) > _RETRY_TTL]
    if not todo:
        return
    _warming.update(todo)
    for s in todo:
        _attempted[s] = now
    try:
        await asyncio.to_thread(_warm_blocking, todo)
    finally:
        for s in todo:
            _warming.discard(s)
