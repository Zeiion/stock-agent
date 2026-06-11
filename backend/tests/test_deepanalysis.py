"""Tests for the multi-agent deep-analysis orchestrator, focused on the
bull/bear debate layer. `brain.run_schema` is monkeypatched so the pipeline is
deterministic and offline; we route replies by inspecting the system prompt.
"""
from __future__ import annotations

import pytest

from app import deepanalysis
from app.models import Decision


def _ctx():
    return {
        "symbol": "US:AAPL",
        "quote": {"symbol": "US:AAPL", "name": "Apple", "market": "US",
                  "last": 190.0, "change_pct": 1.0, "delayed": False},
        "indicators": {"rsi14": 58.0, "ma20": 185.0},
        "recent": [], "news": [], "fundamentals": {}, "social": {},
    }


def _router(monkeypatch):
    """Install a fake run_schema that returns role-appropriate JSON based on the
    system prompt, and records which systems were invoked."""
    seen: list[str] = []

    async def _fake(prompt, system, schema, provider=None):
        seen.append(system)
        # NOTE: order matters — the synthesis system prompt also *mentions* the
        # 多头/空头研究员, so match the CIO/synthesis role FIRST.
        if "首席投资决策官" in system:   # synthesis (plain or risk-adjusted)
            return ({"action": "BUY", "conviction": 3, "horizon": "swing",
                     "rationale": "综合判断", "key_risks": ["回撤"],
                     "entry_zone": [188.0], "stop_loss": 180.0,
                     "take_profit": [210.0], "data_freshness_ok": True}, "fake:m")
        if "你是一名【多头研究员】" in system:
            return ({"thesis": "上行趋势确立", "arguments": ["MA20上方", "动量强"],
                     "rebuttal": "估值偏高但可消化", "confidence": 4}, "fake:m")
        if "你是一名【空头研究员】" in system:
            return ({"thesis": "估值与拥挤风险", "arguments": ["RSI偏高"],
                     "rebuttal": "趋势虽强但脆弱", "confidence": 3}, "fake:m")
        # analyst replies
        return ({"dimension": "x", "stance": "bullish", "score": 4,
                 "summary": "ok", "key_points": ["a"]}, "fake:m")

    monkeypatch.setattr(deepanalysis.brain, "run_schema", _fake)
    return seen


async def test_deep_analyze_default_has_no_debate(monkeypatch):
    seen = _router(monkeypatch)
    res = await deepanalysis.deep_analyze("US:AAPL", _ctx(), debate=False)
    assert "researchers" not in res
    assert len(res["analysts"]) == 3
    assert isinstance(res["decision"], Decision)
    # neither researcher system prompt should have been used
    assert not any("研究员" in s for s in seen)


async def test_deep_analyze_debate_runs_bull_bear_and_risk_synth(monkeypatch):
    seen = _router(monkeypatch)
    res = await deepanalysis.deep_analyze("US:AAPL", _ctx(), debate=True)
    # researchers present and well-formed
    assert "researchers" in res
    bull = res["researchers"]["bull"]
    bear = res["researchers"]["bear"]
    assert bull["side"] == "bull" and bull["confidence"] == 4
    assert bear["side"] == "bear" and bear["arguments"] == ["RSI偏高"]
    # the risk-adjusted synthesis ran (its system prompt mentions 风险管理职责)
    assert any("风险管理职责" in s for s in seen)
    # final decision still a normal Decision, tagged as a debate synthesis
    assert isinstance(res["decision"], Decision)
    assert res["decision"].provider == "deep-debate"
    assert res["decision"].action == "BUY"


async def test_debate_survives_a_failing_researcher(monkeypatch):
    async def _fake(prompt, system, schema, provider=None):
        if "首席投资决策官" in system:   # match synthesis before researcher mentions
            return ({"action": "HOLD", "conviction": 2, "horizon": "swing",
                     "rationale": "r", "key_risks": [], "data_freshness_ok": True},
                    "fake:m")
        if "你是一名【空头研究员】" in system:
            raise RuntimeError("bear tier down")
        if "你是一名【多头研究员】" in system:
            return ({"thesis": "牛", "arguments": ["x"], "rebuttal": "y",
                     "confidence": 5}, "fake:m")
        return ({"dimension": "x", "stance": "neutral", "score": 2,
                 "summary": "s", "key_points": []}, "fake:m")

    monkeypatch.setattr(deepanalysis.brain, "run_schema", _fake)
    res = await deepanalysis.deep_analyze("US:AAPL", _ctx(), debate=True)
    # the bear researcher failed -> neutral placeholder, pipeline still completes
    assert res["researchers"]["bear"]["thesis"].startswith("(该方")
    assert res["researchers"]["bull"]["confidence"] == 5
    assert isinstance(res["decision"], Decision)
