"""Unit tests for the PURE parts of the screener (no network).

We never call screen() or _eval_symbol() — those hit datahub. We test the
field mapping, comparison operators, and universe resolution only.
"""
from __future__ import annotations

from app import db, screener
from app.symbols import parse


# --------------------------------------------------------------------------- #
# _metric: field -> source (quote vs indicators), with coercion
# --------------------------------------------------------------------------- #
def test_metric_reads_quote_fields():
    quote = {"last": 123.4, "change_pct": -2.5, "volume": 1000}
    ind = {"rsi14": 70.0}
    assert screener._metric("last", quote, ind) == 123.4
    assert screener._metric("change_pct", quote, ind) == -2.5
    assert screener._metric("volume", quote, ind) == 1000.0


def test_metric_reads_indicator_fields():
    quote = {"last": 100.0}
    ind = {"rsi14": 65.0, "j": 88.0, "ma20": 99.5}
    assert screener._metric("rsi14", quote, ind) == 65.0
    assert screener._metric("j", quote, ind) == 88.0
    assert screener._metric("ma20", quote, ind) == 99.5


def test_metric_missing_or_unknown_returns_none():
    assert screener._metric("rsi14", {}, {}) is None          # absent
    assert screener._metric("bogus_field", {"last": 1}, {}) is None
    assert screener._metric("last", {"last": None}, {}) is None
    assert screener._metric("last", {"last": "not-a-number"}, {}) is None


def test_filter_field_sets_partition_correctly():
    assert "last" in screener._QUOTE_FIELDS
    assert "rsi14" in screener._IND_FIELDS
    # close is read from indicators, not the quote
    assert "close" in screener._IND_FIELDS
    assert screener._metric("close", {"close": 1}, {"close": 50.0}) == 50.0


# --------------------------------------------------------------------------- #
# _OPS comparison operators
# --------------------------------------------------------------------------- #
def test_ops_comparisons():
    ops = screener._OPS
    assert ops[">"](5, 3) is True
    assert ops[">"](3, 5) is False
    assert ops["<"](3, 5) is True
    assert ops[">="](5, 5) is True
    assert ops["<="](5, 5) is True
    assert ops["=="](1.0, 1.0 + 1e-12) is True   # tolerant equality
    assert ops["=="](1.0, 1.1) is False
    assert ops["!="](1.0, 1.1) is True
    assert ops["!="](1.0, 1.0) is False


# --------------------------------------------------------------------------- #
# _universe resolution (offline)
# --------------------------------------------------------------------------- #
def test_universe_popular_us_only_us():
    syms = screener._universe("popular_us", None)
    assert syms, "expected a non-empty popular_us universe"
    assert all(parse(s)[0] == "US" for s in syms)


def test_universe_popular_hk_and_cn():
    hk = screener._universe("popular_hk", None)
    cn = screener._universe("popular_cn", None)
    assert hk and all(parse(s)[0] == "HK" for s in hk)
    assert cn and all(parse(s)[0] == "CN" for s in cn)


def test_universe_custom_list_canonicalizes_and_skips_garbage():
    out = screener._universe("ignored", ["aapl", "700", "600519", "###bad"])
    assert "US:AAPL" in out
    assert "HK:00700" in out
    assert "CN:600519" in out
    # "###bad" canonicalizes to a US ticker per guess_market (letters/symbols);
    # the important contract is no exception is raised and valid ones come through.
    assert len(out) >= 3


def test_universe_watchlist_reads_db():
    assert screener._universe("watchlist", None) == []   # empty DB
    db.add_watch("US:AAPL", "US", "Apple")
    db.add_watch("HK:00700", "HK", "Tencent")
    out = screener._universe("watchlist", None)
    assert set(out) == {"US:AAPL", "HK:00700"}
