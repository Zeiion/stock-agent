"""Fundamentals snapshot per symbol (PE/PB/ROE/市值/股息率/增长/目标价...).

Primary source: yfinance Ticker.info (works for US, HK, and most CN via the
.SS/.SZ suffixes). Cached ~1h per symbol since fundamentals move slowly.
Feeds both the UI (基本面 panel) and the AI context — the deep-analysis
"基本面" analyst finally gets real numbers instead of "数据有限".

All functions degrade gracefully: missing fields -> None, total failure -> {}.
"""
from __future__ import annotations

import asyncio
import time
from typing import Any, Optional

from . import symbols

_CACHE: dict[str, tuple[float, dict]] = {}
_TTL = 3600.0

# yfinance info-key -> our field name (+ Chinese label used by the UI/AI prompt)
_FIELDS: list[tuple[str, str, str]] = [
    ("marketCap",               "market_cap",        "总市值"),
    ("trailingPE",              "pe",                "市盈率TTM"),
    ("forwardPE",               "forward_pe",        "预期市盈率"),
    ("priceToBook",             "pb",                "市净率"),
    ("dividendYield",           "dividend_yield",    "股息率"),
    ("returnOnEquity",          "roe",               "ROE"),
    ("profitMargins",           "profit_margin",     "净利率"),
    ("revenueGrowth",           "revenue_growth",    "营收增速"),
    ("earningsGrowth",          "earnings_growth",   "盈利增速"),
    ("debtToEquity",            "debt_to_equity",    "负债权益比"),
    ("freeCashflow",            "free_cashflow",     "自由现金流"),
    ("beta",                    "beta",              "Beta"),
    ("fiftyTwoWeekHigh",        "high_52w",          "52周最高"),
    ("fiftyTwoWeekLow",         "low_52w",           "52周最低"),
    ("targetMeanPrice",         "target_price",      "分析师目标价"),
    ("recommendationKey",       "recommendation",    "机构评级"),
]

LABELS = {ours: label for _, ours, label in _FIELDS}

# percent-style ratio fields (0.123 -> 12.3%). NOTE: dividendYield is excluded —
# yfinance >= 1.x already returns it as a percent (0.37 == 0.37%).
_PCT = {"roe", "profit_margin", "revenue_growth", "earnings_growth"}


def _num(v: Any) -> Optional[float]:
    if isinstance(v, bool) or v is None:
        return None
    try:
        f = float(v)
        return f if f == f else None       # NaN guard
    except (TypeError, ValueError):
        return None


def _fetch_blocking(symbol: str) -> dict[str, Any]:
    import yfinance as yf
    ticker = symbols.to_yfinance(symbol)
    info = yf.Ticker(ticker).info or {}
    out: dict[str, Any] = {}
    for yf_key, ours, _label in _FIELDS:
        v = info.get(yf_key)
        if ours == "recommendation":
            out[ours] = str(v) if v else None
            continue
        n = _num(v)
        if n is not None and ours in _PCT and abs(n) <= 1.5:
            n = round(n * 100, 2)          # ratio -> percent
        out[ours] = round(n, 4) if isinstance(n, float) else n
    # convenience: 52-week position (0=低点, 100=高点)
    hi, lo = out.get("high_52w"), out.get("low_52w")
    px = _num(info.get("currentPrice") or info.get("regularMarketPrice"))
    if hi and lo and px and hi > lo:
        out["pos_52w"] = round((px - lo) / (hi - lo) * 100, 1)
    out["sector"] = info.get("sector") or ""
    out["industry"] = info.get("industry") or ""
    return out


async def get_fundamentals(symbol: str) -> dict[str, Any]:
    sym = symbols.canonical(symbol)
    now = time.time()
    cached = _CACHE.get(sym)
    if cached and now - cached[0] < _TTL:
        return cached[1]
    try:
        data = await asyncio.to_thread(_fetch_blocking, sym)
    except Exception as e:
        print(f"[fundamentals] {sym} failed: {e}")
        data = cached[1] if cached else {}
    if data:
        _CACHE[sym] = (now, data)
    return data


def summarize_for_ai(f: dict[str, Any]) -> str:
    """Compact Chinese one-liner for the AI prompt; '' when no data."""
    if not f:
        return ""
    bits = []
    if f.get("sector"):
        bits.append(f"行业:{f['sector']}/{f.get('industry','')}")
    for key in ("market_cap", "pe", "forward_pe", "pb", "roe", "profit_margin",
                "revenue_growth", "dividend_yield", "beta", "pos_52w",
                "target_price", "recommendation"):
        v = f.get(key)
        if v is None or v == "":
            continue
        label = LABELS.get(key, key)
        if key == "market_cap":
            v = f"{v/1e9:.1f}B" if v >= 1e9 else f"{v/1e6:.0f}M"
        elif key in _PCT:
            v = f"{v}%"
        elif key == "pos_52w":
            label = "52周位置"
            v = f"{v}%"
        bits.append(f"{label}={v}")
    return " ".join(bits)
