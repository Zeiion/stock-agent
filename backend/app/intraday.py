"""做 T 当日高低点预测 + 挂单建议（纯 pandas/numpy，零依赖，优雅降级）。

从**日 K 历史**预估当日的高点 / 低点与可挂单区间，给「高抛低吸」做 T 建议。
不做任何模型训练、不依赖 GPU，与 `indicators.py` / `backtest.py` 同风格——一组
成熟、确定性、可解释的经典估计器做**集成**，永不抛错。

调研采纳的方法（详见 docs/RESEARCH.md「做 T 当日高低点预测」一节）：

  1. **枢轴点 Pivot Points**：经典(Floor) / 斐波那契 / **Camarilla**。Camarilla 的
     H3/L3 是日内均值回归（反转）带，正是做 T 的高抛/低吸价位；H4/L4 作突破/止损。
  2. **ATR 投影**：anchor ± k·ATR(14)（Wilder），把昨日真实波幅投影成今日价带。
  3. **经验分位**：历史 (high-prevclose)/prevclose 与 (low-prevclose)/prevclose 的
     分布分位数——直接回答「高低点通常落在哪」(对历史日 K 的规律总结)。
  4. **波动率估计**：Parkinson / Garman-Klass / Rogers-Satchell / **Yang-Zhang**
     (含隔夜跳空) + **EWMA(λ=0.94)**；可选 `arch` 的 GARCH 一步预测，缺失则回退 EWMA。
     用 E[range]≈1.6σ（布朗运动极差期望）把日波动率换成价带。

集成：取各方法高点的中位数 / 低点的中位数作为共识，离散度→置信度。
A 股按涨跌停（默认 ±10%）夹取预测区间；做 T 建议感知 T+1。
"""
from __future__ import annotations

import json
import math
import time
from typing import Any, Optional

import numpy as np
import pandas as pd

from . import indicators, symbols

# E[max-min] of a driftless BM over a unit interval = σ·sqrt(8/π) ≈ 1.5958σ.
# Half-range each side of a central anchor ≈ 0.8σ.
_RANGE_FACTOR = math.sqrt(8.0 / math.pi)      # ~1.5958
_HALF_RANGE = _RANGE_FACTOR / 2.0             # ~0.7979
_EWMA_LAMBDA = 0.94                           # RiskMetrics daily
# Per-market daily price-limit (fraction). HK/US uninhibited. CN main-board ±10%
# (we can't tell ST ±5% / STAR&ChiNext ±20% from the symbol, so use the common
# main-board limit conservatively and flag it).
_PRICE_LIMIT = {"CN": 0.10}
_LOT = {"CN": 100, "HK": 100, "US": 1}        # rough board-lot for qty rounding


# --------------------------------------------------------------------------- #
# Pivot points (computed from the PRIOR session's OHLC)
# --------------------------------------------------------------------------- #
def pivot_points(prev_high: float, prev_low: float, prev_close: float) -> dict:
    """Classic (Floor), Fibonacci and Camarilla pivots from yesterday's H/L/C."""
    h, l, c = float(prev_high), float(prev_low), float(prev_close)
    r = h - l
    p = (h + l + c) / 3.0
    classic = {
        "p": p,
        "r1": 2 * p - l, "s1": 2 * p - h,
        "r2": p + r, "s2": p - r,
        "r3": h + 2 * (p - l), "s3": l - 2 * (h - p),
    }
    fib = {
        "p": p,
        "r1": p + 0.382 * r, "s1": p - 0.382 * r,
        "r2": p + 0.618 * r, "s2": p - 0.618 * r,
        "r3": p + 1.000 * r, "s3": p - 1.000 * r,
    }
    cam = {
        "h1": c + r * 1.1 / 12, "l1": c - r * 1.1 / 12,
        "h2": c + r * 1.1 / 6,  "l2": c - r * 1.1 / 6,
        "h3": c + r * 1.1 / 4,  "l3": c - r * 1.1 / 4,   # 反转/做T带
        "h4": c + r * 1.1 / 2,  "l4": c - r * 1.1 / 2,   # 突破/止损
        "h5": (h / l) * c if l else c, "l5": c - ((h / l) * c - c) if l else c,
    }
    return {"classic": _round_d(classic), "fibonacci": _round_d(fib),
            "camarilla": _round_d(cam)}


# --------------------------------------------------------------------------- #
# Volatility estimators from OHLC (return DAILY sigma, in return units)
# --------------------------------------------------------------------------- #
def _safe_log(a, b):
    a = np.asarray(a, dtype=float)
    b = np.asarray(b, dtype=float)
    with np.errstate(divide="ignore", invalid="ignore"):
        out = np.log(np.where((a > 0) & (b > 0), a / b, np.nan))
    return out


def parkinson(df: pd.DataFrame) -> Optional[float]:
    hl = _safe_log(df["high"], df["low"])
    hl = hl[np.isfinite(hl)]
    if hl.size < 2:
        return None
    var = np.mean(hl ** 2) / (4.0 * math.log(2.0))
    return float(math.sqrt(var)) if var > 0 else None


def garman_klass(df: pd.DataFrame) -> Optional[float]:
    hl = _safe_log(df["high"], df["low"])
    co = _safe_log(df["close"], df["open"])
    m = np.isfinite(hl) & np.isfinite(co)
    if m.sum() < 2:
        return None
    var = np.mean(0.5 * hl[m] ** 2 - (2 * math.log(2) - 1) * co[m] ** 2)
    return float(math.sqrt(var)) if var > 0 else None


def rogers_satchell(df: pd.DataFrame) -> Optional[float]:
    hc = _safe_log(df["high"], df["close"])
    ho = _safe_log(df["high"], df["open"])
    lc = _safe_log(df["low"], df["close"])
    lo = _safe_log(df["low"], df["open"])
    m = np.isfinite(hc) & np.isfinite(ho) & np.isfinite(lc) & np.isfinite(lo)
    if m.sum() < 2:
        return None
    var = np.mean(hc[m] * ho[m] + lc[m] * lo[m])
    return float(math.sqrt(var)) if var > 0 else None


def yang_zhang(df: pd.DataFrame) -> Optional[float]:
    """Yang-Zhang: overnight + open-close + Rogers-Satchell. Handles gaps + drift."""
    if len(df) < 3:
        return None
    o = df["open"].to_numpy(float)
    c = df["close"].to_numpy(float)
    prev_c = df["close"].shift(1).to_numpy(float)
    overnight = _safe_log(o, prev_c)          # ln(O_t / C_{t-1})
    openclose = _safe_log(c, o)               # ln(C_t / O_t)
    on = overnight[np.isfinite(overnight)]
    oc = openclose[np.isfinite(openclose)]
    rs = rogers_satchell(df)
    if on.size < 2 or oc.size < 2 or rs is None:
        return None
    n = min(on.size, oc.size)
    var_on = float(np.var(on, ddof=1))
    var_oc = float(np.var(oc, ddof=1))
    k = 0.34 / (1.34 + (n + 1) / (n - 1))
    var = var_on + k * var_oc + (1 - k) * (rs ** 2)
    return float(math.sqrt(var)) if var > 0 else None


def ewma_vol(df: pd.DataFrame, lam: float = _EWMA_LAMBDA) -> Optional[float]:
    """RiskMetrics EWMA daily sigma from close-to-close log returns."""
    c = df["close"].to_numpy(float)
    r = _safe_log(c[1:], c[:-1])
    r = r[np.isfinite(r)]
    if r.size < 2:
        return None
    var = float(np.var(r, ddof=0))            # seed with sample variance
    for x in r:
        var = lam * var + (1 - lam) * x * x
    return float(math.sqrt(var)) if var > 0 else None


def garch_vol(df: pd.DataFrame) -> Optional[float]:
    """Optional GARCH(1,1) 1-step-ahead daily sigma via the `arch` library.

    Returns None (caller falls back to EWMA) when `arch` isn't installed, the
    series is too short, or fitting fails — never raises.
    """
    c = df["close"].to_numpy(float)
    r = _safe_log(c[1:], c[:-1])
    r = r[np.isfinite(r)]
    if r.size < 100:                          # GARCH needs a decent sample
        return None
    try:
        from arch import arch_model            # optional dependency
        am = arch_model(r * 100.0, vol="GARCH", p=1, q=1, mean="Zero", dist="normal")
        res = am.fit(disp="off", show_warning=False)
        fc = res.forecast(horizon=1, reindex=False)
        var = float(fc.variance.values[-1, 0]) / (100.0 ** 2)
        return float(math.sqrt(var)) if var > 0 else None
    except Exception:
        return None


# --------------------------------------------------------------------------- #
# Empirical quantile of daily extremes vs previous close
# --------------------------------------------------------------------------- #
def empirical_extremes(df: pd.DataFrame, hi_q: float = 0.5, lo_q: float = 0.5
                       ) -> Optional[tuple[float, float]]:
    """Return (hi_ratio, lo_ratio): quantiles of (high-prevclose)/prevclose and
    (low-prevclose)/prevclose over the window. hi_ratio>=0, lo_ratio<=0 typically.
    """
    prev_c = df["close"].shift(1)
    hi = ((df["high"] - prev_c) / prev_c).to_numpy(float)
    lo = ((df["low"] - prev_c) / prev_c).to_numpy(float)
    hi = hi[np.isfinite(hi)]
    lo = lo[np.isfinite(lo)]
    if hi.size < 5 or lo.size < 5:
        return None
    return float(np.quantile(hi, hi_q)), float(np.quantile(lo, lo_q))


# --------------------------------------------------------------------------- #
# Ensemble level prediction
# --------------------------------------------------------------------------- #
def _round_price(x: Optional[float]) -> Optional[float]:
    if x is None or not np.isfinite(x):
        return None
    ax = abs(x)
    nd = 2 if ax >= 10 else 3 if ax >= 1 else 4
    return round(float(x), nd)


def _round_d(d: dict) -> dict:
    return {k: _round_price(v) for k, v in d.items()}


def predict_levels(symbol: str, df: pd.DataFrame, today_open: Optional[float] = None,
                   last: Optional[float] = None, lookback: int = 60,
                   use_garch: bool = False, limit_pct: Optional[float] = None) -> dict:
    """Predict today's high / low by combining the methods above.

    `df` is a daily OHLCV frame (oldest first, indexed by date). `today_open` /
    `last` are today's open / latest price if the session has begun (used as the
    central anchor; otherwise we anchor on the previous close). Never raises.
    """
    market, _ = symbols.parse(symbol)
    if df is None or df.empty or len(df) < 3 or "close" not in df:
        return _empty_levels(symbol, market)

    df = df.sort_index()
    win = df.tail(max(lookback, 20))
    prev = df.iloc[-1]                        # most recent *completed* daily bar
    prev_high = float(prev["high"])
    prev_low = float(prev["low"])
    prev_close = float(prev["close"])
    if prev_close <= 0:
        return _empty_levels(symbol, market)

    # central anchor: prefer today's open (intraday), else last, else prev close
    anchor = float(today_open) if today_open else (
        float(last) if last else prev_close)
    anchor_kind = "today_open" if today_open else ("last" if last else "prev_close")

    pivots = pivot_points(prev_high, prev_low, prev_close)
    atr_v = indicators._last(indicators.atr(win["high"], win["low"], win["close"]))

    # --- gather candidate (high, low) from each method ---
    methods: list[dict] = []

    def _add(name, hi, lo, note=""):
        if hi is not None and lo is not None and np.isfinite(hi) and np.isfinite(lo) \
                and hi > lo:
            methods.append({"name": name, "high": _round_price(hi),
                            "low": _round_price(lo), "note": note})

    # 1) Camarilla reversal band (H3/L3) — the做T core
    cam = pivots["camarilla"]
    _add("camarilla", cam["h3"], cam["l3"], "H3/L3 反转带（做T高抛低吸）")

    # 2) ATR projection off the anchor
    if atr_v:
        _add("atr", anchor + _HALF_RANGE * atr_v, anchor - _HALF_RANGE * atr_v,
             f"anchor±0.8·ATR(14)={atr_v}")

    # 3) empirical quantiles of daily extremes vs prev close
    emp = empirical_extremes(win)
    if emp is not None:
        hi_r, lo_r = emp
        _add("empirical", prev_close * (1 + hi_r), prev_close * (1 + lo_r),
             f"历史日内极值中位数 高{hi_r*100:.2f}%/低{lo_r*100:.2f}%")

    # 4) volatility-based band (best available estimator)
    vol = _vol_estimates(win, use_garch)
    sigma = vol.get("yang_zhang") or vol.get("garch") or vol.get("ewma") \
        or vol.get("parkinson")
    if sigma:
        _add("volatility", anchor * (1 + _HALF_RANGE * sigma),
             anchor * (1 - _HALF_RANGE * sigma),
             f"σ_day={sigma*100:.2f}% → E[极差]≈{_RANGE_FACTOR*sigma*100:.2f}%")

    if not methods:
        return _empty_levels(symbol, market)

    highs = np.array([m["high"] for m in methods], dtype=float)
    lows = np.array([m["low"] for m in methods], dtype=float)
    pred_high = float(np.median(highs))
    pred_low = float(np.median(lows))

    # confidence from cross-method agreement (lower dispersion → higher conf)
    disp = (np.std(highs) + np.std(lows)) / (2.0 * anchor) if anchor else 1.0
    confidence = int(max(5, min(95, round(100 - disp * 4000))))
    if prev.get("close") is not None and len(df) < lookback:
        confidence = int(confidence * 0.85)
    conviction = max(1, min(5, round(confidence / 20)))

    # A-share daily price-limit clamp
    lp = limit_pct if limit_pct is not None else _PRICE_LIMIT.get(market)
    limits = {"applied": False}
    if lp:
        up = prev_close * (1 + lp)
        dn = prev_close * (1 - lp)
        pred_high = min(pred_high, up)
        pred_low = max(pred_low, dn)
        limits = {"applied": True, "limit_pct": lp,
                  "limit_up": _round_price(up), "limit_down": _round_price(dn)}

    pred_high = _round_price(pred_high)
    pred_low = _round_price(pred_low)
    exp_range_pct = round((pred_high - pred_low) / prev_close * 100.0, 2)

    return {
        "symbol": symbol, "market": market,
        "anchor": _round_price(anchor), "anchor_kind": anchor_kind,
        "prev_close": _round_price(prev_close),
        "predicted_high": pred_high, "predicted_low": pred_low,
        "expected_range_pct": exp_range_pct,
        "confidence": confidence, "conviction": conviction,
        "atr14": atr_v,
        "methods": methods,
        "pivots": pivots,
        "volatility": _round_d({k: (v * 100 if v else v) for k, v in vol.items()}),
        "limits": limits,
        "ts": time.time(),
    }


def _vol_estimates(df: pd.DataFrame, use_garch: bool) -> dict:
    out = {
        "parkinson": parkinson(df),
        "garman_klass": garman_klass(df),
        "rogers_satchell": rogers_satchell(df),
        "yang_zhang": yang_zhang(df),
        "ewma": ewma_vol(df),
    }
    if use_garch:
        out["garch"] = garch_vol(df)
    return out


def _empty_levels(symbol: str, market: str) -> dict:
    return {
        "symbol": symbol, "market": market, "anchor": None, "anchor_kind": "none",
        "prev_close": None, "predicted_high": None, "predicted_low": None,
        "expected_range_pct": 0.0, "confidence": 0, "conviction": 1,
        "atr14": None, "methods": [], "pivots": {}, "volatility": {},
        "limits": {"applied": False}, "ts": time.time(),
        "note": "历史数据不足，无法预测",
    }


# --------------------------------------------------------------------------- #
# 做 T 挂单计划（高抛低吸）
# --------------------------------------------------------------------------- #
# A round trip pays ~2× commission + stamp(CN sell) + slippage. Below this the
# predicted spread isn't worth a 做T round-trip.
_MIN_VIABLE_SPREAD_PCT = 1.2


def day_t_plan(symbol: str, df: pd.DataFrame, position: Optional[dict] = None,
               today_open: Optional[float] = None, last: Optional[float] = None,
               t_fraction: float = 0.33, use_garch: bool = False) -> dict:
    """Build a 做T (intraday band) limit-order plan from predicted levels.

    Holders (position qty>0): suggest selling part of the position near the
    predicted high (高抛) and buying it back near the predicted low (低吸) to
    lower cost basis. No position: range guidance only (低吸建仓 / 高抛减仓).
    Never raises.
    """
    lv = predict_levels(symbol, df, today_open=today_open, last=last,
                        use_garch=use_garch)
    market = lv["market"]
    pred_high = lv["predicted_high"]
    pred_low = lv["predicted_low"]
    pos = position or {}
    qty = int(pos.get("qty") or 0)
    avg = pos.get("avg_cost", pos.get("avg_price"))

    if pred_high is None or pred_low is None:
        lv["plan"] = {"viable": False, "actions": [],
                      "note": lv.get("note", "无法生成做T计划")}
        return lv

    cam = lv["pivots"].get("camarilla", {})
    buy_limit = pred_low                       # 低吸挂单价
    sell_limit = pred_high                     # 高抛挂单价
    # Invalidation levels must sit at/beyond the band edges, else "breakout"
    # could be inside the trading band (Camarilla H4/L4 come from yesterday's
    # single-bar range, which can be narrower than the ensemble band).
    breakout_above = max(cam.get("h4") or pred_high, pred_high)
    stop_below = min(cam.get("l4") or pred_low, pred_low)
    spread_pct = round((sell_limit - buy_limit) / buy_limit * 100.0, 2) \
        if buy_limit else 0.0
    viable = spread_pct >= _MIN_VIABLE_SPREAD_PCT

    actions: list[str] = []
    caveats: list[str] = []

    # suggested做T quantity, rounded down to a board lot
    lot = _LOT.get(market, 1)
    suggested_qty = 0
    if qty > 0:
        raw = int(qty * max(0.1, min(1.0, t_fraction)))
        suggested_qty = (raw // lot) * lot if lot > 1 else raw

    last_p = float(last) if last else lv["anchor"]

    if qty > 0 and suggested_qty > 0 and viable:
        actions.append(
            f"高抛：在 ≈{sell_limit}（区间 {cam.get('h3', sell_limit)}~"
            f"{lv['limits'].get('limit_up') or sell_limit}）挂卖出 {suggested_qty} 股，"
            f"卖出的是已持有底仓。")
        actions.append(
            f"低吸：成交后在 ≈{buy_limit}（区间 "
            f"{lv['limits'].get('limit_down') or buy_limit}~{cam.get('l3', buy_limit)}）"
            f"挂买回 {suggested_qty} 股，降低持仓成本。")
        est_gain = round((sell_limit - buy_limit) * suggested_qty, 2)
        actions.append(f"若高抛低吸均成交，预计降低成本约 {est_gain} "
                       f"（{spread_pct}% × {suggested_qty} 股）。")
    elif qty > 0 and not viable:
        actions.append(f"预测振幅仅 {spread_pct}%，低于做T性价比阈值 "
                       f"{_MIN_VIABLE_SPREAD_PCT}%，建议今日不做T、持仓不动。")
    else:
        actions.append(f"低吸参考：≈{buy_limit} 附近分批买入（建仓/底仓）。")
        actions.append(f"高抛参考：≈{sell_limit} 附近减仓。")
        if market == "CN":
            caveats.append("A股无底仓无法当日高抛低吸做T（T+1）；可先低吸建底仓，次日起做T。")

    # T+1 / 涨跌停 合规提示
    if market == "CN":
        caveats.append("A股 T+1：高抛卖出的是昨日底仓；当日买回的部分要次日才能再卖。")
        if lv["limits"].get("applied"):
            caveats.append(f"已按涨跌停 ±{int(lv['limits']['limit_pct']*100)}% "
                           f"夹取预测区间（主板口径，ST/创业板/科创板限幅不同）。")
    # 突破失效提示
    caveats.append(f"突破止损：升破 {_round_price(breakout_above)} 视为向上突破"
                   f"（高抛逻辑失效）；跌破 {_round_price(stop_below)} 视为破位"
                   f"（低吸慎防接飞刀）。")
    if lv["anchor_kind"] == "prev_close":
        caveats.append("当前为盘前/无今开价，锚定昨收；开盘后预测会更准。")

    lv["plan"] = {
        "viable": viable,
        "buy_limit": buy_limit,
        "sell_limit": sell_limit,
        "buy_zone": [lv["limits"].get("limit_down") or buy_limit, _round_price(cam.get("l3"))],
        "sell_zone": [_round_price(cam.get("h3")), lv["limits"].get("limit_up") or sell_limit],
        "spread_pct": spread_pct,
        "stop_below": _round_price(stop_below),
        "breakout_above": _round_price(breakout_above),
        "position_qty": qty,
        "avg_price": avg,
        "suggested_qty": suggested_qty,
        "last": _round_price(last_p),
        "actions": actions,
        "caveats": caveats,
    }
    return lv


# --------------------------------------------------------------------------- #
# Optional AI 经验分析 layer (reuses the existing AIBrain.run_schema)
# --------------------------------------------------------------------------- #
DAYT_AI_SCHEMA: dict = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "recommend": {
            "type": "string", "enum": ["做T", "观望", "不建议"],
            "description": "今日是否值得做T的总体建议。",
        },
        "narrative": {
            "type": "string",
            "description": "简体中文经验分析：结合统计预测、枢轴位、波动率、近K线与持仓，"
                           "说明今日高低点与做T理由。",
        },
        "buy_limit": {"type": "number", "description": "建议低吸买回价（应落在预测带内）。"},
        "sell_limit": {"type": "number", "description": "建议高抛卖出价（应落在预测带内）。"},
        "confidence": {"type": "integer", "minimum": 1, "maximum": 5},
        "risks": {"type": "array", "items": {"type": "string"},
                  "description": "简体中文风险提示。"},
    },
    "required": ["recommend", "narrative", "buy_limit", "sell_limit",
                 "confidence", "risks"],
}

_DAYT_AI_SYSTEM = (
    "你是一名擅长『做T』(日内高抛低吸降成本)的资深交易员。你会收到一份对该标的当日"
    "高/低点的【统计预测】(多方法集成：Camarilla枢轴、ATR投影、历史日内极值分位、波动率)，"
    "以及枢轴位、波动率、最近K线与当前持仓。请做一段经验分析(narrative，简体中文)，"
    "判断今日是否值得做T(recommend)，并在【预测带内】给出具体的低吸买回价(buy_limit)与"
    "高抛卖出价(sell_limit)——不要给出超出预测高低点太多的离谱价位。"
    "务必尊重：A股 T+1(高抛的是昨日底仓，当日买回次日才能卖)与涨跌停限制；振幅过窄时"
    "建议观望。不要臆造未提供的数据。只返回符合 schema 的 JSON 对象。"
)


async def ai_commentary(symbol: str, lv: dict, recent_candles: list[dict],
                        provider: Optional[str] = None) -> Optional[dict]:
    """Ask the AI brain for a 做T 经验分析 over the statistical prediction.

    Returns a dict {recommend, narrative, buy_limit, sell_limit, confidence,
    risks, provider} with AI prices sanitized to stay near the predicted band,
    or None on failure (caller keeps the deterministic plan). Never raises.
    """
    pred_high = lv.get("predicted_high")
    pred_low = lv.get("predicted_low")
    if pred_high is None or pred_low is None:
        return None
    from .ai.brain import brain                # lazy import; keeps core dep-light

    plan = lv.get("plan", {})
    payload = {
        "predicted_high": pred_high, "predicted_low": pred_low,
        "anchor": lv.get("anchor"), "prev_close": lv.get("prev_close"),
        "expected_range_pct": lv.get("expected_range_pct"),
        "atr14": lv.get("atr14"),
        "camarilla": lv.get("pivots", {}).get("camarilla", {}),
        "volatility_pct": lv.get("volatility", {}),
        "methods": lv.get("methods", []),
        "position_qty": plan.get("position_qty"), "avg_price": plan.get("avg_price"),
        "last": plan.get("last"),
        "spread_pct": plan.get("spread_pct"),
    }
    prompt = "\n".join([
        f"标的：{symbol}（{lv.get('market')} 市场）。请对今日做T给出经验分析与挂单价。",
        "",
        "== 统计预测与盘口 ==",
        json.dumps(payload, ensure_ascii=False),
        "",
        "== 最近K线 (由旧到新) ==",
        json.dumps(recent_candles[-20:], ensure_ascii=False) if recent_candles
        else "(无K线)",
        "",
        "buy_limit/sell_limit 必须落在预测低/高点附近。只返回符合 schema 的 JSON。",
    ])
    try:
        raw, prov = await brain.run_schema(prompt, _DAYT_AI_SYSTEM,
                                          DAYT_AI_SCHEMA, provider)
    except Exception as e:  # noqa: BLE001
        print(f"[intraday] ai_commentary failed: {e}")
        return None

    # sanitize AI prices: keep within a small tolerance of the predicted band
    lo_floor = pred_low * 0.97
    hi_cap = pred_high * 1.03
    buy = _clip_num(raw.get("buy_limit"), lo_floor, hi_cap, pred_low)
    sell = _clip_num(raw.get("sell_limit"), lo_floor, hi_cap, pred_high)
    rec = raw.get("recommend")
    if rec not in ("做T", "观望", "不建议"):
        rec = "观望"
    try:
        conf = max(1, min(5, int(raw.get("confidence", 3))))
    except (TypeError, ValueError):
        conf = 3
    risks = raw.get("risks")
    risks = [str(r) for r in risks if r] if isinstance(risks, (list, tuple)) else []
    return {
        "recommend": rec,
        "narrative": str(raw.get("narrative", "")).strip() or "(无分析)",
        "buy_limit": _round_price(buy), "sell_limit": _round_price(sell),
        "confidence": conf, "risks": risks, "provider": prov,
    }


def _clip_num(x, lo: float, hi: float, default: float) -> float:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return default
    if not np.isfinite(v):
        return default
    return min(hi, max(lo, v))
