"""Social / KOL sentiment sources (free-first, all gracefully degrading).

Verified-working free paths (2026-06):
  - X 大V via Nitter RSS  : https://<nitter>/<handle>/rss  +  /search/rss?q=$TICKER
    (instance configurable; nitter.net works, others are bot-walled)
  - Reddit via search.rss : r/wallstreetbets, r/stocks (browser UA required;
    .json is 403 but .rss is open)
  - CNN Fear & Greed      : production.dataviz.cnn.io JSON (US market mood)
  - A股人气/千股千评       : akshare stock_hot_rank_em / stock_comment_em
    (Eastmoney — blocked in some proxied envs, fine on a normal network)

Every fetcher returns [] / {} on failure and is cached briefly. Headlines are
scored with the local lexicon (app.sentiment) so the result is AI-ready.
"""
from __future__ import annotations

import asyncio
import re
import time
from typing import Any, Optional

import requests

from . import symbols
from .config import settings
from .sentiment import score_text

_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/126 Safari/537.36 stock-agent/1.0")
_TIMEOUT = 12
_CACHE: dict[str, tuple[float, Any]] = {}


def _cached(key: str, ttl: float):
    ent = _CACHE.get(key)
    if ent and time.time() - ent[0] < ttl:
        return ent[1]
    return None


def _store(key: str, val: Any) -> Any:
    _CACHE[key] = (time.time(), val)
    return val


def _get(url: str) -> Optional[str]:
    try:
        r = requests.get(url, headers={"User-Agent": _UA, "Accept": "*/*"},
                         timeout=_TIMEOUT)
        if r.status_code == 200:
            return r.text
        print(f"[social] GET {url[:60]} -> {r.status_code}")
    except Exception as e:
        print(f"[social] GET {url[:60]} failed: {type(e).__name__}")
    return None


_TAG = re.compile(r"<[^>]+>")


def _untag(s: str) -> str:
    import html
    return html.unescape(_TAG.sub("", s or "")).strip()


def _parse_rss_items(xml: str, source: str, limit: int) -> list[dict]:
    """Minimal RSS/Atom parser: returns [{title, author, link, ts, source}]."""
    out: list[dict] = []
    if not xml:
        return out
    blocks = re.findall(r"<item>(.*?)</item>", xml, re.S) or \
        re.findall(r"<entry>(.*?)</entry>", xml, re.S)
    for b in blocks[:limit]:
        title = re.search(r"<title[^>]*>(.*?)</title>", b, re.S)
        link = re.search(r"<link[^>]*?href=\"([^\"]+)\"", b) or \
            re.search(r"<link[^>]*>(.*?)</link>", b, re.S)
        author = re.search(r"<dc:creator>(.*?)</dc:creator>", b, re.S) or \
            re.search(r"<name>(.*?)</name>", b, re.S)
        pub = re.search(r"<pubDate>(.*?)</pubDate>", b) or \
            re.search(r"<updated>(.*?)</updated>", b)
        ts = 0.0
        if pub:
            from email.utils import parsedate_to_datetime
            try:
                ts = parsedate_to_datetime(pub.group(1)).timestamp()
            except Exception:
                try:
                    from datetime import datetime
                    ts = datetime.fromisoformat(
                        pub.group(1).replace("Z", "+00:00")).timestamp()
                except Exception:
                    ts = 0.0
        t = _untag(title.group(1) if title else "")
        if not t:
            continue
        out.append({
            "title": t[:280],
            "author": _untag(author.group(1) if author else ""),
            "link": (link.group(1) if link else "").strip(),
            "ts": ts,
            "source": source,
        })
    return out


# --------------------------------------------------------------------------- #
# X 大V via Nitter
# --------------------------------------------------------------------------- #
def kol_handles() -> list[str]:
    raw = getattr(settings, "x_kol_handles", "") or ""
    return [h.strip().lstrip("@") for h in raw.split(",") if h.strip()]


def _nitter_base() -> str:
    return (getattr(settings, "nitter_instance", "") or
            "https://nitter.net").rstrip("/")


def _fetch_x_blocking(symbol: Optional[str], limit: int) -> list[dict]:
    base = _nitter_base()
    posts: list[dict] = []
    # 1. cashtag search for the symbol (US only — X cashtags are US-centric)
    if symbol:
        try:
            mkt, code = symbols.parse(symbol)
            if mkt == "US":
                xml = _get(f"{base}/search/rss?f=tweets&q=%24{code}")
                posts += _parse_rss_items(xml or "", "X·搜索", limit)
        except Exception:
            pass
    # 2. configured KOL timelines
    for h in kol_handles()[:8]:
        xml = _get(f"{base}/{h}/rss")
        items = _parse_rss_items(xml or "", f"X·@{h}", 5)
        for it in items:
            it["author"] = it["author"] or f"@{h}"
        posts += items
    posts.sort(key=lambda p: -(p.get("ts") or 0))
    return posts[:limit]


async def get_x_posts(symbol: Optional[str] = None, limit: int = 12) -> list[dict]:
    key = f"x|{symbol}|{','.join(kol_handles())}"
    hit = _cached(key, 300)
    if hit is not None:
        return hit
    posts = await asyncio.to_thread(_fetch_x_blocking, symbol, limit)
    return _store(key, posts)


# --------------------------------------------------------------------------- #
# Reddit (search.rss is open with a browser UA)
# --------------------------------------------------------------------------- #
_SUBS = ["wallstreetbets", "stocks"]


def _fetch_reddit_blocking(symbol: str, limit: int) -> list[dict]:
    try:
        _, code = symbols.parse(symbol)
    except Exception:
        return []
    posts: list[dict] = []
    for sub in _SUBS:
        xml = _get(f"https://www.reddit.com/r/{sub}/search.rss"
                   f"?q={code}&restrict_sr=1&sort=new&limit={limit}")
        posts += _parse_rss_items(xml or "", f"r/{sub}", limit)
    posts.sort(key=lambda p: -(p.get("ts") or 0))
    return posts[:limit]


async def get_reddit_posts(symbol: str, limit: int = 8) -> list[dict]:
    key = f"reddit|{symbol}"
    hit = _cached(key, 300)
    if hit is not None:
        return hit
    posts = await asyncio.to_thread(_fetch_reddit_blocking, symbol, limit)
    return _store(key, posts)


# --------------------------------------------------------------------------- #
# CNN Fear & Greed (US market mood)
# --------------------------------------------------------------------------- #
async def get_fear_greed() -> dict:
    hit = _cached("feargreed", 1800)
    if hit is not None:
        return hit

    def blocking() -> dict:
        try:
            r = requests.get(
                "https://production.dataviz.cnn.io/index/fearandgreed/graphdata",
                headers={"User-Agent": _UA}, timeout=_TIMEOUT)
            if r.status_code != 200:
                return {}
            fg = (r.json() or {}).get("fear_and_greed") or {}
            return {"score": round(float(fg.get("score", 0)), 1),
                    "rating": fg.get("rating", ""),
                    "prev_close": fg.get("previous_close"),
                    "ts": time.time()}
        except Exception as e:
            print(f"[social] feargreed failed: {e}")
            return {}
    return _store("feargreed", await asyncio.to_thread(blocking))


# --------------------------------------------------------------------------- #
# A股 人气榜 / 千股千评 (akshare/Eastmoney — may be blocked behind some proxies)
# --------------------------------------------------------------------------- #
async def get_cn_buzz(symbol: str) -> dict:
    key = f"cnbuzz|{symbol}"
    hit = _cached(key, 1800)
    if hit is not None:
        return hit

    def blocking() -> dict:
        try:
            mkt, code = symbols.parse(symbol)
            if mkt != "CN":
                return {}
            import akshare as ak
            out: dict[str, Any] = {}
            try:  # 股吧人气排名
                df = ak.stock_hot_rank_em()
                col_code = next((c for c in ("代码", "股票代码") if c in df.columns), None)
                if col_code is not None:
                    row = df[df[col_code].astype(str).str.contains(code)]
                    if len(row):
                        out["hot_rank"] = int(row.index[0]) + 1
            except Exception:
                pass
            try:  # 千股千评 综合得分
                df = ak.stock_comment_em()
                col_code = next((c for c in ("代码", "股票代码") if c in df.columns), None)
                if col_code is not None:
                    row = df[df[col_code].astype(str) == code]
                    if len(row):
                        r0 = row.iloc[0]
                        for col in ("综合得分", "评分"):
                            if col in row.columns:
                                out["comment_score"] = float(r0[col])
                                break
                        for col in ("机构参与度",):
                            if col in row.columns:
                                out["institution_pct"] = float(r0[col])
            except Exception:
                pass
            return out
        except Exception as e:
            print(f"[social] cn_buzz failed: {e}")
            return {}
    return _store(key, await asyncio.to_thread(blocking))


# --------------------------------------------------------------------------- #
# Aggregate: one AI-ready social snapshot per symbol
# --------------------------------------------------------------------------- #
async def get_social(symbol: str, limit: int = 12) -> dict:
    x_posts, reddit, fg, cn = await asyncio.gather(
        get_x_posts(symbol, limit), get_reddit_posts(symbol, 8),
        get_fear_greed(), get_cn_buzz(symbol), return_exceptions=True)
    x_posts = x_posts if isinstance(x_posts, list) else []
    reddit = reddit if isinstance(reddit, list) else []
    fg = fg if isinstance(fg, dict) else {}
    cn = cn if isinstance(cn, dict) else {}

    posts = (x_posts + reddit)
    bull = bear = 0
    for p in posts:
        s = score_text(p.get("title", ""))
        p["sentiment"] = s
        bull += s["bull"]
        bear += s["bear"]
    total = bull + bear
    agg_score = round((bull - bear) / total, 3) if total else 0.0
    label = ("bullish" if agg_score > 0.15 else
             "bearish" if agg_score < -0.15 else "neutral")
    return {
        "symbol": symbols.canonical(symbol),
        "posts": posts,
        "aggregate": {"label": label, "score": agg_score,
                      "bull": bull, "bear": bear, "n": len(posts)},
        "fear_greed": fg,
        "cn_buzz": cn,
        "kol_handles": kol_handles(),
    }


def summarize_for_ai(social: dict) -> str:
    """Compact Chinese summary for prompts; '' when nothing useful."""
    if not social:
        return ""
    bits = []
    agg = social.get("aggregate") or {}
    if agg.get("n"):
        lbl = {"bullish": "偏多", "bearish": "偏空", "neutral": "中性"}.get(
            agg.get("label", ""), "中性")
        bits.append(f"社交讨论{agg['n']}条 情绪{lbl}(多{agg['bull']}/空{agg['bear']})")
    fg = social.get("fear_greed") or {}
    if fg.get("score") is not None and fg.get("rating"):
        bits.append(f"美股恐惧贪婪指数={fg['score']}({fg['rating']})")
    cn = social.get("cn_buzz") or {}
    if cn.get("hot_rank"):
        bits.append(f"股吧人气榜第{cn['hot_rank']}名")
    if cn.get("comment_score"):
        bits.append(f"千股千评得分{cn['comment_score']}")
    titles = [p["title"] for p in (social.get("posts") or [])[:5]]
    if titles:
        bits.append("热帖: " + " | ".join(t[:60] for t in titles))
    return "；".join(bits)
