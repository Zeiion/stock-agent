"""yfinance market data adapter.

Covers US / HK / CN markets with delayed quotes and no API key. Blocking
yfinance SDK calls are wrapped in ``asyncio.to_thread`` so this adapter is safe
to ``await`` from the DataHub. It never raises for a single bad symbol — bad
symbols are simply skipped.
"""
from __future__ import annotations

import asyncio
import time

import pandas as pd

from .. import symbols as sym
from ..models import Quote
from .base import MarketAdapter

try:  # yfinance is an optional dependency; import lazily-safe
    import yfinance as yf
except Exception:  # pragma: no cover - import guard
    yf = None


# Ordered fast_info field name candidates (yfinance has renamed these over time).
_FAST_FIELDS = {
    "last": ("last_price", "lastPrice"),
    "prev_close": ("previous_close", "previousClose", "regular_market_previous_close"),
    "open": ("open", "regular_market_open"),
    "high": ("day_high", "dayHigh", "regular_market_day_high"),
    "low": ("day_low", "dayLow", "regular_market_day_low"),
    "volume": ("last_volume", "lastVolume", "regular_market_volume"),
}


def _fast_get(fast_info, candidates: tuple[str, ...]):
    """Best-effort read from a yfinance fast_info object (dict-like or attr-like)."""
    for key in candidates:
        # dict-like access first (FastInfo supports .get / __getitem__)
        try:
            val = fast_info.get(key)  # type: ignore[attr-defined]
            if val is not None:
                return val
        except Exception:
            pass
        # attribute access fallback
        try:
            val = getattr(fast_info, key)
            if val is not None:
                return val
        except Exception:
            pass
    return None


def _to_float(val) -> float:
    try:
        if val is None:
            return 0.0
        f = float(val)
        if f != f:  # NaN guard
            return 0.0
        return f
    except (TypeError, ValueError):
        return 0.0


class YFinanceAdapter(MarketAdapter):
    name = "yfinance"

    def supports(self, market: str) -> bool:
        return market in ("US", "HK", "CN")

    # ------------------------------------------------------------------ #
    # Quotes
    # ------------------------------------------------------------------ #
    async def get_quotes(self, symbols: list[str]) -> list[Quote]:
        if yf is None:
            raise RuntimeError("yfinance is not installed")
        results = await asyncio.gather(
            *(self._one_quote(s) for s in symbols),
            return_exceptions=True,
        )
        return [q for q in results if isinstance(q, Quote)]

    async def _one_quote(self, symbol: str) -> Quote | None:
        try:
            canon = sym.canonical(symbol)
            market, _ = sym.parse(canon)
            ticker = sym.to_yfinance(canon)
        except Exception:
            return None

        try:
            return await asyncio.to_thread(self._fetch_quote, canon, market, ticker)
        except Exception:
            return None

    def _fetch_quote(self, canon: str, market: str, ticker: str) -> Quote | None:
        """Blocking quote fetch; runs inside asyncio.to_thread."""
        t = yf.Ticker(ticker)

        last = prev_close = open_ = high = low = volume = 0.0
        try:
            fi = t.fast_info
            last = _to_float(_fast_get(fi, _FAST_FIELDS["last"]))
            prev_close = _to_float(_fast_get(fi, _FAST_FIELDS["prev_close"]))
            open_ = _to_float(_fast_get(fi, _FAST_FIELDS["open"]))
            high = _to_float(_fast_get(fi, _FAST_FIELDS["high"]))
            low = _to_float(_fast_get(fi, _FAST_FIELDS["low"]))
            volume = _to_float(_fast_get(fi, _FAST_FIELDS["volume"]))
        except Exception:
            pass

        # Fall back to a short history if fast_info lacked the essentials.
        if last <= 0.0 or prev_close <= 0.0:
            try:
                hist = t.history(period="2d", interval="1d", auto_adjust=False)
                if hist is not None and not hist.empty:
                    closes = hist["Close"].dropna()
                    if len(closes) >= 1 and last <= 0.0:
                        last = _to_float(closes.iloc[-1])
                    if len(closes) >= 2 and prev_close <= 0.0:
                        prev_close = _to_float(closes.iloc[-2])
                    elif prev_close <= 0.0 and "Open" in hist:
                        prev_close = _to_float(hist["Open"].dropna().iloc[-1])
                    last_row = hist.iloc[-1]
                    if open_ <= 0.0 and "Open" in hist:
                        open_ = _to_float(last_row.get("Open"))
                    if high <= 0.0 and "High" in hist:
                        high = _to_float(last_row.get("High"))
                    if low <= 0.0 and "Low" in hist:
                        low = _to_float(last_row.get("Low"))
                    if volume <= 0.0 and "Volume" in hist:
                        volume = _to_float(last_row.get("Volume"))
            except Exception:
                pass

        if last <= 0.0:
            return None
        if prev_close <= 0.0:
            prev_close = last

        name = ""
        try:
            name = str(_fast_get(t.fast_info, ("shortName", "short_name", "longName")) or "")
        except Exception:
            name = ""

        return Quote(
            symbol=canon,
            market=market,
            last=last,
            prev_close=prev_close,
            name=name,
            open=open_,
            high=high,
            low=low,
            volume=volume,
            currency="",  # Quote.to_dict fills from market
            ts=time.time(),
            source="yfinance",
            delayed=(market != "US"),
        )

    # ------------------------------------------------------------------ #
    # History
    # ------------------------------------------------------------------ #
    async def get_history(
        self,
        symbol: str,
        days: int = 200,
        interval: str = "1d",
    ) -> pd.DataFrame:
        if yf is None:
            return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
        try:
            canon = sym.canonical(symbol)
            ticker = sym.to_yfinance(canon)
        except Exception:
            return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
        try:
            return await asyncio.to_thread(self._fetch_history, ticker, days, interval)
        except Exception:
            return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])

    def _fetch_history(self, ticker: str, days: int, interval: str) -> pd.DataFrame:
        empty = pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
        t = yf.Ticker(ticker)

        kwargs: dict = {"interval": interval, "auto_adjust": False}
        if interval in ("1d", "5d", "1wk", "1mo", "3mo"):
            # daily/weekly/monthly: bound by the requested day window
            today = pd.Timestamp.utcnow().normalize()
            start = (today - pd.Timedelta(days=max(int(days), 1))).date()
            end = (today + pd.Timedelta(days=1)).date()
            kwargs["start"] = start
            kwargs["end"] = end
        else:
            # Intraday windows are capped by Yahoo; pick the largest legal period.
            if interval in ("1m", "2m"):
                kwargs["period"] = "5d"      # 1m max ~7d
            elif interval == "5m":
                kwargs["period"] = "1mo"     # 5m max ~60d
            else:                            # 15m/30m/60m/90m/1h
                kwargs["period"] = "60d"

        df = t.history(**kwargs)
        if df is None or df.empty:
            return empty

        rename = {
            "Open": "open",
            "High": "high",
            "Low": "low",
            "Close": "close",
            "Volume": "volume",
        }
        df = df.rename(columns=rename)
        cols = ["open", "high", "low", "close", "volume"]
        have = [c for c in cols if c in df.columns]
        if not have:
            return empty
        out = df[have].copy()
        for c in cols:
            if c not in out.columns:
                out[c] = 0.0
        out = out[cols]
        out = out.sort_index(ascending=True)
        out = out.dropna(subset=["close"])
        return out
