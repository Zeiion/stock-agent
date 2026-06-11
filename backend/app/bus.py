"""In-process pub/sub event bus.

The MonitorDaemon publishes events (quotes, alerts, decisions, status); the
FastAPI SSE endpoint subscribes and streams them to the browser. One bus,
many subscribers, no external broker.
"""
from __future__ import annotations

import asyncio
import json
import time
from typing import Any, AsyncIterator


class Event:
    __slots__ = ("type", "data", "ts")

    def __init__(self, type: str, data: Any):
        self.type = type
        self.data = data
        self.ts = time.time()

    def sse(self) -> str:
        payload = json.dumps({"type": self.type, "data": self.data, "ts": self.ts},
                             default=str)
        return f"event: {self.type}\ndata: {payload}\n\n"


class EventBus:
    def __init__(self, maxsize: int = 1000):
        self._subs: set[asyncio.Queue] = set()
        self._maxsize = maxsize
        self._lock = asyncio.Lock()

    async def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=self._maxsize)
        async with self._lock:
            self._subs.add(q)
        return q

    async def unsubscribe(self, q: asyncio.Queue) -> None:
        async with self._lock:
            self._subs.discard(q)

    def publish(self, type: str, data: Any) -> None:
        """Fire-and-forget publish; safe to call from sync or async contexts on
        the event loop thread. Drops to the slowest subscriber if its queue is
        full (a stalled browser tab must not back-pressure the daemon)."""
        ev = Event(type, data)
        dead = []
        for q in list(self._subs):
            try:
                q.put_nowait(ev)
            except asyncio.QueueFull:
                # drop oldest, push newest
                try:
                    q.get_nowait()
                    q.put_nowait(ev)
                except Exception:
                    dead.append(q)
        for q in dead:
            self._subs.discard(q)

    @property
    def subscriber_count(self) -> int:
        return len(self._subs)


# Singleton bus used across the app.
bus = EventBus()


async def event_stream(q: asyncio.Queue, keepalive_s: float = 15.0
                       ) -> AsyncIterator[str]:
    """Yield SSE-formatted strings from a subscriber queue with keepalives."""
    try:
        # initial hello so the client knows the stream is live
        yield Event("hello", {"ok": True}).sse()
        while True:
            try:
                ev: Event = await asyncio.wait_for(q.get(), timeout=keepalive_s)
                yield ev.sse()
            except asyncio.TimeoutError:
                yield ": keepalive\n\n"
    finally:
        await bus.unsubscribe(q)
