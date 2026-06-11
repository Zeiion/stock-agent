"""Anthropic Messages API tier for the AI decision brain.

Called by :class:`app.ai.brain.AIBrain._run_tier` when the active provider is
``"anthropic"`` (or ``"auto"`` with an ANTHROPIC_API_KEY present).

Public surface (imported by brain.py via these exact names):
    def is_available() -> bool
    async def run(prompt, system, schema, *, model, api_key, timeout_s) -> dict

``run`` returns a plain ``dict`` matching ``DECISION_SCHEMA``'s required keys.
Any failure raises an Exception; brain.py catches it and falls back to the next
tier (or the mechanical decision).

Structured output is obtained via FORCED TOOL USE — the most SDK-version-robust
way to guarantee a typed object: we define a single ``submit_decision`` tool
whose ``input_schema`` is the shared ``DECISION_SCHEMA`` and force the model to
call it (``tool_choice={"type": "tool", "name": "submit_decision"}``). The
decision then lands in the ``tool_use`` content block's ``.input`` dict.
"""
from __future__ import annotations

import asyncio
import json
from typing import Any


def is_available() -> bool:
    """True if the official ``anthropic`` SDK is importable."""
    try:
        import anthropic  # noqa: F401
        return True
    except Exception:
        return False


def _strip_json_fences(text: str) -> str:
    """Best-effort: pull a JSON object out of a (possibly fenced) text reply."""
    s = (text or "").strip()
    if s.startswith("```"):
        # Drop the opening fence line (``` or ```json) and the closing fence.
        s = s.split("\n", 1)[1] if "\n" in s else ""
        if s.rstrip().endswith("```"):
            s = s.rstrip()[: -len("```")]
        s = s.strip()
    # If there's leading/trailing prose, isolate the outermost {...}.
    if not s.startswith("{"):
        start = s.find("{")
        end = s.rfind("}")
        if start != -1 and end != -1 and end > start:
            s = s[start : end + 1]
    return s


def _extract_decision(message: Any) -> dict:
    """Find the forced tool_use block (or fall back to a JSON text block)."""
    content = getattr(message, "content", None) or []

    # Preferred path: the submit_decision tool_use block.
    for block in content:
        if getattr(block, "type", None) == "tool_use":
            data = getattr(block, "input", None)
            if isinstance(data, dict):
                return data

    # Fallback: parse the first text block as JSON.
    for block in content:
        if getattr(block, "type", None) == "text":
            raw = _strip_json_fences(getattr(block, "text", "") or "")
            if raw:
                data = json.loads(raw)  # raises on bad JSON -> bubbles up
                if isinstance(data, dict):
                    return data

    raise ValueError("anthropic: no tool_use or parseable JSON in response")


async def run(
    prompt: str,
    system: str,
    schema: dict,
    *,
    model: str,
    api_key: str,
    timeout_s: int,
) -> dict:
    """Run one decision request against the Anthropic Messages API.

    Returns a plain dict matching DECISION_SCHEMA's required keys. Raises on any
    failure (no key, SDK import error, timeout, network error, bad output).
    """
    if not api_key:
        raise RuntimeError("anthropic: no api_key provided")

    from anthropic import AsyncAnthropic

    client = AsyncAnthropic(api_key=api_key)

    tool = {
        "name": "submit_decision",
        "description": "Return the trading decision.",
        "input_schema": schema,
    }

    kwargs: dict[str, Any] = {
        "model": model,
        "max_tokens": 1500,
        "system": system,
        "tools": [tool],
        "tool_choice": {"type": "tool", "name": "submit_decision"},
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.2,
    }

    async def _call() -> Any:
        try:
            return await client.messages.create(**kwargs)
        except TypeError:
            # Older/newer SDKs may reject a kwarg (e.g. temperature). Retry
            # without the optional sampling param.
            kwargs.pop("temperature", None)
            return await client.messages.create(**kwargs)

    try:
        message = await asyncio.wait_for(_call(), timeout=timeout_s)
    finally:
        # Release the underlying HTTP client; ignore close errors.
        close = getattr(client, "close", None)
        if close is not None:
            try:
                await close()
            except Exception:
                pass

    return _extract_decision(message)
