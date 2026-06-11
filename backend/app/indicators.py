"""Technical indicators in pure pandas/numpy (no native deps, always installable).

Includes the A-share-standard KDJ (1/3-weight SMA smoothing), which differs from
the Western Stochastic that TA-Lib returns — local Chinese charts won't match
TA-Lib STOCH, so KDJ is computed here explicitly.

All functions take/return pandas Series unless noted. `compute_indicators` returns
a compact snapshot dict the rules engine and AI prompt consume.
"""
from __future__ import annotations

from typing import Any, Optional

import numpy as np
import pandas as pd


def sma(s: pd.Series, n: int) -> pd.Series:
    return s.rolling(n, min_periods=1).mean()


def ema(s: pd.Series, n: int) -> pd.Series:
    return s.ewm(span=n, adjust=False).mean()


def rsi(close: pd.Series, n: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0.0)
    loss = -delta.clip(upper=0.0)
    avg_gain = gain.ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    avg_loss = loss.ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    out = 100 - (100 / (1 + rs))
    return out.fillna(50.0)


def macd(close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9
         ) -> tuple[pd.Series, pd.Series, pd.Series]:
    macd_line = ema(close, fast) - ema(close, slow)
    signal_line = ema(macd_line, signal)
    hist = macd_line - signal_line
    return macd_line, signal_line, hist


def bollinger(close: pd.Series, n: int = 20, k: float = 2.0
              ) -> tuple[pd.Series, pd.Series, pd.Series]:
    mid = sma(close, n)
    std = close.rolling(n, min_periods=1).std(ddof=0)
    upper = mid + k * std
    lower = mid - k * std
    return upper, mid, lower


def kdj(high: pd.Series, low: pd.Series, close: pd.Series, n: int = 9
        ) -> tuple[pd.Series, pd.Series, pd.Series]:
    """China-standard KDJ. K=2/3·prevK+1/3·RSV, D=2/3·prevD+1/3·K, J=3K-2D,
    seeded at 50. Thresholds: >80 overbought, <20 oversold; J can overshoot."""
    low_n = low.rolling(n, min_periods=1).min()
    high_n = high.rolling(n, min_periods=1).max()
    rng = (high_n - low_n).replace(0, np.nan)
    rsv = ((close - low_n) / rng * 100).fillna(50.0)
    k = rsv.ewm(alpha=1 / 3, adjust=False).mean()
    d = k.ewm(alpha=1 / 3, adjust=False).mean()
    j = 3 * k - 2 * d
    return k, d, j


def atr(high: pd.Series, low: pd.Series, close: pd.Series, n: int = 14) -> pd.Series:
    prev_close = close.shift(1)
    tr = pd.concat([(high - low),
                    (high - prev_close).abs(),
                    (low - prev_close).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / n, adjust=False).mean()


def _last(s: pd.Series) -> Optional[float]:
    if s is None or len(s) == 0:
        return None
    v = s.iloc[-1]
    return None if pd.isna(v) else round(float(v), 4)


def _prev(s: pd.Series) -> Optional[float]:
    if s is None or len(s) < 2:
        return None
    v = s.iloc[-2]
    return None if pd.isna(v) else round(float(v), 4)


def compute_indicators(df: pd.DataFrame) -> dict[str, Any]:
    """`df` must have columns: open, high, low, close, volume (chronological).

    Returns a flat snapshot of the latest indicator values plus the previous
    value for cross detection. Safe on short / empty frames.
    """
    if df is None or df.empty or "close" not in df:
        return {}
    df = df.sort_index()
    close, high, low, vol = df["close"], df["high"], df["low"], df["volume"]

    ma5, ma10, ma20, ma60 = sma(close, 5), sma(close, 10), sma(close, 20), sma(close, 60)
    r = rsi(close, 14)
    macd_line, signal_line, hist = macd(close)
    bb_u, bb_m, bb_l = bollinger(close)
    k, d, j = kdj(high, low, close)
    vol_ma = sma(vol, 20)

    last_close = _last(close) or 0.0
    snap: dict[str, Any] = {
        "close": last_close,
        "ma5": _last(ma5), "ma10": _last(ma10), "ma20": _last(ma20), "ma60": _last(ma60),
        "ma5_prev": _prev(ma5), "ma20_prev": _prev(ma20),
        "rsi14": _last(r), "rsi14_prev": _prev(r),
        "macd": _last(macd_line), "macd_signal": _last(signal_line),
        "macd_hist": _last(hist),
        "macd_prev": _prev(macd_line), "macd_signal_prev": _prev(signal_line),
        "boll_upper": _last(bb_u), "boll_mid": _last(bb_m), "boll_lower": _last(bb_l),
        "k": _last(k), "d": _last(d), "j": _last(j),
        "k_prev": _prev(k), "j_prev": _prev(j),
        "atr14": _last(atr(high, low, close)),
        "vol": _last(vol), "vol_ma20": _last(vol_ma),
        "bars": int(len(df)),
    }
    # human-readable trend tags for the AI prompt
    tags = []
    if snap["ma5"] and snap["ma20"]:
        tags.append("MA5>MA20 (bullish)" if snap["ma5"] > snap["ma20"]
                    else "MA5<MA20 (bearish)")
    if snap["rsi14"] is not None:
        if snap["rsi14"] >= 70: tags.append("RSI overbought")
        elif snap["rsi14"] <= 30: tags.append("RSI oversold")
    if snap["j"] is not None:
        if snap["j"] >= 80: tags.append("KDJ-J overbought")
        elif snap["j"] <= 20: tags.append("KDJ-J oversold")
    if snap["macd_hist"] is not None:
        tags.append("MACD>signal" if snap["macd_hist"] > 0 else "MACD<signal")
    snap["tags"] = tags
    return snap
