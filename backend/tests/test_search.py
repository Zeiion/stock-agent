"""Unit tests for the OFFLINE parts of symbol search (_direct, _popular).

search_symbols() fans out to akshare/yfinance over the network and is NOT
tested here. _direct and _popular are pure and instant.
"""
from __future__ import annotations

from app import search


# --------------------------------------------------------------------------- #
# _direct: only UNAMBIGUOUS codes (explicit MARKET:CODE or pure digits)
# --------------------------------------------------------------------------- #
def test_direct_accepts_explicit_market_code():
    d = search._direct("US:AAPL")
    assert d is not None
    assert d["symbol"] == "US:AAPL"
    assert d["market"] == "US"
    assert d["source"] == "direct"


def test_direct_accepts_pure_digit_codes():
    cn = search._direct("600519")
    assert cn is not None and cn["symbol"] == "CN:600519" and cn["market"] == "CN"

    hk = search._direct("700")
    assert hk is not None and hk["symbol"] == "HK:00700" and hk["market"] == "HK"


def test_direct_rejects_names_and_words():
    # English word and Chinese name must NOT produce a direct add (avoids US:APPLE)
    assert search._direct("apple") is None
    assert search._direct("腾讯") is None
    assert search._direct("") is None
    assert search._direct("   ") is None


def test_direct_explicit_even_with_letters():
    # explicit market prefix is allowed even though the code is alphabetic
    d = search._direct("US:TSLA")
    assert d is not None and d["symbol"] == "US:TSLA"


# --------------------------------------------------------------------------- #
# _popular: substring matching over the curated bilingual list (offline)
# --------------------------------------------------------------------------- #
def test_popular_matches_english_name():
    out = search._popular("apple")
    syms = {r["symbol"] for r in out}
    assert "US:AAPL" in syms
    assert all(r["source"] == "popular" for r in out)


def test_popular_matches_chinese_name():
    out = search._popular("腾讯")
    syms = {r["symbol"] for r in out}
    assert "HK:00700" in syms


def test_popular_matches_code_substring():
    out = search._popular("600519")
    syms = {r["symbol"] for r in out}
    assert "CN:600519" in syms


def test_popular_matches_symbol_substring_case_insensitive():
    out = search._popular("nvda")
    syms = {r["symbol"] for r in out}
    assert "US:NVDA" in syms


def test_popular_no_match_returns_empty():
    assert search._popular("zzzzz-no-such-stock") == []


def test_popular_market_tags_are_correct():
    for r in search._popular("a"):  # broad query
        assert r["market"] in ("US", "HK", "CN")
        assert r["market"] == r["symbol"].split(":", 1)[0]
