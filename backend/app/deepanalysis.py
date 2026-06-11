"""Multi-agent deep analysis orchestrator — the platform's flagship AI feature.

Instead of asking a single model for one verdict (that's what `brain.decide`
does), `deep_analyze` runs three focused *specialist analyst agents* in parallel
— 技术面 / 基本面·估值 / 消息面·情绪 — and then a *synthesis agent* that weighs
the three opinions against each other (a 多空辩论式 综合) and emits a single,
structured trading decision in the project's canonical `Decision` shape.

Every model call goes through the existing `brain.run_schema`, which already
handles provider selection (anthropic / claude / codex) plus fallback across
tiers. We never raise to the caller: a single analyst failing is substituted
with a neutral placeholder, and a synthesis failure falls back to
`brain.decide(ctx)` (which itself degrades to a mechanical decision).

`ctx` is built by the caller and contains:
    symbol      : str  canonical "MARKET:CODE"
    quote       : dict (normalized Quote.to_dict)
    indicators  : dict (compute_indicators snapshot)
    recent      : list[dict] (OHLCV candles, oldest first)
    position    : dict | None
    news        : list[dict] ({title, publisher, ts, ...})
"""
from __future__ import annotations

import asyncio
import json
import time
from typing import Any, Optional

from .ai.brain import brain, _coerce_decision
from .ai.schema import DECISION_SCHEMA


# --------------------------------------------------------------------------- #
# Schema returned by each specialist analyst agent.
#
# Strict / OpenAI-compatible: `additionalProperties` is False AND every property
# is listed in `required` (codex --output-schema enforces this; Claude/Anthropic
# accept the same schema). The model writes Chinese for summary / key_points.
# --------------------------------------------------------------------------- #
ANALYST_SCHEMA: dict = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "dimension": {
            "type": "string",
            "description": "本次分析的维度名称，例如 技术面 / 基本面·估值 / 消息面·情绪。",
        },
        "stance": {
            "type": "string",
            "enum": ["bullish", "bearish", "neutral"],
            "description": "该维度的方向性结论：看多 / 看空 / 中性。",
        },
        "score": {
            "type": "integer",
            "minimum": 1,
            "maximum": 5,
            "description": "信念强度 1(弱) 到 5(强)。",
        },
        "summary": {
            "type": "string",
            "description": "简体中文，2-4 句的维度小结。",
        },
        "key_points": {
            "type": "array",
            "items": {"type": "string"},
            "description": "简体中文要点列表（bullet points）。",
        },
    },
    "required": ["dimension", "stance", "score", "summary", "key_points"],
}


# --------------------------------------------------------------------------- #
# System prompts (all Simplified Chinese)
# --------------------------------------------------------------------------- #
_TECH_SYSTEM = (
    "你是一名专注【技术面】的资深量化技术分析师。"
    "你只根据给定的价格、技术指标(MA/RSI/MACD/BOLL/KDJ 等)和最近的 K 线数据做判断，"
    "评估趋势方向、动量强弱以及关键的支撑/阻力位。"
    "不要臆造未提供的数据；指标缺失或数据延迟时要降低信念强度(score)。"
    "summary 与 key_points 全部使用简体中文，stance 用规定的英文枚举值。"
    "只返回符合 schema 的 JSON 对象，不要任何多余文字。"
)

_FUND_SYSTEM = (
    "你是一名专注【基本面与估值】的资深权益研究分析师。"
    "你会收到一份基本面快照(可能包含 市值/PE/PB/ROE/净利率/营收增速/股息率/Beta/"
    "52周位置/分析师目标价/机构评级 等)。请基于这些真实数据评估估值水平、盈利质量与"
    "成长性，并结合当前价格与持仓给出该维度的判断；引用具体数字。"
    "若快照为空或字段缺失较多，则明确说明『详细基本面数据有限』并把 score 控制在 1-3。"
    "summary 与 key_points 全部使用简体中文，stance 用规定的英文枚举值。"
    "只返回符合 schema 的 JSON 对象，不要任何多余文字。"
)

_SENT_SYSTEM = (
    "你是一名专注【消息面与市场情绪】的分析师。"
    "你只根据给定的新闻标题列表判断市场情绪偏向(利好/利空/中性)及其潜在影响。"
    "如果没有任何新闻，stance 必须为 neutral，summary 写『暂无消息』，"
    "并把信念强度(score)设为 1。"
    "不要臆造未提供的新闻内容。"
    "summary 与 key_points 全部使用简体中文，stance 用规定的英文枚举值。"
    "只返回符合 schema 的 JSON 对象，不要任何多余文字。"
)

# --------------------------------------------------------------------------- #
# Optional bull/bear researcher DEBATE layer (essence from TauricResearch/
# TradingAgents): after the analysts speak, a 多头研究员 and 空头研究员 argue the
# upside vs downside from the SAME evidence, then a risk-aware CIO adjudicates.
# This catches one-sided reads that a single synthesis pass can rationalize away.
# Gated behind `debate=True` so the default deep-analysis path stays cheap/fast.
# --------------------------------------------------------------------------- #
RESEARCHER_SCHEMA: dict = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "thesis": {
            "type": "string",
            "description": "简体中文，一句话核心论点（你所代表方向的最强主张）。",
        },
        "arguments": {
            "type": "array",
            "items": {"type": "string"},
            "description": "简体中文，支撑该方向的具体论据（引用指标/基本面/消息）。",
        },
        "rebuttal": {
            "type": "string",
            "description": "简体中文，主动回应对立面最强的反驳，或承认并量化己方主要风险。",
        },
        "confidence": {
            "type": "integer", "minimum": 1, "maximum": 5,
            "description": "对该方向论据的信心 1(弱)-5(强)。",
        },
    },
    "required": ["thesis", "arguments", "rebuttal", "confidence"],
}

_BULL_SYSTEM = (
    "你是一名【多头研究员】。基于分析师意见与给定数据，尽你所能有力地论证该标的的"
    "看多/上行逻辑（趋势、催化、估值修复、被低估的利好等），但必须诚实：在 rebuttal 中"
    "正面回应空头最可能的反驳，或承认己方论点的主要风险，不要无视坏数据。"
    "不要臆造未提供的信息。thesis/arguments/rebuttal 用简体中文。"
    "只返回符合 schema 的 JSON 对象。"
)

_BEAR_SYSTEM = (
    "你是一名【空头研究员】。基于分析师意见与给定数据，尽你所能有力地论证该标的的"
    "看空/下行风险（趋势走弱、估值泡沫、消息利空、拥挤交易、流动性等），但必须诚实：在"
    "rebuttal 中正面回应多头最可能的反驳，或承认己方论点的主要风险。"
    "不要臆造未提供的信息。thesis/arguments/rebuttal 用简体中文。"
    "只返回符合 schema 的 JSON 对象。"
)

RISK_SYNTHESIS_SYSTEM = (
    "你是首席投资决策官(CIO)，同时肩负风险管理职责。你会收到三位专家分析师的意见，"
    "以及【多头研究员】与【空头研究员】围绕同一证据展开的辩论(各含论点、论据与反驳)。"
    "请做一次『风险调整后』的最终裁决："
    "明确指出多空双方哪一方的论据更有说服力、为什么；当双方势均力敌或空头风险论据扎实时，"
    "必须主动下调 conviction 并收紧风险控制（更靠近 HOLD/REDUCE、给出更明确的止损）。"
    "决策约定：action 取 BUY/SELL/HOLD/REDUCE/ADD；conviction 为 1(弱)到 5(强)；"
    "horizon 取 intraday/swing/position；数据延迟或过期时把 data_freshness_ok 设为 false。"
    "rationale 必须是详尽的简体中文，复述辩论的关键交锋与你的裁决依据；"
    "key_risks 用简体中文，且必须吸收空头研究员提出的有效风险点。"
    "枚举字段使用规定英文值，价格类字段保持数值。只返回符合 decision schema 的 JSON 对象。"
)

SYNTHESIS_SYSTEM = (
    "你是首席投资决策官(CIO)，负责把多位专家分析师的意见综合为一个最终的交易建议。"
    "你会收到【技术面】【基本面·估值】【消息面·情绪】三个维度的独立分析意见(JSON)，"
    "以及当前的价格与指标快照。"
    "请进行一次『多空辩论式』的综合：对比三方观点，指出它们之间的一致与冲突，"
    "并在冲突时说明你如何取舍与加权(例如技术面与消息面背离时偏向哪一方、为什么)。"
    "决策约定：action 取 BUY/SELL/HOLD/REDUCE/ADD；conviction 为 1(弱)到 5(强)，"
    "请诚实评估，多数情形为 2-3；horizon 取 intraday/swing/position；"
    "数据延迟或过期时把 data_freshness_ok 设为 false 并降低 conviction。"
    "rationale 必须是一段详尽、有条理的简体中文叙述(可以较长)，综合三个维度并体现辩论过程；"
    "key_risks 用简体中文列出主要风险。"
    "枚举字段(action/horizon)使用规定的英文值，价格类字段保持数值。"
    "只返回符合 schema 的 JSON 对象，不要任何多余文字。"
)


# --------------------------------------------------------------------------- #
# User-prompt builders — each analyst gets only the relevant slice of ctx.
# --------------------------------------------------------------------------- #
def _quote_brief(quote: dict) -> dict:
    """Compact, JSON-friendly view of the current quote."""
    return {
        "symbol": quote.get("symbol"),
        "name": quote.get("name"),
        "market": quote.get("market"),
        "last": quote.get("last"),
        "prev_close": quote.get("prev_close"),
        "change_pct": quote.get("change_pct"),
        "open": quote.get("open"),
        "high": quote.get("high"),
        "low": quote.get("low"),
        "volume": quote.get("volume"),
        "currency": quote.get("currency"),
        "delayed": quote.get("delayed"),
        "source": quote.get("source"),
    }


def _build_tech_prompt(ctx: dict) -> str:
    quote = ctx.get("quote", {}) or {}
    ind = ctx.get("indicators", {}) or {}
    recent = ctx.get("recent", []) or []
    # keep candles compact and bounded so the prompt stays cheap
    candles = [
        {"t": c.get("ts"), "o": c.get("open"), "h": c.get("high"),
         "l": c.get("low"), "c": c.get("close"), "v": c.get("volume")}
        for c in recent[-30:]
    ]
    lines = [
        f"请对 {quote.get('symbol', ctx.get('symbol', '?'))} "
        f"({quote.get('name', '')}) 进行【技术面】分析，给出趋势、动量与支撑/阻力判断。",
        "",
        "== 当前行情 ==",
        json.dumps(_quote_brief(quote), ensure_ascii=False),
        "",
        "== 技术指标快照 ==",
        json.dumps(ind, ensure_ascii=False) if ind else "(无可用指标)",
        "",
        "== 最近 K 线 (由旧到新) ==",
        json.dumps(candles, ensure_ascii=False) if candles else "(无 K 线数据)",
        "",
        "dimension 填『技术面』。只返回符合 schema 的 JSON。",
    ]
    return "\n".join(lines)


def _build_fund_prompt(ctx: dict) -> str:
    quote = ctx.get("quote", {}) or {}
    position = ctx.get("position")
    from .fundamentals import summarize_for_ai
    fund_line = summarize_for_ai(ctx.get("fundamentals") or {})
    lines = [
        f"请对 {quote.get('symbol', ctx.get('symbol', '?'))} "
        f"({quote.get('name', '')}) 进行【基本面与估值】分析。",
        "",
        "== 基本面快照 ==",
        fund_line or "（无基本面数据 — 请说明数据有限并保持低 score）",
        "",
        "== 当前行情 (价格 / 市场) ==",
        json.dumps(_quote_brief(quote), ensure_ascii=False),
        "",
        "== 当前持仓 ==",
        json.dumps(position or {"qty": 0}, ensure_ascii=False),
        "",
        "dimension 填『基本面·估值』。只返回符合 schema 的 JSON。",
    ]
    return "\n".join(lines)


def _news_titles(news: Any) -> list[str]:
    """Extract headline strings from ctx['news'] (list of dicts or raw strings)."""
    titles: list[str] = []
    for item in (news or [])[:12]:
        if isinstance(item, dict):
            t = item.get("title") or item.get("headline") or ""
            pub = item.get("publisher") or item.get("source") or ""
            t = str(t).strip()
            if t:
                titles.append(f"{t} ({pub})" if pub else t)
        elif item:
            titles.append(str(item).strip())
    return [t for t in titles if t]


def _build_sent_prompt(ctx: dict) -> str:
    quote = ctx.get("quote", {}) or {}
    titles = _news_titles(ctx.get("news"))
    if titles:
        news_block = "\n".join(f"- {t}" for t in titles)
    else:
        news_block = "(暂无消息)"
    from .social import summarize_for_ai as _soc_sum
    social_line = _soc_sum(ctx.get("social") or {})
    lines = [
        f"请对 {quote.get('symbol', ctx.get('symbol', '?'))} "
        f"({quote.get('name', '')}) 进行【消息面与情绪】分析，判断市场情绪偏向及影响。",
        "若无任何新闻与社交信号，stance 取 neutral，summary 写『暂无消息』，score 设为 1。",
        "",
        "== 最近新闻标题 ==",
        news_block,
        "",
        "== 社交情绪信号 (X大V/Reddit/恐惧贪婪指数/股吧) ==",
        social_line or "(暂无社交信号)",
        "",
        "dimension 填『消息面·情绪』。只返回符合 schema 的 JSON。",
    ]
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# Analyst execution
# --------------------------------------------------------------------------- #
def _failed_opinion(dimension: str) -> dict:
    """Neutral placeholder used when an analyst raises / returns garbage."""
    return {
        "dimension": dimension,
        "stance": "neutral",
        "score": 1,
        "summary": "该维度分析失败或数据不足",
        "key_points": [],
        "provider": "-",
    }


def _normalize_opinion(raw: dict, dimension: str, provider: str) -> dict:
    """Coerce a model reply into a clean opinion dict, tolerating loose typing."""
    stance = str(raw.get("stance", "neutral")).lower()
    if stance not in ("bullish", "bearish", "neutral"):
        stance = "neutral"
    try:
        score = max(1, min(5, int(raw.get("score", 1))))
    except (TypeError, ValueError):
        score = 1
    kp = raw.get("key_points")
    if isinstance(kp, (list, tuple)):
        key_points = [str(p) for p in kp if p is not None]
    elif kp:
        key_points = [str(kp)]
    else:
        key_points = []
    return {
        "dimension": str(raw.get("dimension") or dimension),
        "stance": stance,
        "score": score,
        "summary": str(raw.get("summary", "")).strip() or "(无小结)",
        "key_points": key_points,
        "provider": provider,
    }


async def _run_analyst(dimension: str, system: str, prompt: str,
                       provider: Optional[str]) -> dict:
    """Run one specialist agent. Returns a normalized opinion dict; never raises."""
    try:
        raw, prov_model = await brain.run_schema(prompt, system, ANALYST_SCHEMA, provider)
        return _normalize_opinion(raw, dimension, prov_model)
    except Exception as e:                                    # noqa: BLE001
        print(f"[deepanalysis] analyst '{dimension}' failed: {e}")
        return _failed_opinion(dimension)


# --------------------------------------------------------------------------- #
# Synthesis
# --------------------------------------------------------------------------- #
def _build_synth_prompt(ctx: dict, opinions: list[dict]) -> str:
    quote = ctx.get("quote", {}) or {}
    ind = ctx.get("indicators", {}) or {}
    lines = [
        f"标的：{quote.get('symbol', ctx.get('symbol', '?'))} "
        f"({quote.get('name', '')})，{quote.get('market', '?')} 市场。",
        "下面是三位专家分析师在各自维度上的独立意见，请综合它们并输出最终交易建议。",
        "",
        "== 价格 / 指标快照 ==",
        json.dumps({"quote": _quote_brief(quote), "indicators": ind},
                   ensure_ascii=False),
        "",
        "== 三方分析师意见 (JSON) ==",
        json.dumps(opinions, ensure_ascii=False),
        "",
        "请进行多空辩论式综合，解决冲突并加权，给出最终结构化决策。",
        "rationale 用简体中文详尽叙述(可较长，须综合三个维度)；key_risks 用简体中文。",
        "只返回符合 decision schema 的 JSON 对象。",
    ]
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# Debate layer (bull / bear researchers + risk-adjusted synthesis)
# --------------------------------------------------------------------------- #
def _failed_researcher(side: str) -> dict:
    return {"side": side, "thesis": "(该方研究失败或数据不足)", "arguments": [],
            "rebuttal": "", "confidence": 1, "provider": "-"}


def _normalize_researcher(raw: dict, side: str, provider: str) -> dict:
    args = raw.get("arguments")
    if isinstance(args, (list, tuple)):
        arguments = [str(a) for a in args if a is not None]
    elif args:
        arguments = [str(args)]
    else:
        arguments = []
    try:
        conf = max(1, min(5, int(raw.get("confidence", 1))))
    except (TypeError, ValueError):
        conf = 1
    return {
        "side": side,
        "thesis": str(raw.get("thesis", "")).strip() or "(无核心论点)",
        "arguments": arguments,
        "rebuttal": str(raw.get("rebuttal", "")).strip(),
        "confidence": conf,
        "provider": provider,
    }


def _build_researcher_prompt(ctx: dict, opinions: list[dict], side_cn: str) -> str:
    quote = ctx.get("quote", {}) or {}
    ind = ctx.get("indicators", {}) or {}
    return "\n".join([
        f"标的：{quote.get('symbol', ctx.get('symbol', '?'))} "
        f"({quote.get('name', '')})，{quote.get('market', '?')} 市场。",
        f"请站在【{side_cn}】立场，基于下列证据组织你的论证。",
        "",
        "== 价格 / 指标快照 ==",
        json.dumps({"quote": _quote_brief(quote), "indicators": ind},
                   ensure_ascii=False),
        "",
        "== 三方分析师意见 (JSON) ==",
        json.dumps(opinions, ensure_ascii=False),
        "",
        "只返回符合 researcher schema 的 JSON 对象。",
    ])


async def _run_researcher(side: str, system: str, prompt: str,
                          provider: Optional[str]) -> dict:
    try:
        raw, prov = await brain.run_schema(prompt, system, RESEARCHER_SCHEMA, provider)
        return _normalize_researcher(raw, side, prov)
    except Exception as e:  # noqa: BLE001
        print(f"[deepanalysis] researcher '{side}' failed: {e}")
        return _failed_researcher(side)


def _build_risk_synth_prompt(ctx: dict, opinions: list[dict],
                             bull: dict, bear: dict) -> str:
    quote = ctx.get("quote", {}) or {}
    ind = ctx.get("indicators", {}) or {}
    return "\n".join([
        f"标的：{quote.get('symbol', ctx.get('symbol', '?'))} "
        f"({quote.get('name', '')})，{quote.get('market', '?')} 市场。",
        "请综合三位分析师意见与下面的多空辩论，做风险调整后的最终交易决策。",
        "",
        "== 价格 / 指标快照 ==",
        json.dumps({"quote": _quote_brief(quote), "indicators": ind},
                   ensure_ascii=False),
        "",
        "== 三方分析师意见 (JSON) ==",
        json.dumps(opinions, ensure_ascii=False),
        "",
        "== 多头研究员 ==",
        json.dumps(bull, ensure_ascii=False),
        "",
        "== 空头研究员 ==",
        json.dumps(bear, ensure_ascii=False),
        "",
        "请裁决多空交锋并给出风险调整后的结构化决策。"
        "rationale/key_risks 用简体中文。只返回符合 decision schema 的 JSON 对象。",
    ])


# --------------------------------------------------------------------------- #
# Public entrypoint
# --------------------------------------------------------------------------- #
async def deep_analyze(symbol: str, ctx: dict, provider: str = None,
                       debate: bool = False) -> dict:
    """Run the full multi-agent pipeline for one symbol.

    Returns:
        {
          "symbol": str,
          "analysts": [opinion dicts (dimension/stance/score/summary/
                       key_points/provider)],
          "decision": models.Decision OBJECT (caller persists/serializes),
          "ts": float,
        }
    Never raises: failed analysts become neutral placeholders, and a failed
    synthesis falls back to brain.decide(ctx) (which itself degrades to a
    mechanical decision).
    """
    # ensure the symbol is present in ctx for downstream coercion helpers
    ctx = dict(ctx)
    ctx.setdefault("symbol", symbol)

    # 1) three specialist analysts, concurrently --------------------------- #
    analyst_specs = [
        ("技术面", _TECH_SYSTEM, _build_tech_prompt(ctx)),
        ("基本面·估值", _FUND_SYSTEM, _build_fund_prompt(ctx)),
        ("消息面·情绪", _SENT_SYSTEM, _build_sent_prompt(ctx)),
    ]
    results = await asyncio.gather(
        *[_run_analyst(dim, sys_p, usr_p, provider)
          for dim, sys_p, usr_p in analyst_specs],
        return_exceptions=True,
    )
    # gather(return_exceptions=True) shouldn't yield exceptions here because
    # _run_analyst swallows its own — but stay defensive in case it ever does.
    opinions: list[dict] = []
    for (dim, _, _), res in zip(analyst_specs, results):
        if isinstance(res, dict):
            opinions.append(res)
        else:
            print(f"[deepanalysis] analyst '{dim}' raised through gather: {res}")
            opinions.append(_failed_opinion(dim))

    # 2) optional bull/bear DEBATE round (concurrent) --------------------- #
    researchers: Optional[dict] = None
    if debate:
        bull_res, bear_res = await asyncio.gather(
            _run_researcher("bull", _BULL_SYSTEM,
                            _build_researcher_prompt(ctx, opinions, "多头"), provider),
            _run_researcher("bear", _BEAR_SYSTEM,
                            _build_researcher_prompt(ctx, opinions, "空头"), provider),
            return_exceptions=True,
        )
        bull = bull_res if isinstance(bull_res, dict) else _failed_researcher("bull")
        bear = bear_res if isinstance(bear_res, dict) else _failed_researcher("bear")
        researchers = {"bull": bull, "bear": bear}

    # 3) synthesis agent -> final structured decision ---------------------- #
    # With debate on, the CIO adjudicates the bull/bear clash under an explicit
    # risk mandate; without it, the original 3-analyst synthesis.
    decision = None
    try:
        if debate and researchers is not None:
            synth_prompt = _build_risk_synth_prompt(
                ctx, opinions, researchers["bull"], researchers["bear"])
            synth_system = RISK_SYNTHESIS_SYSTEM
            tag = "deep-debate"
        else:
            synth_prompt = _build_synth_prompt(ctx, opinions)
            synth_system = SYNTHESIS_SYSTEM
            tag = "deep"
        raw, prov_model = await brain.run_schema(
            synth_prompt, synth_system, DECISION_SCHEMA, provider)
        decision = _coerce_decision(raw, ctx, tag, prov_model)
    except Exception as e:                                    # noqa: BLE001
        print(f"[deepanalysis] synthesis failed: {e}; falling back to brain.decide")
        try:
            decision = await brain.decide(ctx, provider)
        except Exception as e2:                               # noqa: BLE001
            # brain.decide should already self-fallback to a mechanical decision,
            # but guard the absolute worst case so deep_analyze never raises.
            print(f"[deepanalysis] brain.decide also failed: {e2}; mechanical fallback")
            from .ai.brain import mechanical_decision
            decision = mechanical_decision(ctx)

    # 4) assemble result --------------------------------------------------- #
    out = {
        "symbol": symbol,
        "analysts": opinions,
        "decision": decision,   # models.Decision OBJECT — caller serializes
        "ts": time.time(),
    }
    if researchers is not None:
        out["researchers"] = researchers
    return out
