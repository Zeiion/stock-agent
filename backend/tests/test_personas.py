"""Unit tests for the investor-persona panel (no network / no real LLM).

`brain.run_schema` is monkeypatched so every "model call" is deterministic; we
verify persona wiring, the confidence-weighted aggregation, abstention on
failure, and persona-list resolution.
"""
from __future__ import annotations

import pytest

from app import personas
from app.personas import aggregate, resolve_personas, build_context_block


def _op(key, label, signal, conf, action="HOLD"):
    return {"key": key, "label": label, "signal": signal, "action": action,
            "confidence": conf, "reasoning": "x", "key_points": [], "provider": "t"}


# --------------------------------------------------------------------------- #
# Pure aggregation
# --------------------------------------------------------------------------- #
def test_aggregate_bullish_consensus():
    ops = [
        _op("buffett", "巴菲特", "bullish", 80),
        _op("lynch", "林奇", "bullish", 70),
        _op("wood", "木头姐", "bullish", 90),
        _op("burry", "伯里", "bearish", 60),
    ]
    c = aggregate(ops)
    assert c["signal"] == "bullish"
    assert c["action"] in ("BUY", "ADD")
    assert c["score"] > 0
    assert c["counts"] == {"bullish": 3, "bearish": 1, "neutral": 0}
    assert c["dissenters"] == ["伯里"]
    assert 0 <= c["confidence"] <= 100


def test_aggregate_bearish_consensus():
    ops = [
        _op("burry", "伯里", "bearish", 90),
        _op("taleb", "塔勒布", "bearish", 80),
        _op("buffett", "巴菲特", "bullish", 20),
    ]
    c = aggregate(ops)
    assert c["signal"] == "bearish"
    assert c["action"] in ("SELL", "REDUCE")
    assert c["score"] < 0
    assert c["dissenters"] == ["巴菲特"]


def test_aggregate_neutral_when_balanced_or_abstaining():
    # opposing equal-confidence votes -> score ~0 -> neutral/HOLD
    ops = [_op("a", "A", "bullish", 50), _op("b", "B", "bearish", 50)]
    c = aggregate(ops)
    assert c["signal"] == "neutral"
    assert c["action"] == "HOLD"
    assert c["score"] == 0.0
    # all abstaining (confidence 0) -> no weight, neutral, zero confidence
    allzero = aggregate([_op("a", "A", "neutral", 0), _op("b", "B", "neutral", 0)])
    assert allzero["signal"] == "neutral"
    assert allzero["confidence"] == 0
    assert allzero["participation"] == 0.0


def test_aggregate_abstentions_carry_no_weight():
    # one strong bull + two zero-confidence abstainers: still bullish, but
    # participation (and thus consensus confidence) is dampened.
    ops = [
        _op("wood", "木头姐", "bullish", 100),
        _op("x", "X", "neutral", 0),
        _op("y", "Y", "neutral", 0),
    ]
    c = aggregate(ops)
    assert c["signal"] == "bullish"
    assert c["score"] == 1.0          # weight only from the one directional vote
    assert c["participation"] == 0.33   # 1 of 3 took a side (rounded to 2dp)


# --------------------------------------------------------------------------- #
# Persona-list resolution
# --------------------------------------------------------------------------- #
def test_resolve_personas():
    assert resolve_personas(None) == personas.DEFAULT_PANEL
    assert resolve_personas("all") == personas.PERSONA_KEYS
    # unknown keys dropped; valid kept
    got = resolve_personas(["buffett", "not_a_persona", "burry"])
    assert got == ["buffett", "burry"]
    # all-unknown -> fall back to default
    assert resolve_personas(["nope"]) == personas.DEFAULT_PANEL
    # the default panel is a real subset of the eligible personas
    assert set(personas.DEFAULT_PANEL).issubset(set(personas.PERSONA_KEYS))
    assert len(personas.PERSONA_KEYS) >= 10   # we ship a rich roster


def test_context_block_is_compact_and_mentions_symbol():
    ctx = {"symbol": "US:AAPL",
           "quote": {"symbol": "US:AAPL", "name": "Apple", "market": "US",
                     "last": 190.0, "change_pct": 1.2},
           "indicators": {"rsi14": 55.0, "ma20": 188.0}}
    block = build_context_block(ctx)
    assert "US:AAPL" in block
    assert "技术指标快照" in block and "基本面快照" in block


# --------------------------------------------------------------------------- #
# Full panel run with a fake brain
# --------------------------------------------------------------------------- #
def _fake_run_schema_factory(label_to_reply):
    async def _fake(prompt, system, schema, provider=None):
        for label, reply in label_to_reply.items():
            if label in system:
                return reply, "fake:test-model"
        return ({"signal": "neutral", "action": "HOLD", "confidence": 0,
                 "reasoning": "n/a", "key_points": []}, "fake:test-model")
    return _fake


@pytest.fixture
def _ctx():
    return {"symbol": "US:AAPL",
            "quote": {"symbol": "US:AAPL", "name": "Apple", "market": "US",
                      "last": 190.0, "change_pct": 1.2},
            "indicators": {"rsi14": 55.0}}


async def test_run_panel_wires_personas_and_aggregates(monkeypatch, _ctx):
    replies = {
        "巴菲特": {"signal": "bullish", "action": "BUY", "confidence": 80,
                  "reasoning": "护城河", "key_points": ["高ROE"]},
        "木头姐": {"signal": "bullish", "action": "ADD", "confidence": 90,
                  "reasoning": "创新", "key_points": []},
        "迈克尔·伯里": {"signal": "bearish", "action": "SELL", "confidence": 70,
                     "reasoning": "泡沫", "key_points": []},
    }
    monkeypatch.setattr(personas.brain, "run_schema",
                        _fake_run_schema_factory(replies))
    res = await personas.run_panel("US:AAPL", _ctx,
                                   personas=["buffett", "wood", "burry"])
    assert res["symbol"] == "US:AAPL"
    assert len(res["panel"]) == 3
    by_key = {o["key"]: o for o in res["panel"]}
    assert by_key["buffett"]["signal"] == "bullish"
    assert by_key["buffett"]["confidence"] == 80
    assert by_key["burry"]["signal"] == "bearish"
    # 2 bull (80,90) vs 1 bear (70) -> net bullish consensus
    assert res["consensus"]["signal"] == "bullish"
    assert res["consensus"]["counts"]["bullish"] == 2


async def test_run_panel_abstains_on_failure(monkeypatch, _ctx):
    async def _boom(*a, **k):
        raise RuntimeError("all tiers failed")
    monkeypatch.setattr(personas.brain, "run_schema", _boom)
    res = await personas.run_panel("US:AAPL", _ctx, personas=["buffett", "burry"])
    assert len(res["panel"]) == 2
    assert all(o["signal"] == "neutral" and o["confidence"] == 0
               for o in res["panel"])
    assert res["consensus"]["signal"] == "neutral"
    assert res["consensus"]["confidence"] == 0
