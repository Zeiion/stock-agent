"""Pure unit tests for the stateless RulesEngine."""
from __future__ import annotations

from app.models import Alert, Quote, RuleType
from app.rules import RulesEngine


ENGINE = RulesEngine()


def _quote(last=100.0, prev_close=100.0, name="TestCo") -> Quote:
    return Quote(symbol="US:TEST", market="US", last=last, prev_close=prev_close,
                 name=name)


def _rule(rtype, params=None, rule_id=1, severity="normal", active=True) -> dict:
    return {"id": rule_id, "symbol": "US:TEST", "type": rtype,
            "params": params or {}, "severity": severity, "active": active}


def _eval(quote, ind, position, rule):
    return ENGINE.evaluate("US:TEST", quote, ind, position, [rule])


# --------------------------------------------------------------------------- #
# price thresholds
# --------------------------------------------------------------------------- #
def test_price_above_fires():
    rule = _rule(RuleType.PRICE_ABOVE.value, {"price": 200}, severity="critical")
    fired = _eval(_quote(last=205.0), {}, None, rule)
    assert len(fired) == 1
    a = fired[0]
    assert isinstance(a, Alert)
    assert a.rule_id == 1
    assert a.rule_type == RuleType.PRICE_ABOVE.value
    assert a.severity == "critical"
    assert "above threshold" in a.message


def test_price_above_does_not_fire_below():
    rule = _rule(RuleType.PRICE_ABOVE.value, {"price": 200})
    assert _eval(_quote(last=150.0), {}, None, rule) == []


def test_price_below_fires():
    rule = _rule(RuleType.PRICE_BELOW.value, {"price": 90})
    fired = _eval(_quote(last=85.0), {}, None, rule)
    assert len(fired) == 1
    assert "below threshold" in fired[0].message


# --------------------------------------------------------------------------- #
# pct move (abs vs prev_close)
# --------------------------------------------------------------------------- #
def test_pct_move_fires_on_large_move():
    rule = _rule(RuleType.PCT_MOVE.value, {"pct": 5})
    # +6% move
    fired = _eval(_quote(last=106.0, prev_close=100.0), {}, None, rule)
    assert len(fired) == 1
    assert "moved" in fired[0].message


def test_pct_move_fires_on_negative_move():
    rule = _rule(RuleType.PCT_MOVE.value, {"pct": 5})
    fired = _eval(_quote(last=93.0, prev_close=100.0), {}, None, rule)  # -7%
    assert len(fired) == 1


def test_pct_move_quiet_on_small_move():
    rule = _rule(RuleType.PCT_MOVE.value, {"pct": 5})
    assert _eval(_quote(last=102.0, prev_close=100.0), {}, None, rule) == []  # +2%


# --------------------------------------------------------------------------- #
# RSI thresholds
# --------------------------------------------------------------------------- #
def test_rsi_below_fires():
    rule = _rule(RuleType.RSI_BELOW.value, {"value": 30})
    fired = _eval(_quote(), {"rsi14": 25.0}, None, rule)
    assert len(fired) == 1
    assert "oversold" in fired[0].message


def test_rsi_below_quiet_when_high():
    rule = _rule(RuleType.RSI_BELOW.value, {"value": 30})
    assert _eval(_quote(), {"rsi14": 55.0}, None, rule) == []


def test_rsi_above_fires():
    rule = _rule(RuleType.RSI_ABOVE.value, {"value": 70})
    fired = _eval(_quote(), {"rsi14": 80.0}, None, rule)
    assert len(fired) == 1
    assert "overbought" in fired[0].message


# --------------------------------------------------------------------------- #
# stop loss / take profit need a held position
# --------------------------------------------------------------------------- #
def test_stop_loss_fires_with_position():
    rule = _rule(RuleType.STOP_LOSS.value, {"price": 90}, severity="critical")
    position = {"symbol": "US:TEST", "qty": 10.0, "avg_cost": 100.0}
    fired = _eval(_quote(last=88.0), {}, position, rule)
    assert len(fired) == 1
    assert "STOP-LOSS" in fired[0].message
    assert fired[0].severity == "critical"


def test_stop_loss_skipped_without_position():
    rule = _rule(RuleType.STOP_LOSS.value, {"price": 90})
    # price is below stop but no position held -> no alert
    assert _eval(_quote(last=88.0), {}, None, rule) == []


def test_take_profit_fires_with_position():
    rule = _rule(RuleType.TAKE_PROFIT.value, {"price": 120})
    position = {"symbol": "US:TEST", "qty": 10.0, "avg_cost": 100.0}
    fired = _eval(_quote(last=125.0), {}, position, rule)
    assert len(fired) == 1
    assert "TAKE-PROFIT" in fired[0].message


# --------------------------------------------------------------------------- #
# cross rules need both current + previous values
# --------------------------------------------------------------------------- #
def test_ma_golden_cross_fires():
    rule = _rule(RuleType.MA_CROSS.value, {"fast": 5, "slow": 20})
    ind = {"ma5": 105.0, "ma20": 100.0, "ma5_prev": 98.0, "ma20_prev": 100.0}
    fired = _eval(_quote(), ind, None, rule)
    assert len(fired) == 1
    assert "golden cross" in fired[0].message


def test_ma_death_cross_fires():
    rule = _rule(RuleType.MA_CROSS.value, {"fast": 5, "slow": 20})
    ind = {"ma5": 95.0, "ma20": 100.0, "ma5_prev": 102.0, "ma20_prev": 100.0}
    fired = _eval(_quote(), ind, None, rule)
    assert len(fired) == 1
    assert "death cross" in fired[0].message


def test_ma_cross_quiet_when_no_flip():
    rule = _rule(RuleType.MA_CROSS.value, {"fast": 5, "slow": 20})
    ind = {"ma5": 105.0, "ma20": 100.0, "ma5_prev": 104.0, "ma20_prev": 100.0}
    assert _eval(_quote(), ind, None, rule) == []


# --------------------------------------------------------------------------- #
# missing-indicator rules are SKIPPED, never crash
# --------------------------------------------------------------------------- #
def test_missing_indicator_skipped_not_crashed():
    # RSI rule with an empty indicator snapshot -> no alert, no exception
    rule = _rule(RuleType.RSI_BELOW.value, {"value": 30})
    assert _eval(_quote(), {}, None, rule) == []
    assert _eval(_quote(), {"rsi14": None}, None, rule) == []


def test_ma_cross_skipped_when_prev_missing():
    rule = _rule(RuleType.MA_CROSS.value, {"fast": 5, "slow": 20})
    # only current values present -> can't detect a cross -> skipped
    ind = {"ma5": 105.0, "ma20": 100.0}
    assert _eval(_quote(), ind, None, rule) == []


def test_ma_cross_skipped_for_unsupported_periods():
    # ma10/ma60 have no *_prev in the snapshot -> skipped
    rule = _rule(RuleType.MA_CROSS.value, {"fast": 10, "slow": 60})
    ind = {"ma10": 105.0, "ma60": 100.0, "ma5_prev": 1, "ma20_prev": 1}
    assert _eval(_quote(), ind, None, rule) == []


def test_inactive_rule_skipped():
    rule = _rule(RuleType.PRICE_ABOVE.value, {"price": 50}, active=False)
    assert _eval(_quote(last=999.0), {}, None, rule) == []


def test_unknown_rule_type_no_crash():
    rule = _rule("not_a_real_rule", {"foo": 1})
    assert _eval(_quote(), {}, None, rule) == []


def test_multiple_rules_evaluated_independently():
    rules = [
        _rule(RuleType.PRICE_ABOVE.value, {"price": 50}, rule_id=1),
        _rule(RuleType.RSI_BELOW.value, {"value": 30}, rule_id=2),
        _rule(RuleType.PRICE_BELOW.value, {"price": 10}, rule_id=3),  # won't fire
    ]
    fired = ENGINE.evaluate("US:TEST", _quote(last=100.0), {"rsi14": 20.0},
                            None, rules)
    fired_ids = sorted(a.rule_id for a in fired)
    assert fired_ids == [1, 2]
