"""AI pre-market / watchlist briefing generator.

ONE LLM call over the entire watchlist (cost-efficient) that produces a concise
Chinese morning brief: overall vibe + notable movers + actionable opportunities +
risks. The caller prepares a compact per-symbol snapshot list; this module only
formats the prompt, runs it through the shared brain, and normalizes the result.

Robustness contract:
  * OFF-by-default / graceful: if the brain has no usable AI tier (or anything
    raises) we return a deterministic mechanical fallback computed from `items`.
  * NEVER raises. NEVER breaks the app at import time (no work done on import;
    `brain` is imported lazily inside generate()).
  * Always returns a dict carrying "provider" (the "provider:model" string, or
    "fallback") and "generated_ts" (this module's import time).
"""
from __future__ import annotations

import json
import time
from typing import Any, Optional

# Import time of this module — exposed on every returned briefing.
_IMPORT_TS = time.time()


# --------------------------------------------------------------------------- #
# Strict JSON schema for the structured LLM reply.
# additionalProperties False everywhere; every property listed in `required`.
# --------------------------------------------------------------------------- #
BRIEFING_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "summary": {
            "type": "string",
            "description": "中文,2-4句,概述盘前整体观察(氛围/板块/关注点)",
        },
        "movers": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "symbol": {"type": "string"},
                    "note": {"type": "string"},
                },
                "required": ["symbol", "note"],
            },
        },
        "opportunities": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "symbol": {"type": "string"},
                    "action": {
                        "type": "string",
                        "description": "BUY / WATCH / REDUCE 等",
                    },
                    "reason": {"type": "string"},
                },
                "required": ["symbol", "action", "reason"],
            },
        },
        "risks": {
            "type": "array",
            "items": {"type": "string"},
        },
    },
    "required": ["summary", "movers", "opportunities", "risks"],
}


_SYSTEM = (
    "你是机构投研晨会主持人,基于自选股快照给出简明盘前简报,只输出JSON,不构成投资建议"
)


def _safe_pct(item: dict[str, Any]) -> float:
    """abs(change_pct) tolerant of None / non-numeric / missing."""
    v = item.get("change_pct")
    try:
        return abs(float(v))
    except (TypeError, ValueError):
        return 0.0


def _fmt_pct(item: dict[str, Any]) -> str:
    v = item.get("change_pct")
    try:
        f = float(v)
    except (TypeError, ValueError):
        return "—"
    return f"{f:+.2f}%"


def _fallback(items: list[dict[str, Any]]) -> dict[str, Any]:
    """Deterministic mechanical overview when AI is unavailable / fails."""
    rows = items if isinstance(items, list) else []
    ranked = sorted(rows, key=_safe_pct, reverse=True)
    movers: list[dict[str, str]] = []
    for it in ranked[:5]:
        if not isinstance(it, dict):
            continue
        if _safe_pct(it) <= 0:
            continue
        sym = str(it.get("symbol", "?"))
        name = it.get("name") or ""
        label = f"{name} " if name else ""
        movers.append({"symbol": sym, "note": f"{label}{_fmt_pct(it)}"})
    return {
        "summary": "AI 简报生成失败,以下为基于行情的机械概览",
        "movers": movers,
        "opportunities": [],
        "risks": [],
        "provider": "fallback",
    }


def _coerce(raw: dict[str, Any]) -> dict[str, Any]:
    """Normalize a model reply into the briefing shape (defensive, never raises)."""
    out: dict[str, Any] = {
        "summary": "",
        "movers": [],
        "opportunities": [],
        "risks": [],
    }
    if not isinstance(raw, dict):
        return out
    out["summary"] = str(raw.get("summary", "") or "").strip()

    movers = raw.get("movers")
    if isinstance(movers, list):
        for m in movers:
            if isinstance(m, dict) and (m.get("symbol") is not None):
                out["movers"].append({
                    "symbol": str(m.get("symbol", "")),
                    "note": str(m.get("note", "") or ""),
                })

    opps = raw.get("opportunities")
    if isinstance(opps, list):
        for o in opps:
            if isinstance(o, dict) and (o.get("symbol") is not None):
                out["opportunities"].append({
                    "symbol": str(o.get("symbol", "")),
                    "action": str(o.get("action", "") or "").upper(),
                    "reason": str(o.get("reason", "") or ""),
                })

    risks = raw.get("risks")
    if isinstance(risks, list):
        out["risks"] = [str(r) for r in risks if r is not None]
    elif risks:
        out["risks"] = [str(risks)]

    return out


async def generate(items: list[dict[str, Any]],
                   provider: Optional[str] = None) -> dict[str, Any]:
    """Run ONE structured LLM call over the prepared watchlist snapshot.

    `items` is a list of per-symbol dicts prepared by the caller, e.g.::

        {"symbol","market","name","last","change_pct",
         "tags":[..], "sentiment":"bullish/bearish/neutral/-",
         "news_titles":[".."]}

    Returns a dict with keys: summary, movers, opportunities, risks, provider,
    generated_ts. NEVER raises — any failure yields a mechanical fallback.
    """
    rows = items if isinstance(items, list) else []

    # Nothing to brief on: short, honest, deterministic.
    if not rows:
        return {
            "summary": "自选股列表为空,暂无盘前观察。",
            "movers": [],
            "opportunities": [],
            "risks": [],
            "provider": "fallback",
            "generated_ts": _IMPORT_TS,
        }

    try:
        # Lazy import so a missing/broken AI layer can never break import time.
        from .ai.brain import brain

        try:
            payload = json.dumps(rows, ensure_ascii=False, default=str)
        except (TypeError, ValueError):
            payload = json.dumps(
                [r for r in rows if isinstance(r, dict)],
                ensure_ascii=False, default=str,
            )

        prompt = (
            "以下是我的自选股盘前快照(JSON 数组,每项含代码/市场/名称/最新价/"
            "涨跌幅/技术指标标签/情绪/新闻标题):\n"
            f"{payload}\n\n"
            "请基于这些数据撰写一份简明的盘前晨会简报:\n"
            "1) summary: 用中文 2-4 句概述整体氛围、活跃板块与今日重点关注;\n"
            "2) movers: 列出最值得关注的异动标的及一句话原因;\n"
            "3) opportunities: 给出可操作的关注点,action 用 BUY/WATCH/REDUCE,"
            "并给出简短理由;\n"
            "4) risks: 列出需要警惕的风险点。\n"
            "只输出符合 schema 的 JSON,不要附加任何解释文字,不构成投资建议。"
        )

        raw, prov = await brain.run_schema(prompt, _SYSTEM, BRIEFING_SCHEMA, provider)
        result = _coerce(raw)
        result["provider"] = prov or "unknown"
        result["generated_ts"] = _IMPORT_TS
        return result
    except Exception as e:  # noqa: BLE001 — must never propagate
        print(f"[briefing] generate failed: {e}")
        fb = _fallback(rows)
        fb["generated_ts"] = _IMPORT_TS
        return fb
