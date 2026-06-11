"""Market data adapter contract.

Every data source implements MarketAdapter. The DataHub picks an adapter per
market and falls back to the next on failure. Adapters are async-friendly but
may run blocking vendor SDK calls inside `asyncio.to_thread` — the DataHub calls
them with `await`.

Concrete adapters live alongside this file:
    yfinance_adapter.YFinanceAdapter   (US/HK/CN, delayed, no key)
    akshare_adapter.AkshareAdapter     (CN near-real-time, no key)
    finnhub_adapter.FinnhubAdapter     (US real-time, needs key)  [optional]
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Optional

import pandas as pd

from ..models import Quote


class MarketAdapter(ABC):
    #: short id, e.g. "yfinance"
    name: str = "base"

    @abstractmethod
    def supports(self, market: str) -> bool:
        """Whether this adapter can serve the given market ('US'|'HK'|'CN')."""

    @abstractmethod
    async def get_quotes(self, symbols: list[str]) -> list[Quote]:
        """Return current quotes for the given canonical symbols.

        Should never raise for a single bad symbol — skip it and return what it
        can. Raising is reserved for total source failure (triggers fallback).
        """

    @abstractmethod
    async def get_history(
        self,
        symbol: str,
        days: int = 200,
        interval: str = "1d",
    ) -> pd.DataFrame:
        """Return a chronological OHLCV DataFrame indexed by timestamp with
        columns: open, high, low, close, volume. Empty DataFrame if unavailable.
        """

    async def close(self) -> None:  # optional cleanup hook
        return None
