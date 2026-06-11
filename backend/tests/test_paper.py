"""Unit tests for the paper broker — fill math, idempotency, REDUCE caps.

We avoid approve_async (it calls datahub.get_quote -> network). fill() is the
synchronous core and is tested directly. submit_manual is exercised only in the
require_human_approval=True path, which produces a *pending* order and never
fills (so no network).
"""
from __future__ import annotations

import pytest

from app import db
from app.paper import PaperBroker


@pytest.fixture
def broker(paper_mode):
    # paper_mode sets trading_mode="paper", require_human_approval=True
    return PaperBroker()


# --------------------------------------------------------------------------- #
# submit_manual builds a PENDING order (no fill, no network) under approval mode
# --------------------------------------------------------------------------- #
async def test_submit_manual_builds_pending_order(broker):
    order = await broker.submit_manual("US:TEST", "buy", 10, limit_price=50.0)
    assert order.id is not None
    assert order.symbol == "US:TEST"
    assert order.side == "BUY"          # uppercased
    assert order.qty == 10.0
    assert order.limit_price == 50.0
    assert order.status == "pending"    # awaiting human approval
    assert order.fill_price is None
    # persisted as pending; no position created until a fill
    stored = db.get_paper_order(order.id)
    assert stored["status"] == "pending"
    assert db.get_position("US:TEST") is None


# --------------------------------------------------------------------------- #
# fill() updates position avg_cost correctly across multiple buys
# --------------------------------------------------------------------------- #
def test_fill_buy_updates_avg_cost(broker):
    o1 = _pending_buy(broker, "US:TEST", qty=10, price=100.0)
    res = broker.fill(o1, 100.0)
    assert res["ok"] and res["status"] == "filled"
    pos = db.get_position("US:TEST")
    assert pos["qty"] == 10.0
    assert pos["avg_cost"] == 100.0

    # second buy at 200 -> weighted avg of (10@100 + 10@200) = 150
    o2 = _pending_buy(broker, "US:TEST", qty=10, price=200.0)
    broker.fill(o2, 200.0)
    pos = db.get_position("US:TEST")
    assert pos["qty"] == 20.0
    assert pos["avg_cost"] == 150.0


# --------------------------------------------------------------------------- #
# a SELL records a realized trade and reduces the position
# --------------------------------------------------------------------------- #
def test_sell_records_realized_trade(broker):
    db.upsert_position("US:TEST", 10.0, 100.0)
    sell = _new_order(broker, "US:TEST", "SELL", qty=4, limit=None)
    res = broker.fill(sell, 130.0)
    assert res["ok"]

    pos = db.get_position("US:TEST")
    assert pos["qty"] == 6.0
    assert pos["avg_cost"] == 100.0     # cost basis unchanged on a partial sell

    trades = db.list_realized_trades()
    assert len(trades) == 1
    t = trades[0]
    assert t["symbol"] == "US:TEST"
    assert t["qty"] == 4.0
    assert t["avg_cost"] == 100.0
    assert t["exit_price"] == 130.0
    assert t["pnl"] == pytest.approx((130.0 - 100.0) * 4.0)  # +120


def test_sell_all_clears_position(broker):
    db.upsert_position("US:TEST", 10.0, 100.0)
    sell = _new_order(broker, "US:TEST", "SELL", qty=10, limit=None)
    broker.fill(sell, 90.0)
    assert db.get_position("US:TEST") is None   # qty 0 deletes the row
    trades = db.list_realized_trades()
    assert len(trades) == 1
    assert trades[0]["pnl"] == pytest.approx((90.0 - 100.0) * 10.0)  # -100 loss


# --------------------------------------------------------------------------- #
# fill() is idempotent — a second fill of the same order is rejected
# --------------------------------------------------------------------------- #
def test_fill_is_idempotent(broker):
    o = _pending_buy(broker, "US:TEST", qty=5, price=100.0)
    first = broker.fill(o, 100.0)
    assert first["ok"]
    second = broker.fill(o, 100.0)
    assert second["ok"] is False
    assert "not fillable" in second["error"]

    # position reflects exactly ONE fill, not two
    pos = db.get_position("US:TEST")
    assert pos["qty"] == 5.0


def test_fill_unknown_order(broker):
    res = broker.fill(999999, 100.0)
    assert res["ok"] is False
    assert res["error"] == "not found"


# --------------------------------------------------------------------------- #
# REDUCE / oversized SELL never exceeds what is held
# --------------------------------------------------------------------------- #
def test_sell_never_exceeds_held(broker):
    db.upsert_position("US:TEST", 5.0, 100.0)
    # try to sell 20 although only 5 held
    sell = _new_order(broker, "US:TEST", "SELL", qty=20, limit=None)
    broker.fill(sell, 110.0)

    # position fully closed, no negative qty
    assert db.get_position("US:TEST") is None
    trades = db.list_realized_trades()
    assert len(trades) == 1
    # realized trade is capped to the 5 shares actually held
    assert trades[0]["qty"] == 5.0


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _new_order(broker, symbol, side, qty, limit):
    """Insert a pending paper order directly (bypasses async submit/network)."""
    from app.models import PaperOrder
    o = PaperOrder(symbol=symbol, side=side, qty=float(qty), limit_price=limit,
                   status="pending", source="manual", note="test")
    o.id = db.add_paper_order(o)
    return o.id


def _pending_buy(broker, symbol, qty, price):
    return _new_order(broker, symbol, "BUY", qty, price)
