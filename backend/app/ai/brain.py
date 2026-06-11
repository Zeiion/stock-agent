"""AIBrain — turns a market context into a structured Decision.

Three interchangeable tiers (user picks via settings.ai_provider, switchable at
runtime through the /api/settings endpoint):
    "anthropic" -> Anthropic Messages API (needs ANTHROPIC_API_KEY)
    "claude"    -> `claude -p` headless CLI  (uses your existing CLI auth)
    "codex"     -> `codex exec` headless CLI (uses your existing CLI auth)
    "auto"      -> anthropic if key else claude, with graceful fallback

Each tier module exposes:
    async def run(prompt, system, schema, *, model, timeout_s, ...) -> dict
    def is_available() -> bool

If every tier fails the brain returns a deterministic mechanical decision so the
monitor never blocks on the AI being up.
"""
from __future__ import annotations

import asyncio
from typing import Any, Optional

from ..config import settings
from ..models import Action, Decision
from .prompts import SYSTEM_PROMPT, build_decision_prompt
from .schema import DECISION_SCHEMA, REQUIRED_KEYS


def _num(x):
    """Coerce to float or None. Free-text-parsed model replies can return strings
    or wrong types for numeric fields; never let those reach the DB / paper broker."""
    if isinstance(x, bool):          # avoid True -> 1.0 surprises
        return None
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def _num_list(x):
    """Coerce to a list[float] (or None). Wraps a scalar into a 1-element list."""
    if isinstance(x, (list, tuple)):
        out = [v for v in (_num(i) for i in x) if v is not None]
        return out or None
    n = _num(x)
    return [n] if n is not None else None


def _str_list(x):
    """Coerce key_risks to list[str] without exploding a string into characters."""
    if isinstance(x, (list, tuple)):
        return [str(i) for i in x if i is not None]
    return [str(x)] if x else []


def _coerce_decision(raw: dict, ctx: dict, provider: str, model: str) -> Decision:
    symbol = raw.get("symbol") or ctx.get("symbol") or ctx.get("quote", {}).get("symbol", "?")
    action = str(raw.get("action", "HOLD")).upper()
    if action not in {a.value for a in Action}:
        action = "HOLD"
    conv = raw.get("conviction", 2)
    try:
        conv = max(1, min(5, int(conv)))
    except (TypeError, ValueError):
        conv = 2
    horizon = raw.get("horizon", "swing")
    if horizon not in ("intraday", "swing", "position"):
        horizon = "swing"
    return Decision(
        symbol=symbol,
        action=action,
        conviction=conv,
        horizon=horizon,
        rationale=str(raw.get("rationale", "")).strip() or "(no rationale)",
        key_risks=_str_list(raw.get("key_risks")),
        entry_zone=_num_list(raw.get("entry_zone")),
        stop_loss=_num(raw.get("stop_loss")),
        take_profit=_num_list(raw.get("take_profit")),
        data_freshness_ok=bool(raw.get("data_freshness_ok", True)),
        strategy=ctx.get("strategy", "balanced"),
        provider=provider,
        model=model,
        snapshot={"indicators": ctx.get("indicators", {}),
                  "quote": ctx.get("quote", {}),
                  "trigger": ctx.get("trigger")},
    )


def mechanical_decision(ctx: dict) -> Decision:
    """Deterministic rule-of-thumb used when all AI tiers are unavailable."""
    ind = ctx.get("indicators", {}) or {}
    quote = ctx.get("quote", {}) or {}
    rsi = ind.get("rsi14")
    j = ind.get("j")
    ma5, ma20 = ind.get("ma5"), ind.get("ma20")
    action, conv, why = "HOLD", 1, []
    if rsi is not None and rsi <= 30:
        action, why = "ADD", why + [f"RSI {rsi} oversold"]
    elif rsi is not None and rsi >= 70:
        action, why = "REDUCE", why + [f"RSI {rsi} overbought"]
    if ma5 and ma20:
        why.append("MA5>MA20" if ma5 > ma20 else "MA5<MA20")
    if j is not None and j <= 20 and action == "HOLD":
        action = "ADD"; why.append(f"KDJ-J {j} oversold")
    return Decision(
        symbol=ctx.get("symbol") or quote.get("symbol", "?"),
        action=action, conviction=conv, horizon="swing",
        rationale="AI unavailable — mechanical fallback: " + "; ".join(why or ["neutral"]),
        key_risks=["Mechanical signal only; no AI judgment applied"],
        data_freshness_ok=not quote.get("delayed", False),
        strategy=ctx.get("strategy", "balanced"),
        provider="fallback", model="rule-based",
        snapshot={"indicators": ind, "quote": quote, "trigger": ctx.get("trigger")},
    )


class AIBrain:
    def __init__(self) -> None:
        self.provider = settings.ai_provider
        self.ensemble = settings.ai_ensemble

    def set_provider(self, provider: str) -> None:
        if provider in ("auto", "anthropic", "claude", "codex"):
            self.provider = provider

    def set_ensemble(self, on: bool) -> None:
        self.ensemble = bool(on)

    # ---- tier resolution -------------------------------------------------- #
    def _resolve_order(self) -> list[str]:
        if self.provider == "auto":
            order = []
            if settings.anthropic_api_key:
                order.append("anthropic")
            order += ["claude", "codex"]
            return order
        # explicit choice first, then the others as fallback
        rest = [p for p in ("anthropic", "claude", "codex") if p != self.provider]
        return [self.provider] + rest

    async def _run_tier(self, tier: str, prompt: str,
                        schema: Optional[dict] = None,
                        system: Optional[str] = None) -> tuple[dict, str]:
        """Return (raw_dict, model_id) for an arbitrary schema. Raises on failure."""
        schema = schema or DECISION_SCHEMA
        system = system or SYSTEM_PROMPT
        if tier == "anthropic":
            from . import anthropic_api
            raw = await anthropic_api.run(
                prompt, system, schema,
                model=settings.claude_model, api_key=settings.anthropic_api_key,
                timeout_s=settings.ai_timeout_s)
            return raw, settings.claude_model
        if tier == "claude":
            from . import claude_cli
            raw = await claude_cli.run(
                prompt, system, schema,
                model=settings.claude_model, timeout_s=settings.ai_timeout_s,
                login_shell=settings.ai_login_shell)
            return raw, settings.claude_model
        if tier == "codex":
            from . import codex_cli
            raw = await codex_cli.run(
                prompt, system, schema,
                model=settings.codex_model, timeout_s=settings.ai_timeout_s,
                login_shell=settings.ai_login_shell)
            return raw, settings.codex_model or "codex-default"
        raise ValueError(f"unknown tier {tier}")

    async def run_schema(self, prompt: str, system: str, schema: dict,
                         provider: Optional[str] = None) -> tuple[dict, str]:
        """Public: run an arbitrary structured prompt through the active tier with
        fallback. Returns (raw_dict, 'provider:model'). Raises if every tier fails.
        Used by the multi-agent deep-analysis orchestrator."""
        order = ([provider] if provider else []) + self._resolve_order()
        seen, tiers, last = set(), [], None
        for t in order:
            if t and t not in seen:
                seen.add(t); tiers.append(t)
        for tier in tiers:
            try:
                raw, model = await self._run_tier(tier, prompt, schema, system)
                return raw, f"{tier}:{model}"
            except Exception as e:
                last = e
                print(f"[ai] run_schema tier {tier} failed: {e}")
        raise RuntimeError(f"all tiers failed: {last}")

    async def decide(self, ctx: dict, provider: Optional[str] = None) -> Decision:
        prompt = build_decision_prompt(ctx)
        order = ([provider] if provider else []) + self._resolve_order()
        # de-dup preserving order
        seen, tiers = set(), []
        for t in order:
            if t and t not in seen:
                seen.add(t); tiers.append(t)

        decision: Optional[Decision] = None
        used_tier = ""
        last_err: Optional[Exception] = None
        for tier in tiers:
            try:
                raw, model = await self._run_tier(tier, prompt)
                decision = _coerce_decision(raw, ctx, tier, model)
                used_tier = tier
                break
            except Exception as e:
                last_err = e
                print(f"[ai] tier {tier} failed: {e}")
                continue

        if decision is None:
            print(f"[ai] all tiers failed ({last_err}); using mechanical fallback")
            return mechanical_decision(ctx)

        # optional ensemble: get a second opinion from a different tier
        if self.ensemble and decision.conviction >= 4:
            other = next((t for t in ("codex", "claude", "anthropic")
                          if t != used_tier), None)
            if other:
                try:
                    raw2, model2 = await self._run_tier(other, prompt)
                    d2 = _coerce_decision(raw2, ctx, other, model2)
                    agree = d2.action == decision.action
                    decision.ensemble = {
                        "provider": other, "model": model2,
                        "action": d2.action, "conviction": d2.conviction,
                        "agree": agree, "rationale": d2.rationale[:400],
                    }
                    if not agree:
                        decision.conviction = max(1, decision.conviction - 1)
                        decision.key_risks.append(
                            f"Ensemble disagreement: {other} suggests {d2.action}")
                except Exception as e:
                    print(f"[ai] ensemble tier {other} failed: {e}")
        return decision


brain = AIBrain()
