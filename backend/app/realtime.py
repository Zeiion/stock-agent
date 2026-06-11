"""Finnhub WebSocket realtime trade feed (US symbols only).

Free Finnhub websocket coverage is US equities only, so we subscribe just the
US tickers among the watchlist. Each incoming trade is delivered via the
`on_trade(canonical, price, ts_seconds, volume)` callback; the daemon wires that
callback to update its quote cache and publish on the event bus.

Everything degrades gracefully and is OFF by default:
  - no FINNHUB_API_KEY  -> start() is a no-op, stays not-running
  - `websockets` missing -> logs once and stays not-running
  - any disconnect/error -> exponential backoff reconnect (1s..30s) + re-subscribe

Safe for a single asyncio loop (the daemon's).
"""
from __future__ import annotations

import asyncio
import json
from typing import Callable, Optional

from . import symbols

# on_trade(canonical_symbol, price, ts_seconds, volume) -> None
TradeCallback = Callable[[str, float, float, float], None]

_FINNHUB_WS = "wss://ws.finnhub.io?token={token}"
_BACKOFF_MIN = 1.0
_BACKOFF_MAX = 30.0


class FinnhubRealtime:
    """Manages a single Finnhub websocket connection + subscription set."""

    def __init__(self, api_key: str, on_trade: TradeCallback) -> None:
        self._api_key = (api_key or "").strip()
        self._on_trade = on_trade

        # canonical symbols requested by the caller (any market)
        self._wanted: set[str] = set()
        # US tickers we believe should be subscribed (e.g. {"AAPL", "MSFT"})
        self._desired_tickers: set[str] = set()
        # US tickers currently subscribed on the live socket
        self._subscribed: set[str] = set()
        # ticker -> canonical map ("AAPL" -> "US:AAPL")
        self._ticker_to_canonical: dict[str, str] = {}

        self._ws = None                       # active websocket connection
        self._task: Optional[asyncio.Task] = None
        self._running = False

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #
    @property
    def running(self) -> bool:
        return self._running

    async def start(self, symbols_list) -> None:
        """Idempotent: (re)connect and subscribe the US symbols in the list.

        Never raises. No-op when api_key is empty or websockets is missing."""
        if not self._api_key:
            print("[realtime] FINNHUB_API_KEY not set; realtime feed disabled")
            return
        try:
            import websockets  # noqa: F401  (lazy import; ships with uvicorn[standard])
        except Exception as e:  # ImportError or anything odd
            print(f"[realtime] websockets library unavailable ({e}); "
                  "realtime feed disabled")
            return

        self._compute_desired(symbols_list)

        if self._running and self._task and not self._task.done():
            # already running; just sync the subscription set on the live socket
            await self._sync_subscriptions()
            return

        self._running = True
        self._subscribed.clear()
        self._task = asyncio.create_task(self._run_loop())

    async def set_symbols(self, symbols_list) -> None:
        """Update the subscription set (subscribe new, unsubscribe removed).

        Never raises. If not running yet, this just records the desired set."""
        try:
            self._compute_desired(symbols_list)
            if self._running and self._ws is not None:
                await self._sync_subscriptions()
        except Exception as e:
            print(f"[realtime] set_symbols failed: {e}")

    async def stop(self) -> None:
        """Cancel the reader task and close the socket. Never raises."""
        self._running = False
        task = self._task
        self._task = None
        if task is not None:
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass
        await self._close_ws()
        self._subscribed.clear()

    # ------------------------------------------------------------------ #
    # Internals
    # ------------------------------------------------------------------ #
    def _compute_desired(self, symbols_list) -> None:
        """Recompute the US ticker set + ticker->canonical map from a symbol list."""
        wanted: set[str] = set()
        tickers: set[str] = set()
        mapping: dict[str, str] = {}
        for sym in (symbols_list or []):
            try:
                market, code = symbols.parse(sym)
            except Exception:
                continue
            if market != "US":
                continue
            canonical = f"{market}:{code}"
            wanted.add(canonical)
            ticker = code            # US ticker is the bare code, e.g. "AAPL"
            tickers.add(ticker)
            mapping[ticker] = canonical
        self._wanted = wanted
        self._desired_tickers = tickers
        self._ticker_to_canonical = mapping

    async def _sync_subscriptions(self) -> None:
        """Diff desired vs subscribed on the live socket and send sub/unsub."""
        ws = self._ws
        if ws is None:
            return
        to_add = self._desired_tickers - self._subscribed
        to_remove = self._subscribed - self._desired_tickers
        for ticker in to_add:
            if await self._send(ws, {"type": "subscribe", "symbol": ticker}):
                self._subscribed.add(ticker)
        for ticker in to_remove:
            if await self._send(ws, {"type": "unsubscribe", "symbol": ticker}):
                self._subscribed.discard(ticker)

    async def _send(self, ws, payload: dict) -> bool:
        try:
            await ws.send(json.dumps(payload))
            return True
        except Exception as e:
            print(f"[realtime] send {payload} failed: {e}")
            return False

    async def _close_ws(self) -> None:
        ws = self._ws
        self._ws = None
        if ws is not None:
            try:
                await ws.close()
            except Exception:
                pass

    async def _run_loop(self) -> None:
        """Connect + read messages, reconnecting with exponential backoff."""
        try:
            import websockets
        except Exception as e:
            print(f"[realtime] websockets library unavailable ({e})")
            self._running = False
            return

        url = _FINNHUB_WS.format(token=self._api_key)
        backoff = _BACKOFF_MIN
        while self._running:
            try:
                async with websockets.connect(url) as ws:
                    self._ws = ws
                    self._subscribed.clear()
                    backoff = _BACKOFF_MIN          # reset after a good connect
                    await self._sync_subscriptions()
                    print(f"[realtime] connected; subscribed "
                          f"{sorted(self._subscribed)}")
                    async for message in ws:
                        if not self._running:
                            break
                        self._handle_message(message)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                if not self._running:
                    break
                print(f"[realtime] connection error: {e}; "
                      f"reconnecting in {backoff:.0f}s")
            finally:
                await self._close_ws()

            if not self._running:
                break
            try:
                await asyncio.sleep(backoff)
            except asyncio.CancelledError:
                raise
            backoff = min(backoff * 2, _BACKOFF_MAX)

        self._running = False

    def _handle_message(self, message) -> None:
        """Parse one websocket frame and emit trades through the callback."""
        try:
            data = json.loads(message)
        except Exception:
            return
        if not isinstance(data, dict):
            return
        if data.get("type") != "trade":
            return                              # ignore "ping" and others
        trades = data.get("data") or []
        if not isinstance(trades, list):
            return
        for t in trades:
            if not isinstance(t, dict):
                continue
            ticker = t.get("s")
            canonical = self._ticker_to_canonical.get(ticker)
            if not canonical:
                continue
            try:
                price = float(t.get("p"))
            except (TypeError, ValueError):
                continue
            try:
                ts_seconds = float(t.get("t", 0)) / 1000.0
            except (TypeError, ValueError):
                ts_seconds = 0.0
            try:
                volume = float(t.get("v") or 0)
            except (TypeError, ValueError):
                volume = 0.0
            try:
                self._on_trade(canonical, price, ts_seconds, volume)
            except Exception as e:
                print(f"[realtime] on_trade callback failed for {canonical}: {e}")
