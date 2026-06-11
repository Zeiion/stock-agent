"""Pure unit tests for technical indicators (no I/O)."""
from __future__ import annotations

import math

import numpy as np
import pandas as pd

from app import indicators


def _synthetic_df(n: int = 120, seed: int = 7) -> pd.DataFrame:
    """A deterministic OHLCV frame with a chronological DatetimeIndex."""
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2024-01-01", periods=n, freq="D")
    # gentle upward drift + bounded noise so values stay positive & realistic
    steps = rng.normal(0.3, 1.0, size=n)
    close = 100.0 + np.cumsum(steps)
    close = np.clip(close, 5.0, None)
    open_ = close - rng.normal(0.0, 0.5, size=n)
    high = np.maximum(open_, close) + np.abs(rng.normal(0.0, 0.8, size=n))
    low = np.minimum(open_, close) - np.abs(rng.normal(0.0, 0.8, size=n))
    volume = rng.integers(1_000, 10_000, size=n).astype(float)
    return pd.DataFrame(
        {"open": open_, "high": high, "low": low, "close": close, "volume": volume},
        index=idx,
    )


def test_compute_indicators_returns_expected_keys():
    snap = indicators.compute_indicators(_synthetic_df())
    expected = {
        "close", "ma5", "ma10", "ma20", "ma60", "ma5_prev", "ma20_prev",
        "rsi14", "rsi14_prev", "macd", "macd_signal", "macd_hist",
        "macd_prev", "macd_signal_prev", "boll_upper", "boll_mid", "boll_lower",
        "k", "d", "j", "k_prev", "j_prev", "atr14", "vol", "vol_ma20",
        "bars", "tags",
    }
    assert expected.issubset(snap.keys())
    assert snap["bars"] == 120
    assert isinstance(snap["tags"], list)


def test_empty_df_returns_empty_dict():
    assert indicators.compute_indicators(pd.DataFrame()) == {}
    assert indicators.compute_indicators(None) == {}
    # a frame without a close column is also treated as empty
    assert indicators.compute_indicators(pd.DataFrame({"open": [1.0]})) == {}


def test_rsi_in_range():
    df = _synthetic_df()
    r = indicators.rsi(df["close"], 14)
    assert ((r >= 0) & (r <= 100)).all()
    snap = indicators.compute_indicators(df)
    assert 0.0 <= snap["rsi14"] <= 100.0


def test_rsi_mostly_up_is_high_mostly_down_is_low():
    # +10 each step with one small -1 pullback so avg_loss is non-zero (a purely
    # monotonic series makes RS undefined -> the engine returns the 50 fallback).
    up = pd.Series(np.cumsum([10.0] * 40 + [-1.0] + [10.0] * 20) + 100.0)
    down = pd.Series(np.cumsum([-10.0] * 40 + [1.0] + [-10.0] * 20) + 1000.0)
    assert indicators.rsi(up, 14).iloc[-1] > 80.0
    assert indicators.rsi(down, 14).iloc[-1] < 20.0


def test_kdj_j_equals_3k_minus_2d():
    df = _synthetic_df()
    k, d, j = indicators.kdj(df["high"], df["low"], df["close"])
    # J == 3K - 2D for every bar (within float tolerance)
    assert np.allclose(j.to_numpy(), (3 * k - 2 * d).to_numpy())
    snap = indicators.compute_indicators(df)
    assert math.isclose(snap["j"], 3 * snap["k"] - 2 * snap["d"], rel_tol=1e-3,
                        abs_tol=1e-2)


def test_macd_hist_is_line_minus_signal():
    close = _synthetic_df()["close"]
    line, signal, hist = indicators.macd(close)
    assert np.allclose(hist.to_numpy(), (line - signal).to_numpy())


def test_sma_ema_basic():
    s = pd.Series([1.0, 2.0, 3.0, 4.0, 5.0])
    # min_periods=1 -> the last 3-bar SMA is mean(3,4,5) = 4
    assert indicators.sma(s, 3).iloc[-1] == 4.0
    # EMA is bounded by the series range and ends near the latest value
    e = indicators.ema(s, 3)
    assert s.min() <= e.iloc[-1] <= s.max()


def test_bollinger_upper_above_lower():
    df = _synthetic_df()
    upper, mid, lower = indicators.bollinger(df["close"])
    # ignore the first bar (std with min_periods=1 is 0 there)
    assert (upper.iloc[1:] >= mid.iloc[1:]).all()
    assert (mid.iloc[1:] >= lower.iloc[1:]).all()


def test_short_history_does_not_crash():
    # 3 bars: still returns a dict; prev keys may be None but no exception.
    idx = pd.date_range("2024-01-01", periods=3, freq="D")
    df = pd.DataFrame(
        {"open": [10, 11, 12], "high": [11, 12, 13], "low": [9, 10, 11],
         "close": [10.5, 11.5, 12.5], "volume": [100, 200, 300]}, index=idx)
    snap = indicators.compute_indicators(df)
    assert snap["bars"] == 3
    assert snap["close"] == 12.5
