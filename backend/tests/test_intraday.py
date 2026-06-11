"""Unit tests for the 做T intraday high/low predictor (pure pandas, no I/O)."""
from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest

from app import intraday


def _df(closes, hi_mult=1.02, lo_mult=0.98, start="2024-01-01"):
    closes = np.asarray(closes, dtype=float)
    n = len(closes)
    idx = pd.date_range(start, periods=n, freq="D")
    return pd.DataFrame(
        {"open": closes * 0.999, "high": closes * hi_mult,
         "low": closes * lo_mult, "close": closes,
         "volume": np.full(n, 1_000_000.0)},
        index=idx,
    )


# --------------------------------------------------------------------------- #
# Pivot points — exact arithmetic
# --------------------------------------------------------------------------- #
def test_pivot_points_exact():
    p = intraday.pivot_points(110, 90, 100)
    c = p["classic"]
    assert c["p"] == 100.0
    assert c["r1"] == 110.0 and c["s1"] == 90.0
    assert c["r2"] == 120.0 and c["s2"] == 80.0
    assert c["r3"] == 130.0 and c["s3"] == 70.0
    cam = p["camarilla"]
    # H3 = C + R*1.1/4 = 100 + 20*0.275 = 105.5 ; H4 = C + R*1.1/2 = 111
    assert cam["h3"] == 105.5 and cam["l3"] == 94.5
    assert cam["h4"] == 111.0 and cam["l4"] == 89.0
    fib = p["fibonacci"]
    assert fib["r1"] == pytest.approx(107.64, abs=0.01)
    assert fib["r3"] == 120.0 and fib["s3"] == 80.0


# --------------------------------------------------------------------------- #
# Volatility estimators
# --------------------------------------------------------------------------- #
def test_parkinson_closed_form():
    # constant H/L ratio => Parkinson sigma = |ln(H/L)| / (2*sqrt(ln2))
    df = _df(np.linspace(100, 100, 30), hi_mult=1.02, lo_mult=0.98)
    expected = math.log(1.02 / 0.98) / (2 * math.sqrt(math.log(2)))
    assert intraday.parkinson(df) == pytest.approx(expected, rel=1e-6)


def test_vol_estimators_positive_and_finite():
    rng = np.random.RandomState(3)
    closes = np.abs(np.cumsum(rng.randn(120))) + 50
    df = _df(closes)
    for fn in (intraday.parkinson, intraday.garman_klass,
               intraday.rogers_satchell, intraday.yang_zhang, intraday.ewma_vol):
        v = fn(df)
        assert v is not None and np.isfinite(v) and v > 0


def test_vol_estimators_degrade_on_tiny_frame():
    df = _df([100.0, 101.0])
    assert intraday.yang_zhang(df) is None
    # garch needs the optional lib + >=100 obs; must never raise, just None here
    assert intraday.garch_vol(df) is None


def test_empirical_extremes_recovers_constant_ratios():
    # high = prevclose*? ; with flat closes, (high-prevclose)/prevclose is constant
    df = _df(np.full(40, 100.0), hi_mult=1.03, lo_mult=0.96)
    res = intraday.empirical_extremes(df)
    assert res is not None
    hi_r, lo_r = res
    # high=103 vs prevclose=99.9? closes flat at 100 so prevclose=100 -> hi 3%, lo -4%
    assert hi_r == pytest.approx(0.03, abs=1e-6)
    assert lo_r == pytest.approx(-0.04, abs=1e-6)


# --------------------------------------------------------------------------- #
# predict_levels ensemble
# --------------------------------------------------------------------------- #
def test_predict_levels_shape_and_ordering():
    df = _df(np.abs(np.cumsum(np.random.RandomState(1).randn(120))) + 50)
    lv = intraday.predict_levels("US:AAPL", df, today_open=float(df["close"].iloc[-1]))
    assert lv["predicted_high"] > lv["predicted_low"] > 0
    assert {m["name"] for m in lv["methods"]} <= {
        "camarilla", "atr", "empirical", "volatility"}
    assert len(lv["methods"]) >= 3
    assert 0 <= lv["confidence"] <= 95
    assert 1 <= lv["conviction"] <= 5
    assert lv["anchor_kind"] == "today_open"
    assert lv["expected_range_pct"] > 0


def test_predict_levels_empty_and_short():
    empty = intraday.predict_levels("US:AAPL", pd.DataFrame())
    assert empty["predicted_high"] is None and empty["confidence"] == 0
    short = intraday.predict_levels("US:AAPL", _df([100.0, 101.0]))
    assert short["predicted_high"] is None


def test_cn_price_limit_clamp():
    # craft a last bar with a huge range so raw predictions blow past ±10%
    closes = np.concatenate([np.full(60, 100.0), [100.0]])
    df = _df(closes, hi_mult=1.30, lo_mult=0.70)   # ±30% bars
    lv = intraday.predict_levels("CN:600519", df, today_open=100.0)
    assert lv["limits"]["applied"] is True
    assert lv["limits"]["limit_pct"] == 0.10
    # clamped within ±10% of prev_close (100)
    assert lv["predicted_high"] <= 110.0 + 1e-9
    assert lv["predicted_low"] >= 90.0 - 1e-9


def test_us_has_no_price_limit_clamp():
    df = _df(np.full(60, 100.0), hi_mult=1.30, lo_mult=0.70)
    lv = intraday.predict_levels("US:AAPL", df, today_open=100.0)
    assert lv["limits"]["applied"] is False


# --------------------------------------------------------------------------- #
# day_t_plan
# --------------------------------------------------------------------------- #
def test_day_t_plan_holder_viable_with_lot_rounding():
    df = _df(np.abs(np.cumsum(np.random.RandomState(5).randn(120))) + 50,
             hi_mult=1.03, lo_mult=0.97)
    last = float(df["close"].iloc[-1])
    plan = intraday.day_t_plan("CN:600519", df,
                               position={"qty": 1000, "avg_price": last},
                               today_open=last)
    p = plan["plan"]
    # 1000 * 0.33 = 330 -> rounded down to a 100-share lot -> 300
    assert p["suggested_qty"] == 300
    assert p["suggested_qty"] % 100 == 0
    assert p["buy_limit"] < p["sell_limit"]
    assert any("高抛" in a for a in p["actions"])
    assert any("低吸" in a for a in p["actions"])
    # CN T+1 caveat must be present
    assert any("T+1" in c for c in p["caveats"])
    # invalidation levels must sit at/beyond the trading-band edges (not inside)
    assert p["breakout_above"] >= plan["predicted_high"]
    assert p["stop_below"] <= plan["predicted_low"]


def test_day_t_plan_skips_when_range_too_narrow():
    # ±0.1% bars -> spread well under the viability threshold
    df = _df(np.full(80, 100.0), hi_mult=1.001, lo_mult=0.999)
    plan = intraday.day_t_plan("US:AAPL", df,
                               position={"qty": 500}, today_open=100.0)
    assert plan["plan"]["viable"] is False
    assert any("不做T" in a or "性价比" in a for a in plan["plan"]["actions"])


def test_day_t_plan_no_position_gives_range_guidance():
    df = _df(np.abs(np.cumsum(np.random.RandomState(9).randn(100))) + 40,
             hi_mult=1.03, lo_mult=0.97)
    plan = intraday.day_t_plan("CN:600519", df, position=None, today_open=None)
    p = plan["plan"]
    assert any("低吸" in a for a in p["actions"])
    # no底仓 + A股 -> must warn that做T needs an existing position (T+1)
    assert any("无底仓" in c or "T+1" in c for c in p["caveats"])
    # with no today_open it should anchor on prev close and say so
    assert plan["anchor_kind"] in ("prev_close", "last")
