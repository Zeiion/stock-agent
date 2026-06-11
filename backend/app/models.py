"""Canonical data models shared across the whole backend.

Every market adapter normalizes into these types, so the rules engine, AI brain,
notifier, and frontend all speak one vocabulary regardless of data source.

Canonical symbol scheme:  "MARKET:CODE"  e.g. "US:AAPL", "HK:00700", "CN:600519".
See app.symbols for parsing / vendor mapping.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Any, Optional


# --------------------------------------------------------------------------- #
# Enums
# --------------------------------------------------------------------------- #
class Market(str, Enum):
    US = "US"
    HK = "HK"
    CN = "CN"


class Action(str, Enum):
    BUY = "BUY"
    SELL = "SELL"
    HOLD = "HOLD"
    REDUCE = "REDUCE"
    ADD = "ADD"


class Severity(str, Enum):
    INFO = "info"
    NORMAL = "normal"
    CRITICAL = "critical"


class RuleType(str, Enum):
    PRICE_ABOVE = "price_above"
    PRICE_BELOW = "price_below"
    PCT_MOVE = "pct_move"            # abs intraday move vs prev_close >= params.pct
    RSI_ABOVE = "rsi_above"
    RSI_BELOW = "rsi_below"
    MA_CROSS = "ma_cross"            # golden/death cross of fast/slow SMA
    MACD_CROSS = "macd_cross"        # MACD line crosses signal line
    KDJ_CROSS = "kdj_cross"          # J crosses 80 (down-warn) / 20 (up-warn)
    VOLUME_SPIKE = "volume_spike"    # vol vs N-day avg >= params.mult
    STOP_LOSS = "stop_loss"          # held position: price <= params.price
    TAKE_PROFIT = "take_profit"      # held position: price >= params.price


CURRENCY_BY_MARKET = {"US": "USD", "HK": "HKD", "CN": "CNY"}


# --------------------------------------------------------------------------- #
# Quote — the heartbeat record every adapter emits
# --------------------------------------------------------------------------- #
@dataclass
class Quote:
    symbol: str                 # canonical "US:AAPL"
    market: str                 # "US" | "HK" | "CN"
    last: float
    prev_close: float
    name: str = ""             # short display name (watchlist)
    long_name: str = ""        # descriptive name (detail header + AI context)
    open: float = 0.0
    high: float = 0.0
    low: float = 0.0
    volume: float = 0.0
    currency: str = ""
    ts: float = field(default_factory=time.time)   # epoch seconds (UTC)
    source: str = ""            # "yfinance" | "akshare" | "finnhub" ...
    delayed: bool = False       # True for yfinance/akshare HK & A snapshots

    @property
    def change(self) -> float:
        return round(self.last - self.prev_close, 4)

    @property
    def change_pct(self) -> float:
        if not self.prev_close:
            return 0.0
        return round((self.last - self.prev_close) / self.prev_close * 100, 3)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["change"] = self.change
        d["change_pct"] = self.change_pct
        if not d.get("currency"):
            d["currency"] = CURRENCY_BY_MARKET.get(self.market, "")
        return d


@dataclass
class Candle:
    ts: float          # epoch seconds of bar open (UTC)
    open: float
    high: float
    low: float
    close: float
    volume: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# --------------------------------------------------------------------------- #
# Rules / alerts
# --------------------------------------------------------------------------- #
@dataclass
class Rule:
    id: Optional[int]
    symbol: str
    type: str                       # RuleType value
    params: dict[str, Any]          # e.g. {"price": 200} or {"fast":5,"slow":20}
    severity: str = Severity.NORMAL.value
    cooldown_s: int = 300
    active: bool = True
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Alert:
    symbol: str
    rule_id: Optional[int]
    rule_type: str
    severity: str
    message: str
    snapshot: dict[str, Any]        # quote + indicator context at fire time
    ts: float = field(default_factory=time.time)
    id: Optional[int] = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# --------------------------------------------------------------------------- #
# AI decision
# --------------------------------------------------------------------------- #
@dataclass
class Decision:
    symbol: str
    action: str                     # Action value
    conviction: int                 # 1..5
    horizon: str                    # intraday | swing | position
    rationale: str
    key_risks: list[str] = field(default_factory=list)
    entry_zone: Optional[list[float]] = None
    stop_loss: Optional[float] = None
    take_profit: Optional[list[float]] = None
    data_freshness_ok: bool = True
    strategy: str = "balanced"      # analysis lens (see ai.prompts.ANALYSIS_STRATEGIES)
    provider: str = ""              # "claude" | "codex" | "anthropic" | "fallback"
    model: str = ""
    ensemble: Optional[dict[str, Any]] = None   # second-opinion / agreement info
    snapshot: dict[str, Any] = field(default_factory=dict)
    ts: float = field(default_factory=time.time)
    id: Optional[int] = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# --------------------------------------------------------------------------- #
# Positions & paper trading
# --------------------------------------------------------------------------- #
@dataclass
class Position:
    symbol: str
    qty: float
    avg_cost: float
    id: Optional[int] = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class PaperOrder:
    symbol: str
    side: str                       # BUY | SELL
    qty: float
    limit_price: Optional[float]
    status: str = "pending"         # pending | approved | filled | rejected | cancelled
    fill_price: Optional[float] = None
    source: str = "ai"              # ai | manual | rule
    note: str = ""
    ts: float = field(default_factory=time.time)
    id: Optional[int] = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
