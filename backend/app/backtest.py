"""Synchronous vectorized-signal backtester (called inside a threadpool).

Long-only, all-in / all-out toy backtest over a single symbol's daily OHLCV
frame. Reuses the pure-pandas indicators from `indicators.py` and applies
per-market cost / realism rules driven by `symbols.parse`:

  US: commission both sides only.
  HK: commission both sides only.
  CN: commission both sides + 0.05% stamp duty on SELL, T+1 (no same-bar sell
      after a buy), and a price-limit lock (skip fills that would require
      trading through the +-9.8% daily limit).

Fills execute at the signal bar's close. Entry point used by the
`/api/backtest` route:

    run_backtest(symbol, df, strategy="ma_cross", params=None) -> dict
"""
from __future__ import annotations

import itertools
from typing import Any, Optional

import numpy as np
import pandas as pd

from . import indicators, symbols

START_CASH = 100_000.0
COMMISSION = 0.00025          # 0.025% per side, all markets
CN_STAMP_DUTY = 0.0005        # 0.05% on SELL only (CN)
CN_LIMIT = 0.098              # approx +-9.8% daily price limit lock
TRADING_DAYS = 252

_DEFAULT_PARAMS: dict[str, dict[str, Any]] = {
    "ma_cross": {"fast": 5, "slow": 20},
    "rsi_reversion": {"n": 14, "lower": 30, "upper": 70},
    "macd": {},
    "boll_breakout": {"n": 20, "k": 2.0},
    "kdj_cross": {"n": 9},
}

# Strategies the API/UI can enumerate.
STRATEGIES = ["ma_cross", "rsi_reversion", "macd", "boll_breakout", "kdj_cross"]

# Per-strategy parameter search grids used by optimize() when no grid is given.
DEFAULT_GRID: dict[str, dict[str, list[Any]]] = {
    "ma_cross": {"fast": [5, 10, 20], "slow": [20, 30, 60]},
    "rsi_reversion": {"n": [14], "lower": [20, 30], "upper": [70, 80]},
    "macd": {},
    "boll_breakout": {"n": [20], "k": [2.0, 2.5]},
    "kdj_cross": {"n": [9, 14]},
}


def _empty_result(symbol: str, strategy: str, params: dict, market: str) -> dict:
    return {
        "symbol": symbol,
        "strategy": strategy,
        "params": params,
        "market": market,
        "stats": {
            "total_return": 0.0,
            "buy_hold_return": 0.0,
            "max_drawdown": 0.0,
            "num_trades": 0,
            "win_rate": 0.0,
            "sharpe": 0.0,
            "sortino": 0.0,
            "calmar": 0.0,
        },
        "equity_curve": [],
        "trades": [],
    }


def _signals(strategy: str, df: pd.DataFrame, params: dict) -> pd.Series:
    """Return a Series aligned to df.index with values in {1 (buy), -1 (sell), 0}.

    Signals fire on a *cross* (this bar vs previous bar).
    """
    close = df["close"]
    sig = pd.Series(0, index=df.index, dtype=int)

    if strategy == "ma_cross":
        fast = int(params["fast"])
        slow = int(params["slow"])
        f = indicators.sma(close, fast)
        s = indicators.sma(close, slow)
        diff = f - s
        # indicators.sma uses min_periods=1, so the slow MA is "defined" long before
        # `slow` real bars exist and produces a spurious cross during warm-up. Mask
        # the warm-up region so the first detectable cross needs a fully-formed slow MA.
        diff.iloc[: max(fast, slow) - 1] = float("nan")
        prev = diff.shift(1)
        sig[(prev <= 0) & (diff > 0)] = 1
        sig[(prev >= 0) & (diff < 0)] = -1

    elif strategy == "rsi_reversion":
        n = int(params["n"])
        lower = float(params["lower"])
        upper = float(params["upper"])
        r = indicators.rsi(close, n)
        prev = r.shift(1)
        # cross UP through lower -> buy ; cross DOWN through upper -> sell
        sig[(prev <= lower) & (r > lower)] = 1
        sig[(prev >= upper) & (r < upper)] = -1

    elif strategy == "macd":
        macd_line, signal_line, _ = indicators.macd(close)
        diff = macd_line - signal_line
        # MACD EMAs (adjust=False) are defined from bar 0; mask a conservative
        # warm-up of slow+signal bars to drop the early artifact cross.
        diff.iloc[: 26 + 9 - 1] = float("nan")
        prev = diff.shift(1)
        sig[(prev <= 0) & (diff > 0)] = 1
        sig[(prev >= 0) & (diff < 0)] = -1

    elif strategy == "boll_breakout":
        n = int(params["n"])
        k = float(params["k"])
        upper, mid, _ = indicators.bollinger(close, n, k)
        # bollinger uses min_periods=1, so the bands are "defined" during warm-up
        # and would produce spurious early breakouts; mask the first n-1 bars.
        upper = upper.copy()
        mid = mid.copy()
        upper.iloc[: n - 1] = float("nan")
        mid.iloc[: n - 1] = float("nan")
        prev_close = close.shift(1)
        prev_upper = upper.shift(1)
        prev_mid = mid.shift(1)
        # cross ABOVE the upper band -> buy ; cross BELOW the middle band -> sell
        sig[(prev_close <= prev_upper) & (close > upper)] = 1
        sig[(prev_close >= prev_mid) & (close < mid)] = -1

    elif strategy == "kdj_cross":
        n = int(params["n"])
        high = df["high"]
        low = df["low"]
        _, _, j = indicators.kdj(high, low, close, n)
        prev = j.shift(1)
        # J cross UP through 20 -> buy ; J cross DOWN through 80 -> sell
        sig[(prev <= 20) & (j > 20)] = 1
        sig[(prev >= 80) & (j < 80)] = -1

    else:
        raise ValueError(f"unknown strategy: {strategy!r}")

    return sig


def run_backtest(symbol: str, df: pd.DataFrame, strategy: str = "ma_cross",
                 params: Optional[dict] = None) -> dict:
    market, _ = symbols.parse(symbol)

    # resolve params: defaults overlaid with caller-supplied values
    base = dict(_DEFAULT_PARAMS.get(strategy, {}))
    if params:
        base.update(params)
    resolved = base

    if df is None or df.empty or "close" not in df or len(df) < 2:
        return _empty_result(symbol, strategy, resolved, market)

    df = df.sort_index()
    close = df["close"].astype(float)
    prev_close = close.shift(1)
    # per-bar pct change vs previous close (for CN price-limit lock)
    change = (close - prev_close) / prev_close.replace(0, np.nan)

    try:
        signals = _signals(strategy, df, resolved)
    except KeyError:
        # missing required param key -> fall back to defaults entirely
        resolved = dict(_DEFAULT_PARAMS.get(strategy, {}))
        signals = _signals(strategy, df, resolved)

    is_cn = market == "CN"
    sell_cost_rate = COMMISSION + (CN_STAMP_DUTY if is_cn else 0.0)
    buy_cost_rate = COMMISSION

    n = len(df)
    idx = df.index
    closes = close.to_numpy()
    chg = change.to_numpy()
    sig = signals.to_numpy()

    cash = START_CASH
    shares = 0.0
    in_pos = False
    buy_bar = -1          # bar index of the last fill that opened the position

    equity = np.empty(n, dtype=float)
    trades: list[dict[str, Any]] = []
    entry_ts: Optional[float] = None
    entry_price: float = 0.0

    pending_sell = False  # carries a same-bar (T+1) deferred sell to next bar

    for i in range(n):
        price = float(closes[i])
        c = chg[i]
        ts = _epoch(idx[i])

        # ---- resolve a deferred (T+1) sell first ----
        if pending_sell and in_pos:
            blocked = is_cn and not np.isnan(c) and c <= -CN_LIMIT
            if not blocked:
                cash += shares * price * (1.0 - sell_cost_rate)
                ret = (price - entry_price) / entry_price * 100.0 if entry_price else 0.0
                trades.append({
                    "entry_ts": entry_ts,
                    "entry_price": round(entry_price, 4),
                    "exit_ts": ts,
                    "exit_price": round(price, 4),
                    "return_pct": round(ret, 2),
                })
                shares = 0.0
                in_pos = False
                pending_sell = False
                buy_bar = -1
            # if blocked (limit-down), keep pending_sell True for a later bar

        s = int(sig[i])

        if s == 1 and not in_pos:
            # price-limit lock: cannot fill a buy on a limit-up bar (CN)
            blocked = is_cn and not np.isnan(c) and c >= CN_LIMIT
            if not blocked and price > 0:
                shares = (cash * (1.0 - buy_cost_rate)) / price
                cash = 0.0
                in_pos = True
                buy_bar = i
                entry_ts = ts
                entry_price = price

        elif s == -1 and in_pos:
            # T+1: cannot sell on the same bar we bought (CN) -> defer one bar
            if is_cn and i == buy_bar:
                pending_sell = True
            else:
                blocked = is_cn and not np.isnan(c) and c <= -CN_LIMIT
                if blocked:
                    pending_sell = True  # retry on a later non-limit bar
                else:
                    cash += shares * price * (1.0 - sell_cost_rate)
                    ret = (price - entry_price) / entry_price * 100.0 if entry_price else 0.0
                    trades.append({
                        "entry_ts": entry_ts,
                        "entry_price": round(entry_price, 4),
                        "exit_ts": ts,
                        "exit_price": round(price, 4),
                        "return_pct": round(ret, 2),
                    })
                    shares = 0.0
                    in_pos = False
                    buy_bar = -1

        equity[i] = cash + shares * price

    equity_series = pd.Series(equity, index=idx)
    stats = _compute_stats(equity_series, closes, trades)

    equity_curve = [
        {"ts": _epoch(idx[i]), "equity": round(float(equity[i]), 2)}
        for i in range(n)
    ]

    return {
        "symbol": symbol,
        "strategy": strategy,
        "params": resolved,
        "market": market,
        "stats": stats,
        "equity_curve": equity_curve,
        "trades": trades,
    }


def _compute_stats(equity: pd.Series, closes: np.ndarray,
                   trades: list[dict[str, Any]]) -> dict[str, Any]:
    eq = equity.to_numpy()
    total_return = (eq[-1] / eq[0] - 1.0) * 100.0 if eq[0] else 0.0

    first_close = float(closes[0])
    last_close = float(closes[-1])
    buy_hold = (last_close / first_close - 1.0) * 100.0 if first_close else 0.0

    # max drawdown off the equity curve
    running_max = np.maximum.accumulate(eq)
    with np.errstate(divide="ignore", invalid="ignore"):
        dd = np.where(running_max > 0, (eq - running_max) / running_max, 0.0)
    max_dd = float(dd.min()) * 100.0 if len(dd) else 0.0

    # sharpe / sortino from daily equity returns, annualized, guarded against
    # zero std. Sortino only penalizes downside volatility (returns < 0) — the
    # empyrical/quantstats refinement that doesn't punish upside swings.
    rets = pd.Series(eq).pct_change().dropna().to_numpy()
    if rets.size > 1:
        mean_r = float(rets.mean())
        std = float(rets.std(ddof=0))
        sharpe = (mean_r / std * np.sqrt(TRADING_DAYS)) if std > 0 else 0.0
        downside = rets[rets < 0]
        dstd = float(downside.std(ddof=0)) if downside.size > 0 else 0.0
        sortino = (mean_r / dstd * np.sqrt(TRADING_DAYS)) if dstd > 0 else 0.0
    else:
        sharpe = sortino = 0.0

    # Calmar = annualized return / abs(max drawdown). Rewards return per unit of
    # worst peak-to-trough pain — the standard tail-risk-aware ratio.
    n_bars = len(eq)
    years = n_bars / TRADING_DAYS if n_bars else 0.0
    growth = (eq[-1] / eq[0]) if eq[0] else 0.0
    if years > 0 and growth > 0:
        ann_return_pct = (growth ** (1.0 / years) - 1.0) * 100.0
    else:
        ann_return_pct = total_return
    calmar = (ann_return_pct / abs(max_dd)) if max_dd != 0 else 0.0

    num_trades = len(trades)
    wins = sum(1 for t in trades if t["return_pct"] > 0)
    win_rate = (wins / num_trades * 100.0) if num_trades else 0.0

    return {
        "total_return": round(total_return, 2),
        "buy_hold_return": round(buy_hold, 2),
        "max_drawdown": round(max_dd, 2),
        "num_trades": num_trades,
        "win_rate": round(win_rate, 2),
        "sharpe": round(sharpe, 2),
        "sortino": round(sortino, 2),
        "calmar": round(calmar, 2),
    }


def _epoch(ts_val: Any) -> float:
    """Best-effort conversion of an index value to epoch seconds (float)."""
    try:
        return float(pd.Timestamp(ts_val).timestamp())
    except Exception:
        try:
            return float(ts_val)
        except Exception:
            return 0.0


# Objective functions the optimizer / walk-forward can rank by. All are
# higher-is-better (freqtrade's hyperopt-loss idea: optimize toward a chosen
# objective, not just realized return). This fixes the "total_return only"
# limitation that quietly favors high-variance, lucky parameter sets.
OPTIMIZE_METRICS = ("total_return", "sharpe", "sortino", "calmar", "win_rate")


def _metric_value(stats: dict, metric: str) -> float:
    return float(stats.get(metric, 0.0) or 0.0)


def optimize(symbol: str, df: pd.DataFrame, strategy: str,
             grid: Optional[dict] = None, metric: str = "total_return") -> dict:
    """Grid-search a strategy's parameters over `df` and rank by `metric`.

    `grid` maps param-name -> list of candidate values, e.g.
    {"fast": [5, 10], "slow": [20, 30, 60]}. When None, a sensible per-strategy
    DEFAULT_GRID is used. `metric` selects the objective to maximize, one of
    OPTIMIZE_METRICS (default "total_return"; unknown values fall back to it).
    ma_cross combos with fast >= slow are skipped. Each combo is evaluated via
    run_backtest and summarized; the full list is sorted by the chosen metric
    (desc) and capped at 50. Returns:

        {"strategy", "metric", "tested", "best", "results"}

    Degrades gracefully on an empty / too-short frame (tested 0, best None).
    """
    if metric not in OPTIMIZE_METRICS:
        metric = "total_return"
    if grid is None:
        grid = DEFAULT_GRID.get(strategy, {})

    if df is None or df.empty or "close" not in df or len(df) < 2:
        return {"strategy": strategy, "metric": metric, "tested": 0,
                "best": None, "results": []}

    keys = list(grid.keys())
    value_lists = [grid[k] for k in keys]

    # itertools.product over an empty grid yields a single empty combo -> one run.
    combos = (dict(zip(keys, vals)) for vals in itertools.product(*value_lists)) \
        if keys else iter([{}])

    results: list[dict[str, Any]] = []
    for combo in combos:
        if strategy == "ma_cross":
            fast = combo.get("fast")
            slow = combo.get("slow")
            if fast is not None and slow is not None and int(fast) >= int(slow):
                continue
        try:
            res = run_backtest(symbol, df, strategy, combo)
        except Exception:
            # never let one bad combo abort the whole sweep
            continue
        stats = res.get("stats", {})
        results.append({
            "params": res.get("params", combo),
            "total_return": stats.get("total_return", 0.0),
            "max_drawdown": stats.get("max_drawdown", 0.0),
            "sharpe": stats.get("sharpe", 0.0),
            "sortino": stats.get("sortino", 0.0),
            "calmar": stats.get("calmar", 0.0),
            "num_trades": stats.get("num_trades", 0),
            "win_rate": stats.get("win_rate", 0.0),
        })

    results.sort(key=lambda r: _metric_value(r, metric), reverse=True)
    best = results[0] if results else None
    return {
        "strategy": strategy,
        "metric": metric,
        "tested": len(results),
        "best": best,
        "results": results[:50],
    }


def walk_forward(symbol: str, df: pd.DataFrame, strategy: str,
                 grid: Optional[dict] = None, folds: int = 4,
                 train_ratio: float = 0.5, metric: str = "sharpe") -> dict:
    """Anchored walk-forward (out-of-sample) validation.

    The headline weakness of `optimize()` is in-sample overfitting: it picks the
    parameters that looked best on the *same* data it's scored on. Walk-forward
    fixes that (qlib's rolling retrain / freqtrade's time-range split): reserve
    the first `train_ratio` of the series as an initial training window, split
    the remainder into `folds` contiguous test windows, and for each fold:

        1. optimize params on everything BEFORE the fold (expanding/anchored
           train window), ranking by `metric`;
        2. run the chosen params on the held-out fold (never seen in training);
        3. record the in-sample (IS) metric vs the out-of-sample (OOS) metric.

    The aggregate `overfit_gap` (mean IS metric − mean OOS metric) and stitched
    OOS equity tell you whether the backtest edge survives unseen data. Returns:

        {"strategy", "metric", "folds", "oos", "is_vs_oos", "fold_results"}

    Degrades gracefully (folds 0) when there isn't enough history.
    """
    if metric not in OPTIMIZE_METRICS:
        metric = "sharpe"
    folds = max(1, int(folds))
    market, _ = symbols.parse(symbol)
    empty = {"strategy": strategy, "metric": metric, "folds": 0,
             "oos": {}, "is_vs_oos": {}, "fold_results": []}

    if df is None or df.empty or "close" not in df or len(df) < 2:
        return empty

    df = df.sort_index()
    n = len(df)
    test_start = int(n * train_ratio)
    # need a non-trivial train window AND at least ~2 test bars per fold
    if test_start < 20 or (n - test_start) < folds * 2:
        return empty

    # contiguous test windows over [test_start, n)
    bounds = np.linspace(test_start, n, folds + 1).astype(int)

    fold_results: list[dict[str, Any]] = []
    is_vals: list[float] = []
    oos_vals: list[float] = []
    oos_returns: list[float] = []
    oos_bh: list[float] = []
    growth = 1.0          # stitched OOS equity multiplier across folds
    total_oos_trades = 0

    for k in range(folds):
        lo, hi = int(bounds[k]), int(bounds[k + 1])
        if hi - lo < 2:
            continue
        train_df = df.iloc[:lo]
        test_df = df.iloc[lo:hi]
        if len(train_df) < 20:
            continue

        opt = optimize(symbol, train_df, strategy, grid, metric=metric)
        best = opt.get("best")
        if best is None:
            continue
        params = best.get("params", {})
        is_metric = _metric_value(best, metric)

        oos = run_backtest(symbol, test_df, strategy, params)
        oos_stats = oos.get("stats", {})
        oos_metric = _metric_value(oos_stats, metric)

        is_vals.append(is_metric)
        oos_vals.append(oos_metric)
        oos_returns.append(oos_stats.get("total_return", 0.0))
        oos_bh.append(oos_stats.get("buy_hold_return", 0.0))
        total_oos_trades += int(oos_stats.get("num_trades", 0))
        growth *= (1.0 + oos_stats.get("total_return", 0.0) / 100.0)

        fold_results.append({
            "fold": k + 1,
            "train_bars": len(train_df),
            "test_bars": len(test_df),
            "params": params,
            "is_metric": round(is_metric, 4),
            "oos_metric": round(oos_metric, 4),
            "oos_total_return": oos_stats.get("total_return", 0.0),
            "oos_buy_hold_return": oos_stats.get("buy_hold_return", 0.0),
            "oos_max_drawdown": oos_stats.get("max_drawdown", 0.0),
            "oos_num_trades": oos_stats.get("num_trades", 0),
        })

    if not fold_results:
        return empty

    mean_is = sum(is_vals) / len(is_vals)
    mean_oos = sum(oos_vals) / len(oos_vals)
    return {
        "strategy": strategy,
        "metric": metric,
        "market": market,
        "folds": len(fold_results),
        "oos": {
            "compound_return": round((growth - 1.0) * 100.0, 2),
            "avg_fold_return": round(sum(oos_returns) / len(oos_returns), 2),
            "avg_buy_hold_return": round(sum(oos_bh) / len(oos_bh), 2),
            "num_trades": total_oos_trades,
            f"avg_{metric}": round(mean_oos, 4),
        },
        "is_vs_oos": {
            "metric": metric,
            "mean_is": round(mean_is, 4),
            "mean_oos": round(mean_oos, 4),
            # positive gap => params looked better in-sample than out (overfit)
            "overfit_gap": round(mean_is - mean_oos, 4),
        },
        "fold_results": fold_results,
    }
