"""Persistent OHLCV history cache.

Keeps K-line data stable when the upstream source is flaky (akshare blocked,
yfinance hiccup, rate-limit, off-hours). Strategy:

  - in-memory layer with a short TTL for "fresh"
  - on-disk CSV per symbol+interval (survives restarts) that only ever GROWS
    (each fetch is merged into the union of all bars ever seen)
  - serve-stale-on-failure: if a live fetch fails or returns empty, the DataHub
    falls back to the last-known-good cached frame instead of an empty chart.
"""
from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Optional

import pandas as pd

from .config import settings

_LOCK = threading.RLock()


def _trim(df: pd.DataFrame, days: int) -> pd.DataFrame:
    if df is None or df.empty or not days or len(df) <= days:
        return df
    return df.tail(days)


class HistoryCache:
    def __init__(self) -> None:
        self.dir = Path(settings.db_path).parent / "cache"
        self.dir.mkdir(parents=True, exist_ok=True)
        self._mem: dict[str, tuple[float, pd.DataFrame]] = {}

    def _key(self, symbol: str, interval: str) -> str:
        return f"{symbol.replace(':', '_')}__{interval}"

    def _path(self, symbol: str, interval: str) -> Path:
        return self.dir / f"{self._key(symbol, interval)}.csv"

    def get_fresh(self, symbol: str, interval: str, ttl: float
                  ) -> Optional[pd.DataFrame]:
        """Return the cached frame only if newer than ttl seconds."""
        ent = self._mem.get(self._key(symbol, interval))
        if ent and time.time() - ent[0] < ttl:
            return ent[1]
        return None

    def get_stale(self, symbol: str, interval: str) -> Optional[pd.DataFrame]:
        """Return the last-known-good frame at ANY age (memory, then disk)."""
        k = self._key(symbol, interval)
        ent = self._mem.get(k)
        if ent is not None:
            return ent[1]
        p = self._path(symbol, interval)
        if p.exists():
            try:
                df = pd.read_csv(p, index_col=0, parse_dates=True)
                if not df.empty:
                    with _LOCK:
                        self._mem[k] = (p.stat().st_mtime, df)
                    return df
            except Exception as e:
                print(f"[cache] read {p.name} failed: {e}")
        return None

    def put(self, symbol: str, interval: str, df: pd.DataFrame) -> None:
        """Merge a freshly-fetched frame into the cache (union of bars, newest
        values win on overlap) so the cache only ever grows / self-heals."""
        if df is None or df.empty:
            return
        k = self._key(symbol, interval)
        merged = df
        existing = self.get_stale(symbol, interval)
        if existing is not None and not existing.empty:
            try:
                merged = pd.concat([existing, df])
                merged = merged[~merged.index.duplicated(keep="last")].sort_index()
            except Exception:
                merged = df
        with _LOCK:
            self._mem[k] = (time.time(), merged)
        try:
            merged.to_csv(self._path(symbol, interval))
        except Exception as e:
            print(f"[cache] write {k} failed: {e}")


history_cache = HistoryCache()
