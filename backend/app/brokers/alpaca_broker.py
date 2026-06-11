"""Alpaca PAPER trading broker adapter (US symbols only).

This routes paper orders to Alpaca's *paper* trading API — a real broker paper
account — for US equities. It is **OFF by default** and only becomes active when
BOTH ``alpaca_api_key`` and ``alpaca_api_secret`` are configured AND the optional
``alpaca-py`` package is installed.

Design contract:
  - The module imports cleanly even without ``alpaca-py`` installed: every import
    of the SDK is *lazy* (inside the methods that use it).
  - ``available()`` is the single gate the rest of the app should check.
  - No method ever raises; on any failure they return an error dict / empty
    collection so the app degrades gracefully and never breaks.

Symbols are canonical ("US:AAPL"); we map to/from Alpaca's bare ticker via
``app.symbols.parse``. Only the US market is supported.
"""
from __future__ import annotations

import importlib.util
from typing import Any, Optional

from ..config import settings
from .. import symbols


class AlpacaBroker:
    """Submit paper orders / read positions from an Alpaca paper account.

    Construct with explicit keys, or leave blank to read them from settings.
    """

    def __init__(self, api_key: str = "", api_secret: str = "") -> None:
        # Fall back to settings (read defensively in case the fields are absent).
        self.api_key = api_key or getattr(settings, "alpaca_api_key", "") or ""
        self.api_secret = (
            api_secret or getattr(settings, "alpaca_api_secret", "") or ""
        )

    # ------------------------------------------------------------------ #
    # Availability gate
    # ------------------------------------------------------------------ #
    def available(self) -> bool:
        """True only if both keys are set AND ``alpaca-py`` is importable."""
        if not (self.api_key and self.api_secret):
            return False
        try:
            return importlib.util.find_spec("alpaca") is not None
        except Exception:
            return False

    # ------------------------------------------------------------------ #
    # Internal: build a paper TradingClient (lazy SDK import)
    # ------------------------------------------------------------------ #
    def _client(self):
        from alpaca.trading.client import TradingClient

        return TradingClient(self.api_key, self.api_secret, paper=True)

    @staticmethod
    def _to_alpaca(symbol: str) -> Optional[str]:
        """Canonical "US:AAPL" -> "AAPL"; return None if not a US symbol."""
        try:
            market, code = symbols.parse(symbol)
        except Exception:
            return None
        if market != "US":
            return None
        return code

    @staticmethod
    def _from_alpaca(ticker: str) -> str:
        """Alpaca ticker -> canonical "US:..."."""
        return f"US:{str(ticker).strip().upper()}"

    @staticmethod
    def _f(value: Any) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return 0.0

    # ------------------------------------------------------------------ #
    # Submit an order
    # ------------------------------------------------------------------ #
    def submit(
        self,
        symbol: str,
        side: str,
        qty: float,
        limit_price: Optional[float] = None,
    ) -> dict:
        """Submit a market or limit DAY order to the paper account.

        ``symbol`` is canonical ("US:AAPL"); only US is supported.
        Returns {"ok": True, "broker": "alpaca", "order_id", "status"} on success,
        or {"ok": False, "error": ...} on any failure.
        """
        if not self.available():
            return {"ok": False, "error": "alpaca: not configured"}

        ticker = self._to_alpaca(symbol)
        if ticker is None:
            return {"ok": False, "error": "alpaca: US only"}

        try:
            from alpaca.trading.requests import (
                LimitOrderRequest,
                MarketOrderRequest,
            )
            from alpaca.trading.enums import OrderSide, TimeInForce

            order_side = (
                OrderSide.BUY
                if str(side).strip().upper() == "BUY"
                else OrderSide.SELL
            )

            if limit_price is not None:
                req = LimitOrderRequest(
                    symbol=ticker,
                    qty=float(qty),
                    side=order_side,
                    time_in_force=TimeInForce.DAY,
                    limit_price=float(limit_price),
                )
            else:
                req = MarketOrderRequest(
                    symbol=ticker,
                    qty=float(qty),
                    side=order_side,
                    time_in_force=TimeInForce.DAY,
                )

            order = self._client().submit_order(req)
            return {
                "ok": True,
                "broker": "alpaca",
                "order_id": str(getattr(order, "id", "")),
                "status": str(getattr(order, "status", "")),
            }
        except Exception as e:  # noqa: BLE001 — degrade gracefully, never raise
            return {"ok": False, "error": str(e)}

    # ------------------------------------------------------------------ #
    # Positions
    # ------------------------------------------------------------------ #
    def positions(self) -> list:
        """Return open positions as canonical dicts; [] on any error.

        Each entry: {symbol, qty, avg_cost, last, pnl}.
        """
        if not self.available():
            return []
        try:
            out: list[dict[str, Any]] = []
            for p in self._client().get_all_positions():
                out.append(
                    {
                        "symbol": self._from_alpaca(getattr(p, "symbol", "")),
                        "qty": self._f(getattr(p, "qty", 0)),
                        "avg_cost": self._f(getattr(p, "avg_entry_price", 0)),
                        "last": self._f(getattr(p, "current_price", 0)),
                        "pnl": self._f(getattr(p, "unrealized_pl", 0)),
                    }
                )
            return out
        except Exception:  # noqa: BLE001
            return []

    # ------------------------------------------------------------------ #
    # Account
    # ------------------------------------------------------------------ #
    def account(self) -> dict:
        """Return {equity, cash, buying_power}; {} on any error."""
        if not self.available():
            return {}
        try:
            acct = self._client().get_account()
            return {
                "equity": self._f(getattr(acct, "equity", 0)),
                "cash": self._f(getattr(acct, "cash", 0)),
                "buying_power": self._f(getattr(acct, "buying_power", 0)),
            }
        except Exception:  # noqa: BLE001
            return {}
