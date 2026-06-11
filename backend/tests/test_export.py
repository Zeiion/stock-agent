import time

import pytest

from app import db
from app.export import export_csv
from app.models import Decision


def test_unknown_kind_raises():
    with pytest.raises(ValueError):
        export_csv("nope")


@pytest.mark.parametrize("kind", ["decisions", "trades", "alerts", "positions", "nav"])
def test_each_kind_returns_csv(kind):
    fn, text = export_csv(kind)
    assert fn.startswith(kind) and fn.endswith(".csv")
    # header row always present even when the table is empty
    assert text.splitlines()[0].count(",") >= 1


def test_decisions_export_has_row():
    d = Decision(symbol="US:AAPL", action="BUY", conviction=4, horizon="swing",
                 rationale="test rationale", key_risks=["r1", "r2"],
                 entry_zone=[100.0, 110.0], stop_loss=95.0, take_profit=[120.0],
                 data_freshness_ok=True, provider="claude", model="m",
                 snapshot={"quote": {"last": 1}}, ts=time.time())
    db.add_decision(d)
    fn, text = export_csv("decisions")
    lines = text.splitlines()
    assert len(lines) >= 2                       # header + the row
    assert "US:AAPL" in text
    assert "100.0|110.0" in text                 # entry_zone joined with '|'
    assert "r1 / r2" in text                     # key_risks joined
    assert "snapshot" not in lines[0]            # heavy field dropped


def test_trades_export_has_row():
    db.add_realized_trade("US:AAPL", 5, 100.0, 110.0, time.time())
    fn, text = export_csv("trades")
    assert "US:AAPL" in text
    assert len(text.splitlines()) >= 2
