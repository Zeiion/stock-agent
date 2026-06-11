"""Market news / headlines pipeline.

Merges headlines for a canonical symbol from multiple vendors into one uniform
list of plain dicts the API and AI brain can consume:

    {"title": str, "publisher": str, "ts": float, "link": str, "summary": str}

Sources (merged, deduped by normalized title, sorted newest-first, capped):
  1. yfinance ``yf.Ticker(tkr).news`` — works for US / HK / CN. Tolerates BOTH
     the older flat dict shape and the newer ``{content: {...}}`` shape.
  2. akshare ``ak.stock_news_em`` — China A-shares only, optional. Wrapped in
     try/except because this sandbox's proxy intermittently blocks eastmoney;
     code is still correct on a normal machine.

Design rules followed from the rest of the backend:
  * Every blocking vendor SDK call runs inside ``asyncio.to_thread``.
  * A single bad item / source never raises to the caller: it is caught,
    logged with ``print``, and the pipeline degrades gracefully (partial / []).
  * Missing fields default to "" / 0.0.
"""
from __future__ import annotations

import asyncio
import time
from datetime import datetime
from typing import Any, Optional

from . import symbols

try:  # yfinance is an optional dependency; import lazily-safe
    import yfinance as yf
except Exception:  # pragma: no cover - import guard
    yf = None


# --------------------------------------------------------------------------- #
# Small parsing helpers (never raise)
# --------------------------------------------------------------------------- #
def _str(val: Any) -> str:
    """Coerce anything to a stripped str; None / NaN -> ''."""
    if val is None:
        return ""
    try:
        if val != val:  # NaN guard (floats)
            return ""
    except Exception:
        pass
    try:
        return str(val).strip()
    except Exception:
        return ""


def _epoch_from_iso(val: Any) -> float:
    """Parse an ISO-8601 string (e.g. '2026-06-09T10:30:00Z') to epoch seconds."""
    s = _str(val)
    if not s:
        return 0.0
    # datetime.fromisoformat (3.10) doesn't accept a trailing 'Z'.
    if s.endswith("Z") or s.endswith("z"):
        s = s[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(s)
        return dt.timestamp()
    except Exception:
        return 0.0


def _epoch_from_naive(val: Any, fmt: str = "%Y-%m-%d %H:%M:%S") -> float:
    """Parse a naive local datetime string ('2026-06-09 10:30:00') to epoch."""
    s = _str(val)
    if not s:
        return 0.0
    try:
        return datetime.strptime(s, fmt).timestamp()
    except Exception:
        return 0.0


def _epoch_any(val: Any) -> float:
    """Best-effort epoch from an epoch number, an ISO string, or a naive string."""
    if val is None:
        return 0.0
    # Already an epoch number?
    if isinstance(val, (int, float)) and not isinstance(val, bool):
        try:
            f = float(val)
            if f != f:  # NaN
                return 0.0
            return f
        except Exception:
            return 0.0
    s = _str(val)
    if not s:
        return 0.0
    # Numeric string -> epoch
    try:
        return float(s)
    except ValueError:
        pass
    # ISO has a 'T' separator; naive snapshot uses a space.
    if "T" in s:
        ep = _epoch_from_iso(s)
        if ep:
            return ep
    ep = _epoch_from_naive(s)
    if ep:
        return ep
    return _epoch_from_iso(s)


def _norm_title(title: str) -> str:
    """Normalized dedup key: lowercase, collapse internal whitespace."""
    return " ".join(title.lower().split())


# --------------------------------------------------------------------------- #
# Source 1: yfinance (handles both flat + nested content shapes)
# --------------------------------------------------------------------------- #
def _parse_yf_item(raw: Any) -> Optional[dict]:
    """Map one yfinance news entry (either shape) to our item dict; None if empty."""
    if not isinstance(raw, dict):
        return None

    content = raw.get("content")
    if isinstance(content, dict):
        # Newer nested shape.
        title = _str(content.get("title"))
        summary = _str(content.get("summary")) or _str(content.get("description"))

        provider = content.get("provider")
        publisher = ""
        if isinstance(provider, dict):
            publisher = _str(provider.get("displayName")) or _str(provider.get("name"))
        else:
            publisher = _str(provider)

        # Link: canonicalUrl / clickThroughUrl are {"url": "..."} objects.
        link = ""
        for key in ("canonicalUrl", "clickThroughUrl"):
            obj = content.get(key)
            if isinstance(obj, dict):
                link = _str(obj.get("url"))
            elif obj:
                link = _str(obj)
            if link:
                break

        ts = _epoch_any(content.get("pubDate"))
        if not ts:
            ts = _epoch_any(content.get("displayTime"))
    else:
        # Older flat shape.
        title = _str(raw.get("title"))
        summary = _str(raw.get("summary")) or _str(raw.get("description"))
        publisher = _str(raw.get("publisher"))
        link = _str(raw.get("link"))
        ts = _epoch_any(raw.get("providerPublishTime"))
        if not ts:
            ts = _epoch_any(raw.get("pubDate"))

    if not title and not link:
        return None
    return {
        "title": title,
        "publisher": publisher,
        "ts": ts,
        "link": link,
        "summary": summary,
    }


def _fetch_yf_news(ticker: str) -> list[dict]:
    """Blocking yfinance news fetch; runs inside asyncio.to_thread."""
    if yf is None:
        return []
    try:
        raw_items = yf.Ticker(ticker).news
    except Exception as e:
        print(f"[news] yfinance news failed for {ticker}: {e}")
        return []
    if not raw_items:
        return []

    out: list[dict] = []
    for raw in raw_items:
        try:
            item = _parse_yf_item(raw)
        except Exception as e:
            print(f"[news] yfinance item parse failed for {ticker}: {e}")
            continue
        if item:
            out.append(item)
    return out


# --------------------------------------------------------------------------- #
# Source 2: akshare China A-share (optional; eastmoney may be blocked)
# --------------------------------------------------------------------------- #
# akshare renames its (Chinese) columns between releases, so look each value up
# through an ordered list of aliases rather than hard-coding one name.
_AK_COLS = {
    "title": ("新闻标题", "标题"),
    "ts": ("发布时间", "时间", "日期"),
    "link": ("新闻链接", "链接", "url"),
    "publisher": ("文章来源", "来源", "媒体"),
    "summary": ("新闻内容", "内容", "摘要"),
}


def _ak_cell(row: dict, candidates: tuple[str, ...]) -> Any:
    for name in candidates:
        if name in row:
            return row[name]
    return None


def _fetch_cn_news(code: str) -> list[dict]:
    """Blocking akshare A-share news fetch; runs inside asyncio.to_thread.

    Returns [] (never raises) if akshare is missing, blocked, or schema-drifted.
    """
    try:
        import akshare as ak
    except Exception as e:
        print(f"[news] akshare unavailable: {e}")
        return []

    try:
        df = ak.stock_news_em(symbol=code)
    except Exception as e:
        print(f"[news] akshare stock_news_em failed for {code}: {e}")
        return []

    if df is None or getattr(df, "empty", True):
        return []

    out: list[dict] = []
    try:
        records = df.to_dict("records")
    except Exception as e:
        print(f"[news] akshare frame->records failed for {code}: {e}")
        return []

    for row in records:
        try:
            title = _str(_ak_cell(row, _AK_COLS["title"]))
            link = _str(_ak_cell(row, _AK_COLS["link"]))
            if not title and not link:
                continue
            out.append({
                "title": title,
                "publisher": _str(_ak_cell(row, _AK_COLS["publisher"])),
                "ts": _epoch_any(_ak_cell(row, _AK_COLS["ts"])),
                "link": link,
                "summary": _str(_ak_cell(row, _AK_COLS["summary"])),
            })
        except Exception as e:
            print(f"[news] akshare row parse failed for {code}: {e}")
            continue
    return out


# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #
async def get_news(symbol: str, limit: int = 10) -> list[dict]:
    """Fetch merged, deduped, newest-first news headlines for a symbol.

    Args:
        symbol: canonical "MARKET:CODE" (bare codes are tolerated via symbols.parse).
        limit:  max number of items to return.

    Returns:
        list of {"title", "publisher", "ts", "link", "summary"} dicts; [] on
        total failure. Never raises for a single bad source/item.
    """
    if limit <= 0:
        return []

    # Resolve the symbol once; degrade to [] if it's unparseable.
    try:
        canon = symbols.canonical(symbol)
        market, _ = symbols.parse(canon)
    except Exception as e:
        print(f"[news] bad symbol {symbol!r}: {e}")
        return []

    tasks = []

    # Source 1: yfinance (all markets).
    try:
        tkr = symbols.to_yfinance(canon)
        tasks.append(asyncio.to_thread(_fetch_yf_news, tkr))
    except Exception as e:
        print(f"[news] to_yfinance failed for {canon}: {e}")

    # Source 2: akshare A-share news (CN only, optional).
    if market == "CN":
        try:
            cn_code = symbols.to_akshare_cn(canon)
            tasks.append(asyncio.to_thread(_fetch_cn_news, cn_code))
        except Exception as e:
            print(f"[news] to_akshare_cn failed for {canon}: {e}")

    if not tasks:
        return []

    merged: list[dict] = []
    for chunk in await asyncio.gather(*tasks, return_exceptions=True):
        if isinstance(chunk, Exception):
            print(f"[news] source failed for {canon}: {chunk}")
            continue
        if chunk:
            merged.extend(chunk)

    # Dedup by normalized title, keeping the entry with the richer payload
    # (prefer a non-empty summary, then a non-empty link, then a real ts).
    by_key: dict[str, dict] = {}
    for item in merged:
        key = _norm_title(item.get("title", ""))
        if not key:
            # No usable title; fall back to the link as the dedup key.
            key = _str(item.get("link"))
            if not key:
                continue
        existing = by_key.get(key)
        if existing is None:
            by_key[key] = item
            continue
        # Keep whichever item carries more information.
        better = (
            (1 if item.get("summary") else 0),
            (1 if item.get("link") else 0),
            (1 if item.get("ts") else 0),
        )
        cur = (
            (1 if existing.get("summary") else 0),
            (1 if existing.get("link") else 0),
            (1 if existing.get("ts") else 0),
        )
        if better > cur:
            by_key[key] = item

    deduped = list(by_key.values())
    deduped.sort(key=lambda it: it.get("ts") or 0.0, reverse=True)
    return deduped[:limit]
