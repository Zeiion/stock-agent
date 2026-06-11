"""Investor-persona panel — run a panel of legendary-investor agents in parallel
and aggregate their independent signals into a confidence-weighted consensus.

Essence borrowed from **virattt/ai-hedge-fund** (~60k★): each famous investor is
a *system persona* that maps the SAME market context to a directional signal
through a distinct investing philosophy, and a "portfolio manager" then
aggregates the panel into one verdict. We adapt the pattern to this project:

  - We reuse the persona "lenses" already defined in ``ai/prompts.py``
    (``ANALYSIS_STRATEGIES`` with ``group == "大师"``) so the panel stays
    consistent with the single-lens ``brain.decide`` path — one source of truth.
  - Every model call routes through the existing ``brain.run_schema`` so we get
    provider selection (anthropic / claude / codex) + cross-tier fallback for
    free.
  - Unlike ai-hedge-fund we do NOT place orders or size positions here — the
    output is a *signal + confidence + reasoning* per persona plus an aggregated
    consensus, in this tool's signal-only spirit (the dross we drop: real-order
    execution, a paid single-source data API, and anthropomorphic over-trust —
    the consensus deliberately surfaces dissent rather than hiding it).

Never raises: a persona that fails or returns garbage becomes a neutral
abstention so the panel always returns a result.
"""
from __future__ import annotations

import asyncio
import json
import time
from typing import Any, Optional

from .ai.brain import brain
from .ai.prompts import ANALYSIS_STRATEGIES


# --------------------------------------------------------------------------- #
# Which personas are eligible: every "大师" lens already defined in prompts.py.
# --------------------------------------------------------------------------- #
PERSONA_KEYS: list[str] = [
    k for k, v in ANALYSIS_STRATEGIES.items() if v.get("group") == "大师"
]

# A diverse default panel kept small to bound latency / token cost: deep value,
# disruptive growth, deep-contrarian risk, GARP growth, macro, and pure trend.
# Callers can override with any subset of PERSONA_KEYS (or "all").
DEFAULT_PANEL: list[str] = [
    k for k in ("buffett", "wood", "burry", "lynch", "dalio", "livermore")
    if k in PERSONA_KEYS
] or PERSONA_KEYS[:6]


# --------------------------------------------------------------------------- #
# Per-persona reply schema (strict / OpenAI-compatible: additionalProperties is
# False and every property is required).
# --------------------------------------------------------------------------- #
PERSONA_SCHEMA: dict = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "signal": {
            "type": "string",
            "enum": ["bullish", "bearish", "neutral"],
            "description": "方向性结论：看多 / 看空 / 中性。",
        },
        "action": {
            "type": "string",
            "enum": ["BUY", "ADD", "HOLD", "REDUCE", "SELL"],
            "description": "若让你操作，会如何行动。",
        },
        "confidence": {
            "type": "integer",
            "minimum": 0,
            "maximum": 100,
            "description": "对该判断的信心 0-100。数据不足时应明显降低。",
        },
        "reasoning": {
            "type": "string",
            "description": "简体中文，2-4 句，体现你这位投资人的独特视角与取舍。",
        },
        "key_points": {
            "type": "array",
            "items": {"type": "string"},
            "description": "简体中文要点列表。",
        },
    },
    "required": ["signal", "action", "confidence", "reasoning", "key_points"],
}


_SYSTEM_HEAD = (
    "你正在扮演著名投资人「{label}」的分析视角（仅模仿其公开投资哲学，并非本人观点）。"
    "请严格代入这位投资人的世界观、偏好与禁忌，对给定标的给出独立判断——"
    "不要给出『中庸的市场共识』，要让你这位投资人的风格鲜明地体现出来。\n"
    "投资哲学：{lens}\n"
)
_SYSTEM_TAIL = (
    "约束：只依据给定的行情、指标、基本面、新闻与情绪数据判断，不要臆造未提供的信息；"
    "数据缺失或延迟时降低 confidence。如果这位投资人按其风格根本不会碰这类标的，"
    "signal 取 neutral、action 取 HOLD 并说明原因。"
    "reasoning 与 key_points 用简体中文，signal/action 用规定英文枚举值。"
    "只返回符合 schema 的 JSON 对象，不要任何多余文字。"
)


# --------------------------------------------------------------------------- #
# Shared context block (built once, fed to every persona).
# --------------------------------------------------------------------------- #
def _quote_brief(quote: dict) -> dict:
    return {
        "symbol": quote.get("symbol"), "name": quote.get("name"),
        "market": quote.get("market"), "last": quote.get("last"),
        "prev_close": quote.get("prev_close"), "change_pct": quote.get("change_pct"),
        "open": quote.get("open"), "high": quote.get("high"), "low": quote.get("low"),
        "volume": quote.get("volume"), "currency": quote.get("currency"),
        "delayed": quote.get("delayed"), "source": quote.get("source"),
    }


def _news_titles(news: Any) -> list[str]:
    titles: list[str] = []
    for item in (news or [])[:8]:
        if isinstance(item, dict):
            t = str(item.get("title") or item.get("headline") or "").strip()
            pub = str(item.get("publisher") or item.get("source") or "").strip()
            if t:
                titles.append(f"{t} ({pub})" if pub else t)
        elif item:
            titles.append(str(item).strip())
    return [t for t in titles if t]


def build_context_block(ctx: dict) -> str:
    """A compact, persona-agnostic snapshot of everything the panel can reason on."""
    quote = ctx.get("quote", {}) or {}
    ind = ctx.get("indicators", {}) or {}
    position = ctx.get("position")
    lines = [
        f"标的：{quote.get('symbol', ctx.get('symbol', '?'))} "
        f"({quote.get('name', '')})，{quote.get('market', '?')} 市场。",
        "",
        "== 当前行情 ==",
        json.dumps(_quote_brief(quote), ensure_ascii=False),
        "",
        "== 技术指标快照 ==",
        json.dumps(ind, ensure_ascii=False) if ind else "(无可用指标)",
    ]

    from .fundamentals import summarize_for_ai as _fund_sum
    fund_line = _fund_sum(ctx.get("fundamentals") or {})
    lines += ["", "== 基本面快照 ==", fund_line or "(无基本面数据)"]

    from .social import summarize_for_ai as _soc_sum
    soc_line = _soc_sum(ctx.get("social") or {})
    lines += ["", "== 社交情绪 (X大V/Reddit/恐惧贪婪/股吧) ==", soc_line or "(暂无社交信号)"]

    titles = _news_titles(ctx.get("news"))
    news_block = "\n".join(f"- {t}" for t in titles) if titles else "(暂无消息)"
    lines += ["", "== 最近新闻标题 ==", news_block]

    lines += ["", "== 当前持仓 ==", json.dumps(position or {"qty": 0}, ensure_ascii=False)]
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# Single persona execution
# --------------------------------------------------------------------------- #
def _abstain(key: str, label: str, reason: str = "该视角分析失败或数据不足") -> dict:
    return {
        "key": key, "label": label, "signal": "neutral", "action": "HOLD",
        "confidence": 0, "reasoning": reason, "key_points": [], "provider": "-",
    }


def _normalize(raw: dict, key: str, label: str, provider: str) -> dict:
    signal = str(raw.get("signal", "neutral")).lower()
    if signal not in ("bullish", "bearish", "neutral"):
        signal = "neutral"
    action = str(raw.get("action", "HOLD")).upper()
    if action not in ("BUY", "ADD", "HOLD", "REDUCE", "SELL"):
        action = "HOLD"
    try:
        confidence = max(0, min(100, int(raw.get("confidence", 0))))
    except (TypeError, ValueError):
        confidence = 0
    kp = raw.get("key_points")
    if isinstance(kp, (list, tuple)):
        key_points = [str(p) for p in kp if p is not None]
    elif kp:
        key_points = [str(kp)]
    else:
        key_points = []
    return {
        "key": key, "label": label, "signal": signal, "action": action,
        "confidence": confidence,
        "reasoning": str(raw.get("reasoning", "")).strip() or "(无说明)",
        "key_points": key_points, "provider": provider,
    }


async def _run_persona(key: str, shared: str, provider: Optional[str]) -> dict:
    spec = ANALYSIS_STRATEGIES.get(key) or {}
    label = spec.get("label", key)
    system = (
        _SYSTEM_HEAD.format(label=label, lens=spec.get("lens", "")) + _SYSTEM_TAIL
    )
    prompt = (
        "请以你这位投资人的视角分析下列标的并给出信号。\n\n"
        f"{shared}\n\n只返回符合 schema 的 JSON 对象。"
    )
    try:
        raw, prov = await brain.run_schema(prompt, system, PERSONA_SCHEMA, provider)
        return _normalize(raw, key, label, prov)
    except Exception as e:  # noqa: BLE001
        print(f"[personas] persona '{key}' failed: {e}")
        return _abstain(key, label)


# --------------------------------------------------------------------------- #
# Aggregation — confidence-weighted vote (the "portfolio manager" step)
# --------------------------------------------------------------------------- #
_SIGN = {"bullish": 1, "bearish": -1, "neutral": 0}


def aggregate(opinions: list[dict]) -> dict:
    """Confidence-weighted consensus over the panel.

    weighted_score in [-1, 1]: sum(sign * confidence) / sum(confidence over the
    *directional* voters). Abstentions (confidence 0) carry no weight. Consensus
    signal comes from thresholding the score; consensus confidence blends the
    score magnitude with the share of the panel that actually took a side.
    Dissenters (those opposing the consensus) are surfaced explicitly.
    """
    counts = {"bullish": 0, "bearish": 0, "neutral": 0}
    weighted_sum = 0.0
    weight_total = 0.0
    conf_sum = 0
    for o in opinions:
        sig = o.get("signal", "neutral")
        counts[sig] = counts.get(sig, 0) + 1
        conf = int(o.get("confidence", 0) or 0)
        conf_sum += conf
        if sig in ("bullish", "bearish"):
            weighted_sum += _SIGN[sig] * conf
            weight_total += conf

    n = len(opinions) or 1
    score = (weighted_sum / weight_total) if weight_total > 0 else 0.0

    if score >= 0.5:
        signal, action = "bullish", "BUY"
    elif score >= 0.15:
        signal, action = "bullish", "ADD"
    elif score <= -0.5:
        signal, action = "bearish", "SELL"
    elif score <= -0.15:
        signal, action = "bearish", "REDUCE"
    else:
        signal, action = "neutral", "HOLD"

    directional = counts["bullish"] + counts["bearish"]
    participation = directional / n            # share that took a side
    avg_conf = round(conf_sum / n, 1)
    # consensus confidence (0-100): conviction magnitude scaled by participation
    consensus_conf = round(abs(score) * 100 * (0.5 + 0.5 * participation))

    opp = "bearish" if signal == "bullish" else "bullish" if signal == "bearish" else None
    dissenters = (
        [o["label"] for o in opinions if o.get("signal") == opp] if opp else []
    )
    return {
        "signal": signal,
        "action": action,
        "score": round(score, 3),
        "confidence": consensus_conf,
        "avg_confidence": avg_conf,
        "counts": counts,
        "participation": round(participation, 2),
        "dissenters": dissenters,
    }


# --------------------------------------------------------------------------- #
# Public entrypoint
# --------------------------------------------------------------------------- #
def resolve_personas(personas: Optional[Any]) -> list[str]:
    """Validate a caller-supplied persona list against PERSONA_KEYS.

    Accepts a list of keys, the string "all", or None (-> DEFAULT_PANEL).
    Unknown keys are dropped; an empty result falls back to DEFAULT_PANEL.
    """
    if personas == "all":
        return list(PERSONA_KEYS)
    if not personas:
        return list(DEFAULT_PANEL)
    keys = [k for k in personas if k in PERSONA_KEYS]
    return keys or list(DEFAULT_PANEL)


async def run_panel(symbol: str, ctx: dict, personas: Optional[Any] = None,
                    provider: Optional[str] = None) -> dict:
    """Run the persona panel for one symbol and return panel + consensus.

    Returns:
        {
          "symbol": str,
          "panel": [ {key,label,signal,action,confidence,reasoning,
                      key_points,provider}, ... ],
          "consensus": { signal, action, score, confidence, avg_confidence,
                         counts, participation, dissenters },
          "ts": float,
        }
    Never raises.
    """
    ctx = dict(ctx)
    ctx.setdefault("symbol", symbol)
    keys = resolve_personas(personas)
    shared = build_context_block(ctx)

    results = await asyncio.gather(
        *[_run_persona(k, shared, provider) for k in keys],
        return_exceptions=True,
    )
    opinions: list[dict] = []
    for key, res in zip(keys, results):
        if isinstance(res, dict):
            opinions.append(res)
        else:  # defensive: _run_persona swallows its own, but stay safe
            spec = ANALYSIS_STRATEGIES.get(key) or {}
            opinions.append(_abstain(key, spec.get("label", key)))

    return {
        "symbol": symbol,
        "panel": opinions,
        "consensus": aggregate(opinions),
        "ts": time.time(),
    }
