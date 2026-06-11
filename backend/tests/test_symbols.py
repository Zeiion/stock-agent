"""Pure unit tests for canonical symbol parsing / vendor mapping."""
from __future__ import annotations

import pytest

from app import symbols


# --------------------------------------------------------------------------- #
# guess_market: digits -> CN/HK, letters -> US
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "code, market, out_code",
    [
        ("600519", "CN", "600519"),   # 6-digit -> CN
        ("000001", "CN", "000001"),   # 6-digit (leading zeros) -> CN
        ("00700", "HK", "00700"),     # 5-digit -> HK
        ("0700", "HK", "00700"),      # 4-digit padded to 5 -> HK
        ("700", "HK", "00700"),       # 3-digit padded to 5 -> HK
        ("5", "HK", "00005"),         # 1-digit -> HK
        ("AAPL", "US", "AAPL"),       # letters -> US
        ("aapl", "US", "AAPL"),       # lowercased letters -> US (uppercased)
    ],
)
def test_guess_market(code, market, out_code):
    assert symbols.guess_market(code) == (market, out_code)


# --------------------------------------------------------------------------- #
# parse: explicit MARKET:CODE and bare best-effort
# --------------------------------------------------------------------------- #
def test_parse_explicit():
    assert symbols.parse("US:AAPL") == ("US", "AAPL")
    assert symbols.parse("HK:700") == ("HK", "00700")     # normalized to 5-digit
    assert symbols.parse("CN:1") == ("CN", "000001")      # normalized to 6-digit
    assert symbols.parse("us:aapl") == ("US", "AAPL")     # case-insensitive market


def test_parse_bare_falls_back_to_guess():
    assert symbols.parse("00700") == ("HK", "00700")
    assert symbols.parse("600519") == ("CN", "600519")
    assert symbols.parse("TSLA") == ("US", "TSLA")


def test_parse_rejects_unknown_market():
    with pytest.raises(ValueError):
        symbols.parse("XX:123")


# --------------------------------------------------------------------------- #
# canonical
# --------------------------------------------------------------------------- #
def test_canonical():
    assert symbols.canonical("aapl") == "US:AAPL"
    assert symbols.canonical("700") == "HK:00700"
    assert symbols.canonical("600519") == "CN:600519"
    assert symbols.canonical("HK:0700") == "HK:00700"


# --------------------------------------------------------------------------- #
# cn_exchange
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "code, exchange",
    [
        ("600519", "SH"),   # 6xx main board Shanghai
        ("688981", "SH"),   # 688 STAR market -> Shanghai
        ("000001", "SZ"),   # 000 main board Shenzhen
        ("300750", "SZ"),   # 300 ChiNext -> Shenzhen
        ("002594", "SZ"),   # 002 SME -> Shenzhen
        ("830799", "BJ"),   # 8xx Beijing exchange
        ("430047", "BJ"),   # 4xx -> Beijing
    ],
)
def test_cn_exchange(code, exchange):
    assert symbols.cn_exchange(code) == exchange


# --------------------------------------------------------------------------- #
# to_yfinance: the canonical -> yahoo ticker mapping
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "canon, yf",
    [
        ("US:AAPL", "AAPL"),          # US passthrough
        ("HK:00700", "0700.HK"),      # 5-digit padded -> 4-digit + .HK
        ("HK:0700", "0700.HK"),       # already 4-digit input still maps right
        ("CN:600519", "600519.SS"),   # Shanghai -> .SS
        ("CN:000001", "000001.SZ"),   # Shenzhen -> .SZ
        ("CN:830799", "830799.BJ"),   # Beijing -> .BJ
    ],
)
def test_to_yfinance(canon, yf):
    assert symbols.to_yfinance(canon) == yf


# --------------------------------------------------------------------------- #
# from_yfinance round-trip
# --------------------------------------------------------------------------- #
def test_from_yfinance():
    assert symbols.from_yfinance("AAPL") == "US:AAPL"
    assert symbols.from_yfinance("0700.HK") == "HK:00700"
    assert symbols.from_yfinance("600519.SS") == "CN:600519"
    assert symbols.from_yfinance("000001.SZ") == "CN:000001"


def test_yfinance_round_trip():
    for canon in ("US:AAPL", "HK:00700", "CN:600519", "CN:000001"):
        assert symbols.from_yfinance(symbols.to_yfinance(canon)) == canon


# --------------------------------------------------------------------------- #
# akshare mappings + market guards
# --------------------------------------------------------------------------- #
def test_akshare_cn():
    assert symbols.to_akshare_cn("CN:600519") == "600519"
    assert symbols.to_akshare_cn_prefixed("CN:600519") == "sh600519"
    assert symbols.to_akshare_cn_prefixed("CN:000001") == "sz000001"
    with pytest.raises(ValueError):
        symbols.to_akshare_cn("US:AAPL")


def test_akshare_hk():
    assert symbols.to_akshare_hk("HK:00700") == "00700"
    with pytest.raises(ValueError):
        symbols.to_akshare_hk("CN:600519")
