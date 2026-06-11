"""Canonical symbol parsing and per-vendor mapping.

Canonical form: "MARKET:CODE"
    US  -> "US:AAPL"          vendor code "AAPL"
    HK  -> "HK:00700"         5-digit, zero-padded
    CN  -> "CN:600519"        6-digit Shanghai/Shenzhen/Beijing

Helpers convert canonical <-> the format each data vendor expects.
"""
from __future__ import annotations

import re
from typing import Tuple

VALID_MARKETS = {"US", "HK", "CN"}


def parse(symbol: str) -> Tuple[str, str]:
    """Return (market, code) from a canonical "MARKET:CODE" symbol.

    Also tolerates a bare code with a best-effort guess (useful for user input):
      "AAPL" -> US, "00700"/"0700" -> HK, "600519" -> CN.
    """
    s = symbol.strip().upper()
    if ":" in s:
        market, code = s.split(":", 1)
        market = market.strip()
        code = code.strip()
        if market not in VALID_MARKETS:
            raise ValueError(f"unknown market in symbol: {symbol!r}")
        return market, _norm_code(market, code)
    return guess_market(s)


def guess_market(code: str) -> Tuple[str, str]:
    c = code.strip().upper()
    if re.fullmatch(r"\d{6}", c):                        # 600519 / 000001 -> CN
        return "CN", _norm_code("CN", c)
    if re.fullmatch(r"\d{1,5}", c):                      # 5 / 700 / 0700 / 00700 -> HK
        return "HK", _norm_code("HK", c)                 # (US tickers are alphabetic)
    return "US", c                                       # letters -> US


def _norm_code(market: str, code: str) -> str:
    if market == "HK":
        digits = re.sub(r"\D", "", code)
        return digits.zfill(5)                           # HK uses 5-digit padded
    if market == "CN":
        return re.sub(r"\D", "", code).zfill(6)
    return code.upper()


def canonical(symbol: str) -> str:
    market, code = parse(symbol)
    return f"{market}:{code}"


# --------------------------------------------------------------------------- #
# China exchange inference (for .SS/.SZ/.BJ suffixes)
# --------------------------------------------------------------------------- #
def cn_exchange(code: str) -> str:
    """Return 'SH' | 'SZ' | 'BJ' for a 6-digit A-share code."""
    c = code.zfill(6)
    if c[0] == "6":                       # 600/601/603/605 main, 688 STAR
        return "SH"
    if c[0] in ("0", "3"):                # 000/001/002 main, 300/301 ChiNext
        return "SZ"
    if c[0] in ("4", "8", "9"):           # 北交所 / 新三板
        return "BJ"
    return "SH"


# --------------------------------------------------------------------------- #
# yfinance mapping (covers all three markets)
# --------------------------------------------------------------------------- #
def to_yfinance(symbol: str) -> str:
    market, code = parse(symbol)
    if market == "US":
        return code
    if market == "HK":
        # yfinance wants 4-digit + .HK (e.g. 0700.HK); strip a leading pad zero
        return f"{int(code):04d}.HK"
    if market == "CN":
        ex = cn_exchange(code)
        suffix = {"SH": "SS", "SZ": "SZ", "BJ": "BJ"}[ex]
        return f"{code}.{suffix}"
    raise ValueError(market)


def from_yfinance(ticker: str) -> str:
    t = ticker.strip().upper()
    if t.endswith(".HK"):
        return f"HK:{_norm_code('HK', t[:-3])}"
    if t.endswith(".SS") or t.endswith(".SZ") or t.endswith(".BJ"):
        return f"CN:{t[:-3]}"
    return f"US:{t}"


# --------------------------------------------------------------------------- #
# akshare mapping
# --------------------------------------------------------------------------- #
def to_akshare_cn(symbol: str) -> str:
    """A-share bare 6-digit code for akshare stock_zh_a_* functions."""
    market, code = parse(symbol)
    if market != "CN":
        raise ValueError(f"{symbol} is not a CN symbol")
    return code


def to_akshare_cn_prefixed(symbol: str) -> str:
    """Prefixed form sh600519 / sz000001 used by some akshare functions."""
    market, code = parse(symbol)
    if market != "CN":
        raise ValueError(f"{symbol} is not a CN symbol")
    ex = cn_exchange(code).lower()
    prefix = {"sh": "sh", "sz": "sz", "bj": "bj"}[ex]
    return f"{prefix}{code}"


def to_akshare_hk(symbol: str) -> str:
    """HK 5-digit code for akshare stock_hk_* functions."""
    market, code = parse(symbol)
    if market != "HK":
        raise ValueError(f"{symbol} is not a HK symbol")
    return code
