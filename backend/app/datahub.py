"""DataHub: the single point that turns canonical symbols into normalized Quotes
and OHLCV history, choosing a data adapter per market with fallback.

Priority per market (first that succeeds wins):
    CN : akshare (near-real-time, no key)  -> yfinance (delayed)
    HK : akshare (delayed)                 -> yfinance (delayed)
    US : finnhub (real-time, if key)       -> yfinance (real-time-ish)

History is cached briefly so charts / indicators / backtests don't refetch.
Also exposes market-hours helpers used by the daemon to throttle polling.
"""
from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone, timedelta, time as dtime
from typing import Optional
from zoneinfo import ZoneInfo

import pandas as pd

from .adapters.base import MarketAdapter
from .config import settings
from .models import Quote
from .symbols import parse


# --------------------------------------------------------------------------- #
# Market trading hours (local exchange time). Simplified; ignores half-days.
# --------------------------------------------------------------------------- #
_MARKET_TZ = {
    "US": ZoneInfo("America/New_York"),
    "HK": ZoneInfo("Asia/Hong_Kong"),
    "CN": ZoneInfo("Asia/Shanghai"),
}
# (open, close) local time windows; CN/HK have a lunch break (two windows).
_MARKET_SESSIONS = {
    "US": [(dtime(9, 30), dtime(16, 0))],
    "HK": [(dtime(9, 30), dtime(12, 0)), (dtime(13, 0), dtime(16, 0))],
    "CN": [(dtime(9, 30), dtime(11, 30)), (dtime(13, 0), dtime(15, 0))],
}


def market_is_open(market: str, now_utc: Optional[float] = None) -> bool:
    tz = _MARKET_TZ.get(market)
    if tz is None:
        return False
    dt = datetime.fromtimestamp(now_utc or time.time(), tz=timezone.utc).astimezone(tz)
    if dt.weekday() >= 5:                     # Sat/Sun
        return False
    t = dt.time()
    return any(o <= t <= c for o, c in _MARKET_SESSIONS[market])


def any_market_open(markets: Optional[list[str]] = None) -> bool:
    markets = markets or ["US", "HK", "CN"]
    return any(market_is_open(m) for m in markets)


# --------------------------------------------------------------------------- #
# DataHub
# --------------------------------------------------------------------------- #
class DataHub:
    def __init__(self) -> None:
        self._adapters: dict[str, list[MarketAdapter]] = {"US": [], "HK": [], "CN": []}
        self._hist_cache: dict[str, tuple[float, pd.DataFrame]] = {}
        self._hist_ttl = 60.0
        self._built = False
        self._lock = asyncio.Lock()

    def _build(self) -> None:
        """Lazily import & register adapters (keeps optional deps optional)."""
        if self._built:
            return
        # Imported lazily so a missing optional package doesn't kill startup.
        from .adapters.yfinance_adapter import YFinanceAdapter
        yf = YFinanceAdapter()

        cn: list[MarketAdapter] = []
        hk: list[MarketAdapter] = []
        us: list[MarketAdapter] = []

        if settings.prefer_akshare_for_cn:
            try:
                from .adapters.akshare_adapter import AkshareAdapter
                ak = AkshareAdapter()
                cn.append(ak)
                hk.append(ak)
            except Exception as e:  # akshare not installed -> fall back to yf
                print(f"[datahub] akshare unavailable: {e}")

        if settings.finnhub_api_key:
            try:
                from .adapters.finnhub_adapter import FinnhubAdapter
                us.append(FinnhubAdapter(settings.finnhub_api_key))
            except Exception as e:
                print(f"[datahub] finnhub unavailable: {e}")

        # yfinance is the universal fallback for every market
        cn.append(yf); hk.append(yf); us.append(yf)
        self._adapters = {"US": us, "HK": hk, "CN": cn}
        self._built = True

    def adapters_for(self, market: str) -> list[MarketAdapter]:
        self._build()
        return self._adapters.get(market, [])

    async def get_quotes(self, symbols: list[str]) -> list[Quote]:
        """Fetch quotes for many symbols across markets, with per-market fallback."""
        self._build()
        by_market: dict[str, list[str]] = {}
        for s in symbols:
            try:
                m, _ = parse(s)
            except Exception:
                continue
            by_market.setdefault(m, []).append(s)

        results: dict[str, Quote] = {}
        tasks = [self._quotes_one_market(m, syms) for m, syms in by_market.items()]
        for chunk in await asyncio.gather(*tasks, return_exceptions=True):
            if isinstance(chunk, Exception):
                continue
            for q in chunk:
                results[q.symbol] = q
        # preserve input order
        ordered = [results[s] for s in symbols if s in results]

        # Fill display names the data vendor didn't provide (yfinance fast_info
        # has none) from the cached resolver; warm any still-unknown in the bg.
        from . import names
        missing: list[str] = []
        for q in ordered:
            if not q.name:
                nm = names.name_for(q.symbol)
                if nm:
                    q.name = nm
                else:
                    missing.append(q.symbol)
            if not q.long_name:
                q.long_name = names.long_name_for(q.symbol) or q.name
        if missing:
            asyncio.create_task(names.ensure_warm(missing))
        return ordered

    async def _quotes_one_market(self, market: str, symbols: list[str]) -> list[Quote]:
        remaining = set(symbols)
        out: list[Quote] = []
        for adapter in self.adapters_for(market):
            if not remaining:
                break
            try:
                got = await adapter.get_quotes(list(remaining))
            except Exception as e:
                print(f"[datahub] {adapter.name} get_quotes failed for {market}: {e}")
                continue
            for q in got:
                if q.symbol in remaining:
                    out.append(q)
                    remaining.discard(q.symbol)
        return out

    async def get_quote(self, symbol: str) -> Optional[Quote]:
        qs = await self.get_quotes([symbol])
        return qs[0] if qs else None

    async def get_history(self, symbol: str, days: int = 200, interval: str = "1d",
                          use_cache: bool = True) -> pd.DataFrame:
        """Fetch OHLCV with a persistent cache + serve-stale-on-failure, so the
        chart stays stable even when the upstream source flakes (akshare blocked,
        yfinance hiccup, off-hours)."""
        self._build()
        from .cache import history_cache, _trim
        # `days` is a calendar window; for daily/weekly/monthly it ~= row count so
        # trim by it, but for intraday a fixed row cap is correct (the adapter
        # already bounds the time window).
        daily = interval in ("1d", "5d", "1wk", "1mo", "3mo")
        cap = days if daily else 2400
        if use_cache:
            fresh = history_cache.get_fresh(symbol, interval, self._hist_ttl)
            if fresh is not None and not fresh.empty:
                return _trim(fresh, cap)
        try:
            market, _ = parse(symbol)
        except Exception:
            return pd.DataFrame()

        last_err: Optional[Exception] = None
        for adapter in self.adapters_for(market):
            try:
                df = await adapter.get_history(symbol, days=days, interval=interval)
            except Exception as e:
                last_err = e
                print(f"[datahub] {adapter.name} get_history failed for {symbol}: {e}")
                continue
            if df is not None and not df.empty:
                history_cache.put(symbol, interval, df)         # merge + persist
                merged = history_cache.get_stale(symbol, interval)
                if merged is None or merged.empty:
                    merged = df
                return _trim(merged, cap)

        # every adapter failed or returned empty -> last-known-good (any age)
        stale = history_cache.get_stale(symbol, interval)
        if stale is not None and not stale.empty:
            print(f"[datahub] serving STALE history for {symbol} "
                  f"(live fetch unavailable: {last_err})")
            return _trim(stale, cap)
        return pd.DataFrame()


datahub = DataHub()
