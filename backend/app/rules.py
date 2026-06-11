"""RulesEngine — pure, stateless condition evaluation for alert rules.

The daemon owns the lifecycle (which rules are active, cooldown gating, persistence,
notification). This module answers exactly one question per rule:

    "Given this quote + indicator snapshot (+ optional held position), is the
     rule's condition TRUE *right now*?"

If TRUE, an `Alert` is produced with a human-readable message that embeds the
actual numbers so the notification is self-explanatory. Cooldown and dedup are
NOT handled here — see MonitorDaemon._eval_rules.

Indicator snapshot keys consumed here come from indicators.compute_indicators:
    ma5/ma10/ma20/ma60 (+ ma5_prev, ma20_prev),
    rsi14, macd/macd_signal (+ *_prev), j/j_prev (+ k/k_prev),
    vol, vol_ma20.
Any of these may be None on short/empty history, so every accessor is guarded —
a rule whose required data is missing is silently skipped (never crashes).
"""
from __future__ import annotations

from typing import Any, Optional

from .models import Alert, Quote, RuleType


# Map fast/slow MA periods -> (current key, previous key) in the snapshot dict.
# Only periods that compute_indicators exposes a *_prev for can detect a cross;
# ma10/ma60 have no *_prev, so cross detection for those legs falls back to None
# (and the rule is skipped — we never guess a cross without the prior value).
_MA_NOW = {5: "ma5", 10: "ma10", 20: "ma20", 60: "ma60"}
_MA_PREV = {5: "ma5_prev", 20: "ma20_prev"}


def _num(value: Any) -> Optional[float]:
    """Coerce to float, returning None for missing / non-numeric values."""
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


class RulesEngine:
    """Stateless evaluator. Construct once; call `evaluate` per (symbol, tick)."""

    def evaluate(
        self,
        symbol: str,
        quote: Quote,
        indicators: dict,
        position: dict | None,
        rules: list[dict],
    ) -> list[Alert]:
        """Return an Alert for every ACTIVE rule whose condition is currently TRUE.

        Args mirror what the daemon supplies:
          - `quote`      : latest Quote (has .last, .change_pct, etc.)
          - `indicators` : compute_indicators snapshot (may be {} or have None values)
          - `position`   : db.get_position(symbol) dict, or None if not held
          - `rules`      : list of db.list_rules() dicts for this symbol
        """
        indicators = indicators or {}
        alerts: list[Alert] = []

        for rule in rules:
            # The daemon already filters with active_only=True, but be defensive.
            if not rule.get("active", True):
                continue
            try:
                msg = self._check(quote, indicators, position, rule)
            except Exception:
                # A single malformed rule must never sink the whole evaluation pass.
                msg = None
            if msg:
                alerts.append(self._build_alert(symbol, quote, indicators, rule, msg))

        return alerts

    # ------------------------------------------------------------------ #
    # Alert construction
    # ------------------------------------------------------------------ #
    def _build_alert(
        self,
        symbol: str,
        quote: Quote,
        indicators: dict,
        rule: dict,
        message: str,
    ) -> Alert:
        return Alert(
            symbol=symbol,
            rule_id=rule.get("id"),
            rule_type=rule.get("type", ""),
            severity=rule.get("severity", "normal"),
            message=message,
            snapshot={"quote": quote.to_dict(), "indicators": indicators},
        )

    # ------------------------------------------------------------------ #
    # Condition dispatch
    # ------------------------------------------------------------------ #
    def _check(
        self,
        quote: Quote,
        ind: dict,
        position: dict | None,
        rule: dict,
    ) -> Optional[str]:
        """Return a message string if the rule fires, else None.

        `None` is also used as the universal "can't evaluate / data missing"
        signal so any guard short-circuit safely results in no alert.
        """
        rtype = rule.get("type")
        params = rule.get("params") or {}
        # Friendly label for messages; fall back to canonical symbol.
        tag = quote.name or quote.symbol

        # --- price thresholds -------------------------------------------- #
        if rtype == RuleType.PRICE_ABOVE.value:
            target = _num(params.get("price"))
            if target is None or quote.last is None:
                return None
            if quote.last >= target:
                return f"{tag} price {quote.last:g} >= {target:g} (above threshold)"
            return None

        if rtype == RuleType.PRICE_BELOW.value:
            target = _num(params.get("price"))
            if target is None or quote.last is None:
                return None
            if quote.last <= target:
                return f"{tag} price {quote.last:g} <= {target:g} (below threshold)"
            return None

        # --- intraday percent move (abs, vs prev_close) ------------------ #
        if rtype == RuleType.PCT_MOVE.value:
            threshold = _num(params.get("pct"))
            if threshold is None:
                return None
            move = quote.change_pct  # signed; compare absolute magnitude
            if abs(move) >= threshold:
                return (f"{tag} moved {move:+.2f}% "
                        f"(|move| >= {threshold:g}%, last {quote.last:g})")
            return None

        # --- RSI thresholds ---------------------------------------------- #
        if rtype == RuleType.RSI_ABOVE.value:
            value = _num(params.get("value"))
            rsi = _num(ind.get("rsi14"))
            if value is None or rsi is None:
                return None
            if rsi >= value:
                return f"{tag} RSI14 {rsi:.1f} >= {value:g} (overbought zone)"
            return None

        if rtype == RuleType.RSI_BELOW.value:
            value = _num(params.get("value"))
            rsi = _num(ind.get("rsi14"))
            if value is None or rsi is None:
                return None
            if rsi <= value:
                return f"{tag} RSI14 {rsi:.1f} <= {value:g} (oversold zone)"
            return None

        # --- moving-average cross (golden / death) ----------------------- #
        if rtype == RuleType.MA_CROSS.value:
            return self._ma_cross(tag, ind, params)

        # --- MACD line crossing signal line ------------------------------ #
        if rtype == RuleType.MACD_CROSS.value:
            return self._macd_cross(tag, ind)

        # --- KDJ J crossing 80 (down) / 20 (up) -------------------------- #
        if rtype == RuleType.KDJ_CROSS.value:
            return self._kdj_cross(tag, ind)

        # --- volume spike vs 20-day average ------------------------------ #
        if rtype == RuleType.VOLUME_SPIKE.value:
            mult = _num(params.get("mult"))
            vol = _num(ind.get("vol"))
            vol_ma = _num(ind.get("vol_ma20"))
            if mult is None or vol is None or not vol_ma:  # vol_ma 0/None -> skip
                return None
            if vol >= vol_ma * mult:
                ratio = vol / vol_ma
                return (f"{tag} volume spike: {vol:.0f} = {ratio:.2f}x "
                        f"20d-avg ({vol_ma:.0f}), >= {mult:g}x")
            return None

        # --- position-conditional stop / target -------------------------- #
        if rtype == RuleType.STOP_LOSS.value:
            if not position:  # only meaningful while holding the symbol
                return None
            price = _num(params.get("price"))
            if price is None or quote.last is None:
                return None
            if quote.last <= price:
                return (f"{tag} STOP-LOSS hit: last {quote.last:g} "
                        f"<= stop {price:g}")
            return None

        if rtype == RuleType.TAKE_PROFIT.value:
            if not position:
                return None
            price = _num(params.get("price"))
            if price is None or quote.last is None:
                return None
            if quote.last >= price:
                return (f"{tag} TAKE-PROFIT hit: last {quote.last:g} "
                        f">= target {price:g}")
            return None

        # Unknown / unsupported rule type -> no alert.
        return None

    # ------------------------------------------------------------------ #
    # Cross helpers (need both previous and current values to detect a flip)
    # ------------------------------------------------------------------ #
    def _ma_cross(self, tag: str, ind: dict, params: dict) -> Optional[str]:
        """Golden cross = fast crosses ABOVE slow; death cross = below.

        Requires *_prev for BOTH legs so we can compare the relation across two
        bars. Only periods 5 and 20 carry a *_prev in the snapshot; any other
        combination lacks the data and is skipped.
        """
        fast = params.get("fast")
        slow = params.get("slow")
        if fast not in _MA_NOW or slow not in _MA_NOW or fast == slow:
            return None
        # Need previous values for both legs to detect a cross.
        if fast not in _MA_PREV or slow not in _MA_PREV:
            return None

        fast_now = _num(ind.get(_MA_NOW[fast]))
        slow_now = _num(ind.get(_MA_NOW[slow]))
        fast_prev = _num(ind.get(_MA_PREV[fast]))
        slow_prev = _num(ind.get(_MA_PREV[slow]))
        if None in (fast_now, slow_now, fast_prev, slow_prev):
            return None

        prev_diff = fast_prev - slow_prev
        now_diff = fast_now - slow_now
        # Golden cross: was at/below, now strictly above.
        if prev_diff <= 0 and now_diff > 0:
            return (f"{tag} golden cross: MA{fast} crossed above MA{slow} "
                    f"(MA{fast} {fast_now:g} > MA{slow} {slow_now:g})")
        # Death cross: was at/above, now strictly below.
        if prev_diff >= 0 and now_diff < 0:
            return (f"{tag} death cross: MA{fast} crossed below MA{slow} "
                    f"(MA{fast} {fast_now:g} < MA{slow} {slow_now:g})")
        return None

    def _macd_cross(self, tag: str, ind: dict) -> Optional[str]:
        """MACD line crossing its signal line, in either direction."""
        macd_now = _num(ind.get("macd"))
        sig_now = _num(ind.get("macd_signal"))
        macd_prev = _num(ind.get("macd_prev"))
        sig_prev = _num(ind.get("macd_signal_prev"))
        if None in (macd_now, sig_now, macd_prev, sig_prev):
            return None

        prev_diff = macd_prev - sig_prev
        now_diff = macd_now - sig_now
        if prev_diff <= 0 and now_diff > 0:
            return (f"{tag} MACD bullish cross: MACD crossed above signal "
                    f"({macd_now:g} > {sig_now:g})")
        if prev_diff >= 0 and now_diff < 0:
            return (f"{tag} MACD bearish cross: MACD crossed below signal "
                    f"({macd_now:g} < {sig_now:g})")
        return None

    def _kdj_cross(self, tag: str, ind: dict) -> Optional[str]:
        """KDJ J-line crossing key thresholds:
          - crossing 80 DOWNWARD  -> overbought warning (sell signal)
          - crossing 20 UPWARD    -> oversold rebound  (buy signal)
        Needs j_prev + j to know the direction of the crossing.
        """
        j_now = _num(ind.get("j"))
        j_prev = _num(ind.get("j_prev"))
        if j_now is None or j_prev is None:
            return None

        # Downward cross of the 80 overbought line.
        if j_prev >= 80 and j_now < 80:
            return (f"{tag} KDJ-J crossed below 80 (overbought fading): "
                    f"J {j_prev:.1f} -> {j_now:.1f}")
        # Upward cross of the 20 oversold line.
        if j_prev <= 20 and j_now > 20:
            return (f"{tag} KDJ-J crossed above 20 (oversold rebound): "
                    f"J {j_prev:.1f} -> {j_now:.1f}")
        return None
