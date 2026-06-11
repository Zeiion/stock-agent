"""Finnhub market data adapter (US real-time, requires an API key).

Finnhub's free tier serves real-time US equity quotes via /quote but gates
historical stock candles behind a paid plan. We therefore best-effort the
candle endpoint and return an EMPTY DataFrame on any failure so the DataHub
transparently falls back to yfinance for history.

Canonical symbols are "MARKET:CODE"; for US the Finnhub ticker is the bare
code part (e.g. "US:AAPL" -> "AAPL"). See app.symbols for parsing.
"""
from __future__ import annotations

import asyncio
import time

import httpx
import pandas as pd

from .. import symbols
from ..models import CURRENCY_BY_MARKET, Quote
from .base import MarketAdapter

_BASE_URL = "https://finnhub.io/api/v1"
_TIMEOUT = httpx.Timeout(10.0)


class FinnhubAdapter(MarketAdapter):
    name = "finnhub"

    def __init__(self, api_key: str):
        self.api_key = api_key or ""

    def supports(self, market: str) -> bool:
        return market == "US" and bool(self.api_key)

    async def get_quotes(self, symbols_: list[str]) -> list[Quote]:
        if not self.api_key or not symbols_:
            return []

        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            results = await asyncio.gather(
                *(self._fetch_quote(client, sym) for sym in symbols_),
                return_exceptions=True,
            )

        quotes: list[Quote] = []
        for res in results:
            if isinstance(res, Quote):
                quotes.append(res)
            # exceptions / None -> skip per-symbol failure
        return quotes

    async def _fetch_quote(
        self, client: httpx.AsyncClient, symbol: str
    ) -> Quote | None:
        try:
            market, code = symbols.parse(symbol)
            if market != "US":
                return None
            resp = await client.get(
                f"{_BASE_URL}/quote",
                params={"symbol": code, "token": self.api_key},
            )
            if resp.status_code != 200:
                return None
            data = resp.json()
            last = data.get("c")
            prev_close = data.get("pc")
            if last is None or prev_close is None:
                return None
            # Finnhub returns 0s for unknown/invalid tickers.
            if float(last) == 0.0 and float(prev_close) == 0.0:
                return None
            return Quote(
                symbol=f"{market}:{code}",
                market="US",
                last=float(last),
                prev_close=float(prev_close),
                name="",
                open=float(data.get("o") or 0.0),
                high=float(data.get("h") or 0.0),
                low=float(data.get("l") or 0.0),
                volume=0.0,
                currency=CURRENCY_BY_MARKET["US"],
                ts=time.time(),
                source="finnhub",
                delayed=False,
            )
        except Exception:
            return None

    async def get_history(
        self,
        symbol: str,
        days: int = 200,
        interval: str = "1d",
    ) -> pd.DataFrame:
        empty = pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
        if not self.api_key:
            return empty
        try:
            market, code = symbols.parse(symbol)
            if market != "US":
                return empty
            now = int(time.time())
            frm = now - int(days) * 86400
            async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
                resp = await client.get(
                    f"{_BASE_URL}/stock/candle",
                    params={
                        "symbol": code,
                        "resolution": "D",
                        "from": frm,
                        "to": now,
                        "token": self.api_key,
                    },
                )
            if resp.status_code != 200:
                return empty
            data = resp.json()
            if data.get("s") != "ok":
                return empty
            ts = data.get("t") or []
            if not ts:
                return empty
            df = pd.DataFrame(
                {
                    "open": data.get("o", []),
                    "high": data.get("h", []),
                    "low": data.get("l", []),
                    "close": data.get("c", []),
                    "volume": data.get("v", []),
                },
                index=pd.to_datetime(ts, unit="s"),
            )
            df = df[["open", "high", "low", "close", "volume"]].sort_index()
            return df
        except Exception:
            return empty
