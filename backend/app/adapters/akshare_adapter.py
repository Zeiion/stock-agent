"""akshare market data adapter (China A-shares + Hong Kong).

akshare exposes near-real-time snapshots and historical OHLCV for the CN and HK
markets without any API key. Every `ak.*` call is synchronous/blocking and may do
network I/O, so each one is wrapped in `asyncio.to_thread` to keep the event loop
responsive.

Defensive note: akshare frequently renames its (Chinese) DataFrame columns between
releases. This module never hard-codes a single column name — it looks each value
up through an ordered list of best-known aliases and skips a row/symbol gracefully
when a required column is missing, so one bad symbol or schema drift never crashes
the whole batch.
"""
from __future__ import annotations

import asyncio
import time
from datetime import datetime, timedelta
from typing import Any, Optional

import pandas as pd

import akshare as ak

from .base import MarketAdapter
from ..models import Quote
from .. import symbols


# Snapshot cache TTL: the CN/HK whole-market spot frames are heavy, so reuse a
# fetched frame for a few seconds across symbols inside one polling cycle.
_SNAPSHOT_TTL_S = 4.0


def _first_col(df: pd.DataFrame, *names: str) -> Optional[str]:
    """Return the first column name present in `df` from the given aliases."""
    cols = set(df.columns)
    for n in names:
        if n in cols:
            return n
    return None


def _to_float(value: Any) -> Optional[float]:
    """Coerce an akshare cell to float, returning None on failure / NaN / blank."""
    if value is None:
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    if f != f:  # NaN
        return None
    return f


class AkshareAdapter(MarketAdapter):
    name = "akshare"

    def __init__(self) -> None:
        # (epoch_ts, DataFrame) per snapshot kind; reused within _SNAPSHOT_TTL_S.
        self._cn_snapshot: Optional[tuple[float, pd.DataFrame]] = None
        self._hk_snapshot: Optional[tuple[float, pd.DataFrame]] = None

    # ------------------------------------------------------------------ #
    def supports(self, market: str) -> bool:
        return market in ("CN", "HK")

    # ------------------------------------------------------------------ #
    async def _cn_spot(self) -> pd.DataFrame:
        cached = self._cn_snapshot
        now = time.time()
        if cached is not None and (now - cached[0]) < _SNAPSHOT_TTL_S:
            return cached[1]
        df = await asyncio.to_thread(ak.stock_zh_a_spot_em)
        self._cn_snapshot = (time.time(), df)
        return df

    async def _hk_spot(self) -> pd.DataFrame:
        cached = self._hk_snapshot
        now = time.time()
        if cached is not None and (now - cached[0]) < _SNAPSHOT_TTL_S:
            return cached[1]
        df = await asyncio.to_thread(ak.stock_hk_spot_em)
        self._hk_snapshot = (time.time(), df)
        return df

    # ------------------------------------------------------------------ #
    async def get_quotes(self, symbols_: list[str]) -> list[Quote]:
        # Split requested canonical symbols by market.
        cn_map: dict[str, str] = {}   # 6-digit code -> canonical symbol
        hk_map: dict[str, str] = {}   # 5-digit code -> canonical symbol
        for sym in symbols_:
            try:
                market, _ = symbols.parse(sym)
            except Exception:
                continue
            if market == "CN":
                try:
                    cn_map[symbols.to_akshare_cn(sym)] = symbols.canonical(sym)
                except Exception:
                    continue
            elif market == "HK":
                try:
                    hk_map[symbols.to_akshare_hk(sym)] = symbols.canonical(sym)
                except Exception:
                    continue

        out: list[Quote] = []
        if cn_map:
            out.extend(await self._quotes_cn(cn_map))
        if hk_map:
            out.extend(await self._quotes_hk(hk_map))
        return out

    async def _quotes_cn(self, code_map: dict[str, str]) -> list[Quote]:
        try:
            df = await self._cn_spot()
        except Exception:
            return []
        return self._build_quotes(
            df, code_map, market="CN", delayed=False, code_width=6,
        )

    async def _quotes_hk(self, code_map: dict[str, str]) -> list[Quote]:
        try:
            df = await self._hk_spot()
        except Exception:
            return []
        return self._build_quotes(
            df, code_map, market="HK", delayed=True, code_width=5,
        )

    def _build_quotes(
        self,
        df: pd.DataFrame,
        code_map: dict[str, str],
        market: str,
        delayed: bool,
        code_width: int,
    ) -> list[Quote]:
        if df is None or df.empty:
            return []

        col_code = _first_col(df, "代码", "symbol", "code")
        if col_code is None:
            return []
        col_name = _first_col(df, "名称", "name")
        col_last = _first_col(df, "最新价", "现价", "最新")
        col_prev = _first_col(df, "昨收", "昨收价", "前收盘", "昨日收盘")
        col_open = _first_col(df, "今开", "开盘", "开盘价")
        col_high = _first_col(df, "最高", "最高价")
        col_low = _first_col(df, "最低", "最低价")
        col_vol = _first_col(df, "成交量", "volume")

        # last + prev_close are required to form a meaningful Quote.
        if col_last is None or col_prev is None:
            return []

        # Normalize the snapshot codes once for matching (zero-pad to expected width).
        try:
            codes_norm = df[col_code].astype(str).str.extract(r"(\d+)", expand=False)
        except Exception:
            codes_norm = df[col_code].astype(str)
        codes_norm = codes_norm.fillna("").str.zfill(code_width)

        quotes: list[Quote] = []
        for idx, raw_code in codes_norm.items():
            canon = code_map.get(raw_code)
            if canon is None:
                continue
            row = df.loc[idx]
            last = _to_float(row.get(col_last))
            prev = _to_float(row.get(col_prev))
            if last is None or prev is None:
                continue
            name = ""
            if col_name is not None:
                nm = row.get(col_name)
                name = "" if nm is None else str(nm)
            quotes.append(
                Quote(
                    symbol=canon,
                    market=market,
                    last=last,
                    prev_close=prev,
                    name=name,
                    open=_to_float(row.get(col_open)) or 0.0 if col_open else 0.0,
                    high=_to_float(row.get(col_high)) or 0.0 if col_high else 0.0,
                    low=_to_float(row.get(col_low)) or 0.0 if col_low else 0.0,
                    volume=_to_float(row.get(col_vol)) or 0.0 if col_vol else 0.0,
                    source="akshare",
                    delayed=delayed,
                )
            )
        return quotes

    # ------------------------------------------------------------------ #
    async def get_history(
        self,
        symbol: str,
        days: int = 200,
        interval: str = "1d",
    ) -> pd.DataFrame:
        empty = pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
        try:
            market, _ = symbols.parse(symbol)
        except Exception:
            return empty

        if market not in ("CN", "HK"):
            return empty

        # akshare daily history takes YYYYMMDD bounds. Pad the window generously so
        # holidays/weekends still leave roughly `days` trading bars.
        end = datetime.now()
        start = end - timedelta(days=max(days, 1) * 2 + 30)
        start_date = start.strftime("%Y%m%d")
        end_date = end.strftime("%Y%m%d")

        try:
            if market == "CN":
                code = symbols.to_akshare_cn(symbol)
                df = await asyncio.to_thread(
                    ak.stock_zh_a_hist,
                    symbol=code,
                    period="daily",
                    start_date=start_date,
                    end_date=end_date,
                    adjust="qfq",
                )
            else:  # HK
                code = symbols.to_akshare_hk(symbol)
                df = await asyncio.to_thread(
                    ak.stock_hk_hist,
                    symbol=code,
                    period="daily",
                    start_date=start_date,
                    end_date=end_date,
                    adjust="qfq",
                )
        except Exception:
            return empty

        if df is None or len(df) == 0:
            return empty

        try:
            return self._normalize_history(df, days)
        except Exception:
            return empty

    def _normalize_history(self, df: pd.DataFrame, days: int) -> pd.DataFrame:
        empty = pd.DataFrame(columns=["open", "high", "low", "close", "volume"])

        col_date = _first_col(df, "日期", "date", "时间")
        col_open = _first_col(df, "开盘", "开盘价", "open")
        col_high = _first_col(df, "最高", "最高价", "high")
        col_low = _first_col(df, "最低", "最低价", "low")
        col_close = _first_col(df, "收盘", "收盘价", "close")
        col_vol = _first_col(df, "成交量", "volume")

        if col_date is None or col_close is None:
            return empty

        out = pd.DataFrame()
        out["open"] = pd.to_numeric(df[col_open], errors="coerce") if col_open else pd.NA
        out["high"] = pd.to_numeric(df[col_high], errors="coerce") if col_high else pd.NA
        out["low"] = pd.to_numeric(df[col_low], errors="coerce") if col_low else pd.NA
        out["close"] = pd.to_numeric(df[col_close], errors="coerce")
        out["volume"] = pd.to_numeric(df[col_vol], errors="coerce") if col_vol else 0.0

        idx = pd.to_datetime(df[col_date], errors="coerce")
        out.index = idx
        out = out[~out.index.isna()]
        out = out.dropna(subset=["close"])
        out = out.sort_index()

        if days and len(out) > days:
            out = out.iloc[-days:]
        return out
