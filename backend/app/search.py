"""Cross-market symbol search for the watchlist add box (US / HK / CN).

Layered so it degrades gracefully:
  1. direct code         — if the query parses as a symbol, always offer it
  2. built-in popular list — bilingual, instant, offline (大盘股)
  3. akshare name lists   — full A-share + HK Chinese-name search (your machine)
  4. yfinance Search      — US / English names

Results are merged + de-duped by canonical symbol, popular/direct first.
"""
from __future__ import annotations

import asyncio
import time
from typing import Any, Optional

from .symbols import canonical, from_yfinance, parse

_US_EXCHANGES = {"NMS", "NGM", "NYQ", "NCM", "ASE", "PCX", "BATS", "OQB", "OQX", "NYS"}

# --------------------------------------------------------------------------- #
# Built-in popular stocks (instant, offline). name carries 中文 + English so a
# substring match works for either. Keep this curated and small.
# --------------------------------------------------------------------------- #
POPULAR: list[tuple[str, str]] = [
    # US
    ("US:AAPL", "Apple 苹果"), ("US:NVDA", "NVIDIA 英伟达"), ("US:TSLA", "Tesla 特斯拉"),
    ("US:MSFT", "Microsoft 微软"), ("US:GOOGL", "Alphabet 谷歌"), ("US:AMZN", "Amazon 亚马逊"),
    ("US:META", "Meta 脸书"), ("US:AMD", "AMD 超威"), ("US:NFLX", "Netflix 奈飞"),
    ("US:COIN", "Coinbase"), ("US:AVGO", "Broadcom 博通"), ("US:PLTR", "Palantir"),
    ("US:BABA", "Alibaba 阿里巴巴(美)"), ("US:PDD", "拼多多 PDD"), ("US:TSM", "台积电 TSMC"),
    # HK
    ("HK:00700", "腾讯控股 Tencent"), ("HK:09988", "阿里巴巴 Alibaba"), ("HK:03690", "美团 Meituan"),
    ("HK:09618", "京东集团 JD"), ("HK:01810", "小米集团 Xiaomi"), ("HK:00941", "中国移动"),
    ("HK:00939", "建设银行"), ("HK:01299", "友邦保险 AIA"), ("HK:02318", "中国平安(港)"),
    ("HK:09888", "百度集团 Baidu"), ("HK:01024", "快手 Kuaishou"), ("HK:02020", "安踏体育"),
    ("HK:00388", "香港交易所"), ("HK:01211", "比亚迪股份 BYD"), ("HK:02015", "理想汽车 Li Auto"),
    ("HK:09868", "小鹏汽车 XPeng"), ("HK:09866", "蔚来 NIO"), ("HK:00005", "汇丰控股 HSBC"),
    ("HK:03988", "中国银行"), ("HK:01398", "工商银行"),
    # CN A-share
    ("CN:600519", "贵州茅台"), ("CN:300750", "宁德时代 CATL"), ("CN:601318", "中国平安"),
    ("CN:000858", "五粮液"), ("CN:002594", "比亚迪"), ("CN:600036", "招商银行"),
    ("CN:601899", "紫金矿业"), ("CN:000333", "美的集团"), ("CN:600900", "长江电力"),
    ("CN:002415", "海康威视"), ("CN:688981", "中芯国际"), ("CN:601012", "隆基绿能"),
    ("CN:600276", "恒瑞医药"), ("CN:000001", "平安银行"), ("CN:600030", "中信证券"),
    ("CN:601888", "中国中免"), ("CN:002230", "科大讯飞"), ("CN:300059", "东方财富"),
    ("CN:600887", "伊利股份"), ("CN:601166", "兴业银行"),
]


def _code(sym: str) -> str:
    return sym.split(":", 1)[1] if ":" in sym else sym


def _direct(query: str) -> Optional[dict[str, Any]]:
    """Offer a direct add only for an UNAMBIGUOUS code: an explicit "MARKET:CODE"
    or a pure-digit code (CN 6-digit / HK 1-5 digit). Names and lowercase words are
    left to the search results (avoids garbage like US:腾讯 / US:APPLE)."""
    q = query.strip()
    if not q:
        return None
    bare = q.split(":")[-1]
    if ":" not in q and not bare.isdigit():
        return None
    try:
        sym = canonical(q)
        market, _ = parse(sym)
    except Exception:
        return None
    return {"symbol": sym, "name": "（直接添加 / add as typed）",
            "market": market, "source": "direct"}


def _popular(query: str) -> list[dict[str, Any]]:
    ql = query.lower()
    out = []
    for sym, name in POPULAR:
        if ql in name.lower() or ql in _code(sym).lower() or ql in sym.lower():
            out.append({"symbol": sym, "name": name,
                        "market": parse(sym)[0], "source": "popular"})
    return out


# --- akshare name lists (cached) ------------------------------------------- #
_AK_A: dict[str, Any] = {"ts": 0.0, "df": None}
_AK_HK: dict[str, Any] = {"ts": 0.0, "df": None}
_AK_TTL = 86400.0


def _col(df, *names):
    for n in names:
        if n in df.columns:
            return n
    return None


def _akshare_blocking(query: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    try:
        import akshare as ak
    except Exception:
        return out
    now = time.time()
    # A-share full code/name list
    try:
        if _AK_A["df"] is None or now - _AK_A["ts"] > _AK_TTL:
            _AK_A["df"] = ak.stock_info_a_code_name()
            _AK_A["ts"] = now
        df = _AK_A["df"]
        cc = _col(df, "code", "代码")
        nc = _col(df, "name", "名称")
        if cc and nc:
            mask = (df[nc].astype(str).str.contains(query, case=False, na=False)
                    | df[cc].astype(str).str.contains(query, na=False))
            for _, r in df[mask].head(8).iterrows():
                code = str(r[cc]).zfill(6)
                out.append({"symbol": f"CN:{code}", "name": str(r[nc]),
                            "market": "CN", "source": "akshare"})
    except Exception as e:
        print(f"[search] akshare A-list failed: {e}")
    # HK name search only when the query has CJK chars (snapshot is heavy)
    if any("一" <= ch <= "鿿" for ch in query):
        try:
            if _AK_HK["df"] is None or now - _AK_HK["ts"] > _AK_TTL:
                _AK_HK["df"] = ak.stock_hk_spot_em()
                _AK_HK["ts"] = now
            df = _AK_HK["df"]
            cc = _col(df, "代码", "symbol", "code")
            nc = _col(df, "名称", "name")
            if cc and nc:
                mask = df[nc].astype(str).str.contains(query, case=False, na=False)
                for _, r in df[mask].head(8).iterrows():
                    code = "".join(filter(str.isdigit, str(r[cc]))).zfill(5)
                    out.append({"symbol": f"HK:{code}", "name": str(r[nc]),
                                "market": "HK", "source": "akshare"})
        except Exception as e:
            print(f"[search] akshare HK-list failed: {e}")
    return out


def _yfinance_blocking(query: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    try:
        import yfinance as yf
        quotes = yf.Search(query, max_results=8).quotes or []
    except Exception as e:
        print(f"[search] yfinance search failed: {e}")
        return out
    for q in quotes:
        sym = q.get("symbol", "")
        qt = (q.get("quoteType") or "").upper()
        if qt not in ("EQUITY", "ETF"):
            continue
        if sym.endswith(".HK"):
            market = "HK"
        elif sym.endswith((".SS", ".SZ", ".BJ")):
            market = "CN"
        elif "." not in sym and (q.get("exchange") in _US_EXCHANGES):
            market = "US"
        else:
            continue
        try:
            canon = from_yfinance(sym)
        except Exception:
            continue
        out.append({"symbol": canon,
                    "name": q.get("shortname") or q.get("longname") or "",
                    "market": market, "source": "yahoo"})
    return out


async def search_symbols(query: str, limit: int = 12) -> list[dict[str, Any]]:
    query = (query or "").strip()
    if not query:
        return []
    results: dict[str, dict[str, Any]] = {}

    # 1. direct code + 2. popular (instant, offline)
    direct = _direct(query)
    if direct:
        results[direct["symbol"]] = direct
    for r in _popular(query):
        results.setdefault(r["symbol"], r)

    # 3 + 4. live sources in parallel (best-effort; bounded so a slow/unreachable
    # source can never hang the box — popular/direct still return instantly).
    try:
        live = await asyncio.wait_for(
            asyncio.gather(
                asyncio.to_thread(_akshare_blocking, query),
                asyncio.to_thread(_yfinance_blocking, query),
                return_exceptions=True,
            ),
            timeout=3.0,
        )
    except asyncio.TimeoutError:
        live = []
    for chunk in live:
        if isinstance(chunk, Exception):
            continue
        for r in chunk:
            results.setdefault(r["symbol"], r)

    return list(results.values())[:limit]
