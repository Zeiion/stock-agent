"""Market overview: major indices + risk gauges (free via yfinance).

Gives the platform (and the AI briefing) cross-market context: 美股三大指数,
VIX, 恒指, 沪深主要指数, 美元/人民币, 黄金, BTC. Cached ~60s.
"""
from __future__ import annotations

import asyncio
import time
from typing import Any

_INDICES: list[tuple[str, str, str]] = [
    # (yfinance ticker, 中文名, group)
    ("^GSPC",     "标普500",   "US"),
    ("^IXIC",     "纳斯达克",  "US"),
    ("^DJI",      "道琼斯",    "US"),
    ("^VIX",      "VIX恐慌",   "US"),
    ("^HSI",      "恒生指数",  "HK"),
    ("^HSTECH",   "恒生科技",  "HK"),
    ("000001.SS", "上证指数",  "CN"),
    ("399001.SZ", "深证成指",  "CN"),
    ("399006.SZ", "创业板指",  "CN"),
    ("CNY=X",     "美元/人民币", "FX"),
    ("GC=F",      "黄金",      "CMDTY"),
    ("BTC-USD",   "比特币",    "CRYPTO"),
]

_CACHE: dict[str, tuple[float, Any]] = {}
_TTL = 60.0


def _fetch_blocking() -> list[dict]:
    import yfinance as yf
    out: list[dict] = []
    for ticker, name, group in _INDICES:
        try:
            fi = yf.Ticker(ticker).fast_info
            last = getattr(fi, "last_price", None)
            prev = getattr(fi, "previous_close", None)
            if last is None or prev is None or not prev:
                continue
            out.append({
                "ticker": ticker, "name": name, "group": group,
                "last": round(float(last), 2),
                "change_pct": round((float(last) - float(prev)) / float(prev) * 100, 2),
            })
        except Exception:
            continue
    return out


async def get_market_overview() -> dict:
    ent = _CACHE.get("mkt")
    if ent and time.time() - ent[0] < _TTL:
        return ent[1]
    from .social import get_fear_greed
    rows, fg = await asyncio.gather(
        asyncio.to_thread(_fetch_blocking), get_fear_greed(),
        return_exceptions=True)
    rows = rows if isinstance(rows, list) else []
    fg = fg if isinstance(fg, dict) else {}
    data = {"indices": rows, "fear_greed": fg, "ts": time.time()}
    if rows:
        _CACHE["mkt"] = (time.time(), data)
    return data
