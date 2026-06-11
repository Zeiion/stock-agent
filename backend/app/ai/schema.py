"""The structured-output JSON Schema the AI brain must return.

Shared verbatim by all three tiers:
  - Anthropic Messages API  -> output_config.format.json_schema
  - claude -p               -> --json-schema (object lands in .structured_result)
  - codex exec              -> --output-schema FILE
"""
from __future__ import annotations

DECISION_SCHEMA: dict = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "symbol": {"type": "string"},
        "action": {
            "type": "string",
            "enum": ["BUY", "SELL", "HOLD", "REDUCE", "ADD"],
        },
        "conviction": {"type": "integer", "minimum": 1, "maximum": 5},
        "horizon": {
            "type": "string",
            "enum": ["intraday", "swing", "position"],
        },
        "entry_zone": {
            "type": ["array", "null"],
            "items": {"type": "number"},
            "description": "1-2 price levels forming a suggested entry band",
        },
        "stop_loss": {"type": ["number", "null"]},
        "take_profit": {
            "type": ["array", "null"],
            "items": {"type": "number"},
        },
        "rationale": {
            "type": "string",
            "description": "Concise reasoning citing the provided indicators/price.",
        },
        "key_risks": {"type": "array", "items": {"type": "string"}},
        "data_freshness_ok": {
            "type": "boolean",
            "description": "False if the data is too stale/delayed to act on.",
        },
    },
    # NOTE: OpenAI strict structured output (codex --output-schema) requires that
    # `required` list EVERY property when additionalProperties is false. The
    # genuinely-optional fields are typed nullable, so the model returns them as
    # null when not applicable. Claude/Anthropic accept this same schema.
    "required": [
        "symbol", "action", "conviction", "horizon", "rationale", "key_risks",
        "data_freshness_ok", "entry_zone", "stop_loss", "take_profit",
    ],
}

# The semantically-required subset used by the CLI parsers to validate a reply
# (a free-text fallback parse may legitimately omit the nullable fields).
REQUIRED_KEYS = [
    "symbol", "action", "conviction", "horizon",
    "rationale", "key_risks", "data_freshness_ok",
]
