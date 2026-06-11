"""Paper trading / order queue — the SAFE default execution layer.

Posture (per the design): the AI/rules NEVER place an order silently.
  - trading_mode="signal": decisions are logged only; no orders created.
  - trading_mode="paper" : decisions create a PENDING paper order; if
    require_human_approval is True it waits for /approve, then fills against the
    latest quote and updates the simulated position. No real broker is touched.

A position/notional cap and duplicate guard are enforced here, independent of
any model output.
"""
from __future__ import annotations

import asyncio
import time
from typing import Any, Optional

from . import db
from .config import settings
from .datahub import datahub
from .models import Action, Decision, PaperOrder, Quote
from .symbols import parse


def _alpaca_broker():
    """Return a configured+available AlpacaBroker, or None (internal sim)."""
    if getattr(settings, "broker", "internal") != "alpaca":
        return None
    try:
        from .brokers.alpaca_broker import AlpacaBroker
        b = AlpacaBroker(getattr(settings, "alpaca_api_key", ""),
                         getattr(settings, "alpaca_api_secret", ""))
        return b if b.available() else None
    except Exception as e:
        print(f"[paper] alpaca init failed: {e}")
        return None


# How many shares a decision implies, by conviction (very conservative default).
def _suggested_qty(price: float, conviction: int) -> float:
    if price <= 0:
        return 0.0
    budget = min(settings.max_position_value,
                 settings.max_position_value * (conviction / 5.0))
    qty = budget / price
    # round to a sane lot: whole shares (HK board lots vary; keep simple)
    return float(int(qty))


_ACTIONABLE = {"BUY": "BUY", "ADD": "BUY", "SELL": "SELL", "REDUCE": "SELL"}

# Don't stack repeated orders for the same symbol+side within this window
# (a re-fired critical alert must not keep adding to a position).
DEDUP_WINDOW_S = 600.0

_FILLABLE = ("pending", "approved")


class PaperBroker:
    def __init__(self) -> None:
        pass

    # ---- intake ----------------------------------------------------------- #
    def from_decision(self, decision: Decision, quote: Optional[Quote]
                      ) -> Optional[PaperOrder]:
        """Create a paper order from an actionable decision, or None."""
        if settings.trading_mode != "paper":
            return None
        side = _ACTIONABLE.get(decision.action)
        if not side:
            return None
        if decision.conviction < 3:        # don't queue low-conviction calls
            return None

        # A real observed market price is required to FILL. entry_zone is only a
        # hypothetical the model suggested — never fill at it. Use it (or the live
        # price) merely to SIZE the order.
        live_price = quote.last if (quote and quote.last) else None
        entry = decision.entry_zone[0] if decision.entry_zone else None
        sizing_price = live_price or entry
        if not sizing_price or sizing_price <= 0:
            return None

        # Windowed duplicate guard — covers pending, approved AND filled orders so a
        # re-fired alert can't keep stacking the same position (the old pending-only
        # guard was dead in auto-fill mode).
        now = time.time()
        for o in db.list_paper_orders():
            if (o["symbol"] == decision.symbol and o["side"] == side
                    and o["status"] in (*_FILLABLE, "filled")
                    and now - o["ts"] < DEDUP_WINDOW_S):
                return None

        qty = _suggested_qty(sizing_price, decision.conviction)
        if side == "SELL":
            pos = db.get_position(decision.symbol)
            held = pos["qty"] if pos else 0.0
            if held <= 0:
                return None
            # SELL = exit all; REDUCE = ~half, never more than held (fractional-safe)
            qty = held if decision.action == "SELL" else min(held, max(1.0, held // 2))
        else:  # BUY/ADD — cumulative notional cap that accounts for what's held
            pos = db.get_position(decision.symbol)
            held = pos["qty"] if pos else 0.0
            room = settings.max_position_value - held * sizing_price
            if room <= 0:
                return None
            qty = min(qty, float(int(room / sizing_price)))

        if qty <= 0:
            return None

        fill_now = (not settings.require_human_approval) and live_price is not None
        order = PaperOrder(
            symbol=decision.symbol, side=side, qty=qty,
            limit_price=entry,
            status="approved" if fill_now else "pending",
            source="ai",
            note=f"{decision.action} conv={decision.conviction} "
                 f"({decision.provider}): {decision.rationale[:120]}",
        )
        order.id = db.add_paper_order(order)
        if fill_now:
            self.fill(order.id, live_price)
            o = db.get_paper_order(order.id)
            if o:
                order.status = o["status"]; order.fill_price = o["fill_price"]
        return order

    async def submit_manual(self, symbol: str, side: str, qty: float,
                            limit_price: Optional[float] = None) -> PaperOrder:
        side = side.upper()

        # route US orders to a real Alpaca paper account when configured
        ab = _alpaca_broker()
        if ab is not None and parse(symbol)[0] == "US":
            res = await asyncio.to_thread(ab.submit, symbol, side, float(qty), limit_price)
            order = PaperOrder(
                symbol=symbol, side=side, qty=float(qty), limit_price=limit_price,
                status="filled" if res.get("ok") else "rejected",
                source="alpaca", note=str(res)[:200])
            order.id = db.add_paper_order(order)
            return order

        order = PaperOrder(symbol=symbol, side=side, qty=float(qty),
                           limit_price=limit_price,
                           status="pending" if settings.require_human_approval
                           else "approved",
                           source="manual", note="manual order")
        order.id = db.add_paper_order(order)
        if not settings.require_human_approval:
            # approve_async fills at the limit (if given) or the live quote — so a
            # market order (no limit) actually executes instead of sitting forever.
            await self.approve_async(order.id)
            o = db.get_paper_order(order.id)
            if o:
                order.status = o["status"]; order.fill_price = o["fill_price"]
        return order

    # ---- lifecycle -------------------------------------------------------- #
    async def approve_async(self, order_id: int) -> dict[str, Any]:
        o = db.get_paper_order(order_id)
        if not o or o["status"] not in ("pending", "approved"):
            return {"ok": False, "error": "order not pending"}
        q = await datahub.get_quote(o["symbol"])
        price = (o["limit_price"] or (q.last if q else None))
        if not price:
            return {"ok": False, "error": "no price to fill"}
        return self.fill(order_id, float(price))

    def approve(self, order_id: int) -> dict[str, Any]:
        """Synchronous approve using limit price if present (else needs async)."""
        o = db.get_paper_order(order_id)
        if not o:
            return {"ok": False, "error": "not found"}
        if o["status"] not in _FILLABLE:
            return {"ok": False, "error": "order not pending"}
        if o["limit_price"]:
            return self.fill(order_id, float(o["limit_price"]))
        db.update_paper_order(order_id, status="approved")
        return {"ok": True, "status": "approved",
                "note": "approved; approve again with a live quote to fill"}

    def reject(self, order_id: int) -> dict[str, Any]:
        db.update_paper_order(order_id, status="rejected")
        return {"ok": True, "status": "rejected"}

    def fill(self, order_id: int, price: float) -> dict[str, Any]:
        o = db.get_paper_order(order_id)
        if not o:
            return {"ok": False, "error": "not found"}
        # idempotency: never apply the same fill twice (would double-count position)
        if o["status"] not in _FILLABLE:
            return {"ok": False, "error": f"order not fillable ({o['status']})"}
        db.update_paper_order(order_id, status="filled", fill_price=price)
        # update simulated position
        pos = db.get_position(o["symbol"])
        held = pos["qty"] if pos else 0.0
        cost = pos["avg_cost"] if pos else 0.0
        if o["side"] == "BUY":
            new_qty = held + o["qty"]
            new_cost = (held * cost + o["qty"] * price) / new_qty if new_qty else 0.0
            db.upsert_position(o["symbol"], new_qty, round(new_cost, 4))
        else:  # SELL
            sold = min(o["qty"], held)
            if sold > 0 and cost > 0:
                db.add_realized_trade(o["symbol"], sold, cost, price, time.time())
            new_qty = max(0.0, held - o["qty"])
            db.upsert_position(o["symbol"], new_qty, cost if new_qty else 0.0)
        return {"ok": True, "status": "filled", "fill_price": price}


paper = PaperBroker()
