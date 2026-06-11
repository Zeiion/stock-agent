"""AI decision track-record / reflection — closes the feedback loop.

Given the latest quotes and the persisted decision history, score each past AI
decision by the price move since it was made: did the directional call (BUY/ADD,
SELL/REDUCE, HOLD) play out? We persist the realized move back onto the decision
row and aggregate accuracy overall, per-action, and a BUY-signal "alpha" proxy.

This is intentionally a cheap, price-only proxy (no benchmark, no holding-period
matching) — a directional sanity check on whether the brain is adding value.

All user-facing strings are Simplified Chinese.
"""
from __future__ import annotations

from typing import Any

from . import db


# Direction-correctness thresholds (percent move since decision).
_BUY_THRESH = 0.5     # BUY/ADD correct if move_pct > +0.5%
_SELL_THRESH = -0.5   # SELL/REDUCE correct if move_pct < -0.5%
_HOLD_BAND = 3.0      # HOLD correct if |move_pct| <= 3.0%

_BUY_ACTIONS = {"BUY", "ADD"}
_SELL_ACTIONS = {"SELL", "REDUCE"}


def _is_correct(action: str, move_pct: float) -> bool:
    """Was the directional call validated by the subsequent price move?"""
    if action in _BUY_ACTIONS:
        return move_pct > _BUY_THRESH
    if action in _SELL_ACTIONS:
        return move_pct < _SELL_THRESH
    if action == "HOLD":
        return abs(move_pct) <= _HOLD_BAND
    # Unknown action: treat as a non-directional call, score by HOLD band.
    return abs(move_pct) <= _HOLD_BAND


def _empty_result() -> dict[str, Any]:
    return {
        "scored": 0,
        "correct": 0,
        "accuracy": 0.0,
        "avg_move": 0.0,
        "buy_signal_alpha": 0.0,
        "by_action": {},
        "by_strategy": {},
        "recent": [],
    }


def track_record(latest: dict, limit: int = 200) -> dict:
    """Score historical AI decisions against the latest prices.

    Args:
        latest: dict[symbol -> Quote.to_dict()]; each value has a "last" price.
        limit:  how many recent decisions to pull from the DB.

    Returns:
        Aggregate accuracy stats plus a recent (most-recent-first) scored list.
        Never raises — degrades to zeros / empty on any failure.
    """
    try:
        decisions = db.list_decisions(limit=limit)
    except Exception as e:  # pragma: no cover - defensive
        print(f"[reflection] list_decisions failed: {e}")
        return _empty_result()

    latest = latest or {}

    scored = 0
    correct = 0
    move_sum = 0.0
    buy_moves: list[float] = []
    # action -> {"count", "correct", "move_sum"}
    by_action: dict[str, dict[str, float]] = {}
    by_strategy: dict[str, dict[str, float]] = {}
    recent: list[dict[str, Any]] = []

    for d in decisions:
        try:
            symbol = d.get("symbol")
            action = (d.get("action") or "").upper()
            strategy = d.get("strategy") or "balanced"

            entry = (d.get("snapshot") or {}).get("quote", {}).get("last")
            cur = (latest.get(symbol) or {}).get("last")

            # Unscored if either price missing or entry is non-positive.
            if entry is None or cur is None:
                continue
            try:
                entry = float(entry)
                cur = float(cur)
            except (TypeError, ValueError):
                continue
            if entry <= 0:
                continue

            move_pct = (cur - entry) / entry * 100.0
            ok = _is_correct(action, move_pct)

            # Persist the realized move back onto the decision row.
            did = d.get("id")
            if did is not None:
                try:
                    db.update_decision_realized(did, round(move_pct, 3))
                except Exception as e:  # pragma: no cover - defensive
                    print(f"[reflection] update_decision_realized({did}) failed: {e}")

            scored += 1
            move_sum += move_pct
            if ok:
                correct += 1

            agg = by_action.setdefault(
                action, {"count": 0, "correct": 0, "move_sum": 0.0})
            agg["count"] += 1
            agg["move_sum"] += move_pct
            if ok:
                agg["correct"] += 1

            sagg = by_strategy.setdefault(
                strategy, {"count": 0, "correct": 0, "move_sum": 0.0})
            sagg["count"] += 1
            sagg["move_sum"] += move_pct
            if ok:
                sagg["correct"] += 1

            if action in _BUY_ACTIONS:
                buy_moves.append(move_pct)

            if len(recent) < 30:
                recent.append({
                    "symbol": symbol,
                    "action": action,
                    "strategy": strategy,
                    "conviction": d.get("conviction"),
                    "provider": d.get("provider"),
                    "entry": round(entry, 4),
                    "current": round(cur, 4),
                    "move_pct": round(move_pct, 2),
                    "correct": ok,
                    "ts": d.get("ts"),
                })
        except Exception as e:  # pragma: no cover - defensive, skip bad row
            print(f"[reflection] skipping decision {d.get('id')}: {e}")
            continue

    if scored == 0:
        return _empty_result()

    by_action_out: dict[str, dict[str, Any]] = {}
    for act, agg in by_action.items():
        cnt = int(agg["count"])
        cor = int(agg["correct"])
        by_action_out[act] = {
            "count": cnt,
            "correct": cor,
            "accuracy": round(cor / cnt * 100.0, 2) if cnt else 0.0,
            "avg_move": round(agg["move_sum"] / cnt, 2) if cnt else 0.0,
        }

    by_strategy_out: dict[str, dict[str, Any]] = {}
    for strat, agg in by_strategy.items():
        cnt = int(agg["count"])
        cor = int(agg["correct"])
        by_strategy_out[strat] = {
            "count": cnt,
            "correct": cor,
            "accuracy": round(cor / cnt * 100.0, 2) if cnt else 0.0,
            "avg_move": round(agg["move_sum"] / cnt, 2) if cnt else 0.0,
        }

    buy_signal_alpha = (
        round(sum(buy_moves) / len(buy_moves), 2) if buy_moves else 0.0)

    return {
        "scored": scored,
        "correct": correct,
        "accuracy": round(correct / scored * 100.0, 2),
        "avg_move": round(move_sum / scored, 2),
        "buy_signal_alpha": buy_signal_alpha,
        "by_action": by_action_out,
        "by_strategy": by_strategy_out,
        "recent": recent,
    }
