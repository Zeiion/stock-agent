"""Unit tests for portfolio analytics (pure, reads temp DB only)."""
from __future__ import annotations

import pytest

from app import analytics, db


def test_empty_portfolio_returns_zeros():
    p = analytics.compute_portfolio({})
    assert p["nav"] == 0.0
    assert p["holdings_value"] == 0.0
    assert p["cost_basis"] == 0.0
    assert p["unrealized"] == 0.0
    assert p["unrealized_pct"] == 0.0
    assert p["positions"] == []
    assert p["exposure"] == {}
    assert p["exposure_pct"] == {}


def test_single_position_unrealized_math():
    db.upsert_position("US:AAPL", 10.0, 100.0)   # cost 1000
    latest = {"US:AAPL": {"last": 120.0, "market": "US", "name": "Apple"}}
    p = analytics.compute_portfolio(latest)

    assert p["holdings_value"] == 1200.0   # 10 * 120
    assert p["cost_basis"] == 1000.0       # 10 * 100
    assert p["unrealized"] == 200.0
    assert p["unrealized_pct"] == 20.0
    # no realized P&L yet -> nav == holdings_value
    assert p["nav"] == 1200.0

    assert len(p["positions"]) == 1
    row = p["positions"][0]
    assert row["symbol"] == "US:AAPL"
    assert row["value"] == 1200.0
    assert row["unrealized"] == 200.0
    assert row["unrealized_pct"] == 20.0


def test_missing_quote_falls_back_to_avg_cost():
    db.upsert_position("US:AAPL", 10.0, 100.0)
    # no entry for the symbol in latest -> last = avg_cost, zero P&L
    p = analytics.compute_portfolio({})
    assert p["holdings_value"] == 1000.0
    assert p["unrealized"] == 0.0
    assert p["unrealized_pct"] == 0.0


def test_null_last_falls_back_to_avg_cost():
    db.upsert_position("US:AAPL", 10.0, 100.0)
    latest = {"US:AAPL": {"last": None, "market": "US"}}
    p = analytics.compute_portfolio(latest)
    assert p["holdings_value"] == 1000.0
    assert p["unrealized"] == 0.0


def test_exposure_by_market():
    db.upsert_position("US:AAPL", 10.0, 100.0)    # 1000 US
    db.upsert_position("HK:00700", 100.0, 30.0)   # 3000 HK
    latest = {
        "US:AAPL": {"last": 100.0, "market": "US"},
        "HK:00700": {"last": 30.0, "market": "HK"},
    }
    p = analytics.compute_portfolio(latest)
    assert p["holdings_value"] == 4000.0
    assert p["exposure"] == {"US": 1000.0, "HK": 3000.0}
    # exposure_pct sums to ~100
    assert p["exposure_pct"]["US"] == pytest.approx(25.0)
    assert p["exposure_pct"]["HK"] == pytest.approx(75.0)
    # positions sorted by value desc -> HK first
    assert p["positions"][0]["symbol"] == "HK:00700"


def test_market_inferred_from_symbol_when_absent():
    db.upsert_position("CN:600519", 1.0, 1000.0)
    latest = {"CN:600519": {"last": 1000.0}}   # no "market" key
    p = analytics.compute_portfolio(latest)
    assert p["exposure"].get("CN") == 1000.0
    assert p["positions"][0]["market"] == "CN"


def test_nav_includes_realized_pnl():
    db.upsert_position("US:AAPL", 10.0, 100.0)
    # bank a realized gain
    db.add_realized_trade("US:MSFT", 5.0, 100.0, 120.0, 0.0)  # +100 pnl
    latest = {"US:AAPL": {"last": 100.0, "market": "US"}}
    p = analytics.compute_portfolio(latest)
    # nav = holdings_value (1000) + realized_pnl (100)
    assert p["realized"]["realized_pnl"] == pytest.approx(100.0)
    assert p["nav"] == pytest.approx(1100.0)
