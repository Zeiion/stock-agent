"""Unit tests for the vectorized backtester (pure pandas, no I/O)."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from app import backtest


_STATS_KEYS = {
    "total_return", "buy_hold_return", "max_drawdown",
    "num_trades", "win_rate", "sharpe", "sortino", "calmar",
}
_RESULT_KEYS = {"symbol", "strategy", "params", "market", "stats",
                "equity_curve", "trades"}


def _df(closes, start="2024-01-01"):
    n = len(closes)
    idx = pd.date_range(start, periods=n, freq="D")
    closes = np.asarray(closes, dtype=float)
    return pd.DataFrame(
        {"open": closes, "high": closes * 1.001, "low": closes * 0.999,
         "close": closes, "volume": np.full(n, 1000.0)},
        index=idx,
    )


def test_result_shape_and_stats_keys():
    df = _df(np.linspace(100, 200, 80))
    res = backtest.run_backtest("US:AAPL", df, "ma_cross", {})
    assert _RESULT_KEYS.issubset(res.keys())
    assert _STATS_KEYS == set(res["stats"].keys())
    assert res["symbol"] == "US:AAPL"
    assert res["market"] == "US"
    assert res["strategy"] == "ma_cross"
    assert len(res["equity_curve"]) == len(df)


def test_no_spurious_warmup_trade_on_monotonic_series():
    # A strictly increasing series: fast MA stays above slow MA after warm-up,
    # so there is exactly ONE golden-cross buy and no death cross, hence no
    # completed round-trip trade and no spurious warm-up artifact.
    df = _df(np.linspace(100, 300, 120))
    res = backtest.run_backtest("US:AAPL", df, "ma_cross", {"fast": 5, "slow": 20})
    # No closed trades on a one-way ramp (buy then hold to the end).
    assert res["stats"]["num_trades"] == 0
    assert res["trades"] == []
    # buy-and-hold return is positive on the ramp
    assert res["stats"]["buy_hold_return"] > 0


def test_ma_cross_round_trip_produces_a_trade():
    # flat -> dip -> rise -> fall forces a genuine golden cross AFTER the slow-MA
    # warm-up (on the way up) and a death cross (on the way down) => one trade.
    flat = np.full(25, 100.0)
    dip = np.linspace(100, 90, 15)
    rise = np.linspace(90, 160, 40)
    fall = np.linspace(160, 100, 40)
    df = _df(np.concatenate([flat, dip, rise, fall]))
    res = backtest.run_backtest("US:AAPL", df, "ma_cross", {"fast": 5, "slow": 20})
    assert res["stats"]["num_trades"] >= 1
    for t in res["trades"]:
        assert {"entry_ts", "entry_price", "exit_ts", "exit_price",
                "return_pct"}.issubset(t.keys())
        assert t["exit_ts"] > t["entry_ts"]   # exit strictly after entry


def test_short_or_empty_frame_returns_empty_result():
    empty = backtest.run_backtest("US:AAPL", pd.DataFrame(), "ma_cross", {})
    assert empty["stats"]["num_trades"] == 0
    assert empty["equity_curve"] == []
    one_bar = backtest.run_backtest("US:AAPL", _df([100.0]), "ma_cross", {})
    assert one_bar["stats"]["num_trades"] == 0


def test_cn_symbol_runs_with_t1_and_limit_logic():
    # Construct a path that buys then immediately wants to sell to exercise the
    # T+1 same-bar defer branch, plus a limit-down bar, all without crashing.
    up = np.linspace(100, 130, 40)
    spike_down = np.array([130.0 * 0.90])     # ~ -10% would be limit-down territory
    recover = np.linspace(120, 150, 40)
    df = _df(np.concatenate([up, spike_down, recover]))
    res = backtest.run_backtest("CN:600519", df, "ma_cross", {"fast": 5, "slow": 20})
    assert res["market"] == "CN"
    assert _STATS_KEYS == set(res["stats"].keys())
    # any executed trades must respect T+1 (exit strictly after entry)
    for t in res["trades"]:
        assert t["exit_ts"] >= t["entry_ts"]


def test_unknown_strategy_with_bad_params_falls_back():
    # An rsi_reversion call missing keys should fall back to defaults, not crash.
    df = _df(np.linspace(100, 120, 60))
    res = backtest.run_backtest("US:AAPL", df, "rsi_reversion", {})
    assert _STATS_KEYS == set(res["stats"].keys())
    # defaults got resolved into params
    assert res["params"].get("n") == 14


def test_macd_strategy_runs():
    df = _df(np.concatenate([np.linspace(100, 160, 60), np.linspace(160, 100, 60)]))
    res = backtest.run_backtest("US:AAPL", df, "macd", {})
    assert _STATS_KEYS == set(res["stats"].keys())
    assert len(res["equity_curve"]) == len(df)


def test_sortino_and_calmar_present_and_finite():
    df = _df(np.concatenate([np.full(25, 100.0), np.linspace(100, 90, 15),
                             np.linspace(90, 160, 40), np.linspace(160, 110, 40)]))
    res = backtest.run_backtest("US:AAPL", df, "ma_cross", {"fast": 5, "slow": 20})
    s = res["stats"]
    assert "sortino" in s and "calmar" in s
    assert np.isfinite(s["sortino"]) and np.isfinite(s["calmar"])


# --------------------------------------------------------------------------- #
# optimize() objective selection
# --------------------------------------------------------------------------- #
def test_optimize_ranks_by_selected_metric():
    df = _df(np.concatenate([np.full(20, 100.0), np.linspace(100, 80, 20),
                             np.linspace(80, 170, 50), np.linspace(170, 120, 40)]))
    res = backtest.optimize("US:AAPL", df, "ma_cross",
                            {"fast": [5, 10], "slow": [20, 30]}, metric="sharpe")
    assert res["metric"] == "sharpe"
    assert res["tested"] >= 1
    # results are sorted descending by the chosen metric
    vals = [r["sharpe"] for r in res["results"]]
    assert vals == sorted(vals, reverse=True)
    # every result row now exposes the richer metric set
    assert {"sharpe", "sortino", "calmar"}.issubset(res["best"].keys())


def test_optimize_unknown_metric_falls_back_to_total_return():
    df = _df(np.linspace(100, 200, 80))
    res = backtest.optimize("US:AAPL", df, "ma_cross",
                            {"fast": [5], "slow": [20]}, metric="bogus")
    assert res["metric"] == "total_return"


# --------------------------------------------------------------------------- #
# walk-forward out-of-sample validation
# --------------------------------------------------------------------------- #
def test_walk_forward_shape_and_oos_folds():
    # a long, varied series so train windows are meaningful and folds are valid
    closes = np.concatenate([
        np.linspace(100, 140, 120), np.linspace(140, 95, 90),
        np.linspace(95, 180, 140), np.linspace(180, 130, 100),
    ])
    df = _df(closes)
    res = backtest.walk_forward("US:AAPL", df, "ma_cross",
                                {"fast": [5, 10], "slow": [20, 30]},
                                folds=3, metric="sharpe")
    assert res["folds"] >= 1
    assert res["metric"] == "sharpe"
    assert len(res["fold_results"]) == res["folds"]
    for f in res["fold_results"]:
        # OOS test window must come entirely after the training window
        assert f["train_bars"] > 0 and f["test_bars"] >= 2
        assert {"is_metric", "oos_metric", "params"}.issubset(f.keys())
    assert {"mean_is", "mean_oos", "overfit_gap"}.issubset(res["is_vs_oos"].keys())
    # overfit_gap is exactly mean_is - mean_oos
    assert res["is_vs_oos"]["overfit_gap"] == pytest.approx(
        res["is_vs_oos"]["mean_is"] - res["is_vs_oos"]["mean_oos"], abs=1e-3)


def test_walk_forward_degrades_on_short_series():
    res = backtest.walk_forward("US:AAPL", _df(np.linspace(100, 110, 10)), "ma_cross")
    assert res["folds"] == 0
    assert res["fold_results"] == []
    assert backtest.walk_forward("US:AAPL", pd.DataFrame(), "ma_cross")["folds"] == 0
