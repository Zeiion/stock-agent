"""Lexicon sentiment scorer for finance headlines (中文 + English).

Fast, dependency-free, fully deterministic: NO network, NO LLM. It counts
case-insensitive substring hits of two curated word lists ("看多"/bullish and
"看空"/bearish) inside a headline's title + summary, then derives a normalized
score in [-1, 1] and a discrete label.

Public API
----------
    score_text(text) -> {"label", "score", "bull", "bear"}
    score_headlines(items) -> {"items": [item + {"sentiment": ...}],
                               "aggregate": {"label","score","bull","bear",
                                             "neutral","n"}}

Design rules followed from the rest of the backend:
  * Never raise on a single bad / non-string / empty input — degrade gracefully.
  * Word lists are module constants so they're trivial to extend.

Scoring
-------
  score = (bull - bear) / max(1, bull + bear)   -> always within [-1, 1]
  label = "bullish"  if score >  POS_THRESHOLD
          "bearish"  if score <  NEG_THRESHOLD
          "neutral"  otherwise
"""
from __future__ import annotations

from typing import Any

# --------------------------------------------------------------------------- #
# Curated word lists (extend freely — kept as module constants on purpose).
# Substring matching is case-insensitive; Chinese terms match as-is.
# --------------------------------------------------------------------------- #
BULL: list[str] = [
    # 中文（看多）
    "大涨", "涨停", "利好", "突破", "新高", "增持", "回购", "超预期",
    "加仓", "反弹", "放量上涨", "业绩增长", "上调", "买入", "看多", "中标",
    "扭亏", "盈利", "创纪录", "强劲", "需求旺盛",
    # English (bullish)
    "surge", "soar", "beat", "beats", "upgrade", "buy", "rally", "record",
    "jump", "gain", "gains", "outperform", "bullish", "raise", "raised",
    "growth", "strong", "tops", "boost", "wins",
]

BEAR: list[str] = [
    # 中文（看空）
    "大跌", "跌停", "利空", "下挫", "新低", "减持", "亏损", "不及预期",
    "暴跌", "抛售", "裁员", "下调", "卖出", "看空", "违约", "处罚",
    "诉讼", "风险", "放缓", "警告", "下滑", "巨亏",
    # English (bearish)
    "plunge", "plummet", "miss", "misses", "downgrade", "sell", "crash",
    "fall", "falls", "drop", "drops", "cut", "cuts", "bearish", "lawsuit",
    "probe", "warning", "slump", "weak", "layoff", "fraud", "decline",
]

# Lowercased once at import for fast, case-insensitive substring matching.
_BULL_LC: list[str] = [w.lower() for w in BULL if w]
_BEAR_LC: list[str] = [w.lower() for w in BEAR if w]

# Label thresholds on the normalized score.
POS_THRESHOLD = 0.15
NEG_THRESHOLD = -0.15


# --------------------------------------------------------------------------- #
# Helpers (never raise)
# --------------------------------------------------------------------------- #
def _str(val: Any) -> str:
    """Coerce anything to a stripped str; None / NaN / errors -> ''."""
    if val is None:
        return ""
    try:
        if val != val:  # NaN guard
            return ""
    except Exception:
        pass
    try:
        return str(val).strip()
    except Exception:
        return ""


def _label_for(score: float) -> str:
    if score > POS_THRESHOLD:
        return "bullish"
    if score < NEG_THRESHOLD:
        return "bearish"
    return "neutral"


def _count_hits(text_lc: str, words_lc: list[str]) -> int:
    n = 0
    for w in words_lc:
        if w and w in text_lc:
            n += 1
    return n


# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #
def score_text(text: str) -> dict:
    """Score a single piece of text.

    Returns {"label", "score" in [-1,1], "bull": int, "bear": int}.
    Robust to non-string / empty input (returns a neutral zero result).
    """
    text_lc = _str(text).lower()
    if not text_lc:
        return {"label": "neutral", "score": 0.0, "bull": 0, "bear": 0}

    bull = _count_hits(text_lc, _BULL_LC)
    bear = _count_hits(text_lc, _BEAR_LC)
    score = (bull - bear) / max(1, bull + bear)
    return {"label": _label_for(score), "score": score, "bull": bull, "bear": bear}


def score_headlines(items: list[dict]) -> dict:
    """Score a list of headline items and aggregate the result.

    Args:
        items: list of {"title", "summary"?, ...} dicts. Each item's title and
            summary are concatenated and scored; the original item is returned
            with an extra "sentiment" key. Non-dict / bad items degrade to a
            neutral score rather than raising.

    Returns:
        {
          "items": [<original item> + {"sentiment": <score_text result>}, ...],
          "aggregate": {"label", "score", "bull", "bear", "neutral", "n"},
        }
        where bull/bear are summed across items, neutral counts items whose own
        label is "neutral", n is the item count, and label/score apply the same
        thresholds to the aggregate (bull - bear) / max(1, bull + bear).
    """
    out_items: list[dict] = []
    total_bull = 0
    total_bear = 0
    neutral = 0
    n = 0

    if items:
        for raw in items:
            n += 1
            if isinstance(raw, dict):
                text = _str(raw.get("title")) + " " + _str(raw.get("summary"))
                sent = score_text(text)
                # Don't mutate the caller's dict.
                item = dict(raw)
            else:
                # Tolerate non-dict rows (e.g. a bare string) without crashing.
                sent = score_text(_str(raw))
                item = {"value": _str(raw)}
            item["sentiment"] = sent
            out_items.append(item)

            total_bull += sent["bull"]
            total_bear += sent["bear"]
            if sent["label"] == "neutral":
                neutral += 1

    agg_score = (total_bull - total_bear) / max(1, total_bull + total_bear)
    aggregate = {
        "label": _label_for(agg_score),
        "score": agg_score,
        "bull": total_bull,
        "bear": total_bear,
        "neutral": neutral,
        "n": n,
    }
    return {"items": out_items, "aggregate": aggregate}
