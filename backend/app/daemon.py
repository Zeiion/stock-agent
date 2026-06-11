"""MonitorDaemon — the always-on core.

Loop:
  1. fetch quotes for the watchlist (via DataHub, market-aware throttling)
  2. compute indicators from cached history
  3. publish quotes to the SSE bus (browser ticker updates)
  4. evaluate alert rules; on a fresh trigger (past cooldown) persist + notify
  5. on critical alerts (stop/target) optionally run an AI decision
Also runs scheduled routines (open/close/EOD digest) via APScheduler if present,
falling back to a simple in-loop timer otherwise.
"""
from __future__ import annotations

import asyncio
import time
import traceback
from datetime import datetime
from typing import Any, Optional
from zoneinfo import ZoneInfo

import pandas as pd

from . import db
from .bus import bus
from .config import settings
from .datahub import datahub, market_is_open, any_market_open
from .indicators import compute_indicators
from .models import Alert, Decision, Quote
from .symbols import parse


class MonitorDaemon:
    def __init__(self) -> None:
        self.latest: dict[str, dict[str, Any]] = {}      # symbol -> quote dict
        self.indicators: dict[str, dict[str, Any]] = {}  # symbol -> snapshot
        self._task: Optional[asyncio.Task] = None
        self._sched_task: Optional[asyncio.Task] = None
        self._bg: set[asyncio.Task] = set()              # tracked fire-and-forget tasks
        self._ind_refresh: dict[str, float] = {}         # symbol -> last refresh ts
        self.ind_refresh_s = 30.0                        # throttle indicator recompute
        self._running = False
        self.last_poll_ts = 0.0
        self.last_error = ""
        # lazy singletons (leaf modules)
        from .rules import RulesEngine
        from .notify.notifier import notifier
        from .ai.brain import brain
        from .paper import paper
        self.rules = RulesEngine()
        self.notifier = notifier
        self.brain = brain
        self.paper = paper
        self._rt = None                                  # FinnhubRealtime (lazy)
        self._rt_syms: list[str] = []

    # ---- background task plumbing ---------------------------------------- #
    def _spawn_bg(self, coro) -> None:
        """Fire-and-forget a coroutine while keeping a strong reference (so it is
        not GC'd mid-flight) and logging any exception instead of swallowing it."""
        async def _runner():
            try:
                await coro
            except Exception:
                traceback.print_exc()
        t = asyncio.create_task(_runner())
        self._bg.add(t)
        t.add_done_callback(self._bg.discard)

    async def _safe_analyze(self, symbol: str, trigger: dict) -> None:
        try:
            await self.analyze(symbol, trigger=trigger)
        except Exception as e:
            traceback.print_exc()
            bus.publish("ai_status", {"symbol": symbol, "state": "error",
                                      "error": str(e)})

    # ---- lifecycle -------------------------------------------------------- #
    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        db.init_db()
        self._task = asyncio.create_task(self._run(), name="monitor-loop")
        self._sched_task = asyncio.create_task(self._scheduler(), name="routines")
        await self._start_realtime()
        bus.publish("status", {"running": True})
        print("[daemon] started")

    async def stop(self) -> None:
        self._running = False
        for t in (self._task, self._sched_task):
            if t:
                t.cancel()
        if self._rt:
            try:
                await self._rt.stop()
            except Exception:
                pass
        bus.publish("status", {"running": False})
        print("[daemon] stopped")

    # ---- realtime (Finnhub WS for US) ------------------------------------- #
    async def _start_realtime(self) -> None:
        if not (settings.realtime_enabled and settings.finnhub_api_key):
            return
        try:
            from .realtime import FinnhubRealtime
            self._rt = FinnhubRealtime(settings.finnhub_api_key, self._on_tick)
            self._rt_syms = db.watch_symbols()
            await self._rt.start(self._rt_syms)
            print("[daemon] realtime WS started")
        except Exception as e:
            print(f"[daemon] realtime start failed: {e}")

    def _on_tick(self, symbol: str, price: float, ts: float, volume: float) -> None:
        """Finnhub WS trade tick -> update the live quote cache + push to UI."""
        qd = self.latest.get(symbol)
        if qd is None:
            qd = {"symbol": symbol, "market": "US", "last": price,
                  "prev_close": price, "name": "", "currency": "USD",
                  "source": "finnhub-ws", "delayed": False}
        else:
            qd = dict(qd)
        prev = qd.get("prev_close") or price
        qd["last"] = price
        qd["ts"] = ts
        qd["source"] = "finnhub-ws"
        qd["delayed"] = False
        qd["change"] = round(price - prev, 4)
        qd["change_pct"] = round((price - prev) / prev * 100, 3) if prev else 0.0
        self.latest[symbol] = qd
        bus.publish("quote", qd)

    async def _sync_realtime(self) -> None:
        if not self._rt:
            return
        syms = db.watch_symbols()
        if syms != self._rt_syms:
            self._rt_syms = syms
            try:
                await self._rt.set_symbols(syms)
            except Exception as e:
                print(f"[daemon] realtime resync failed: {e}")

    def status(self) -> dict[str, Any]:
        return {
            "running": self._running,
            "last_poll_ts": self.last_poll_ts,
            "last_error": self.last_error,
            "watch_count": len(db.watch_symbols()),
            "markets_open": {m: market_is_open(m) for m in ("US", "HK", "CN")},
            "ai_provider": self.brain.provider,
            "ai_ensemble": self.brain.ensemble,
            "trading_mode": settings.trading_mode,
            "subscribers": bus.subscriber_count,
        }

    # ---- main loop -------------------------------------------------------- #
    async def _run(self) -> None:
        # warm indicator buffers from history on startup
        await self._warm_indicators()
        while self._running:
            try:
                await self.poll_once()
                self.last_error = ""
            except asyncio.CancelledError:
                break
            except Exception as e:
                self.last_error = f"{e}"
                traceback.print_exc()
            interval = (settings.poll_interval_s if any_market_open()
                        else settings.poll_interval_offhours_s)
            await asyncio.sleep(interval)

    async def _warm_indicators(self) -> None:
        for sym in db.watch_symbols():
            try:
                df = await datahub.get_history(sym, days=settings.history_lookback_days)
                if not df.empty:
                    self.indicators[sym] = compute_indicators(df)
            except Exception:
                pass

    async def poll_once(self) -> None:
        symbols = db.watch_symbols()
        if not symbols:
            return
        await self._sync_realtime()                      # keep WS subscriptions in sync
        quotes = await datahub.get_quotes(symbols)
        self.last_poll_ts = now = time.time()

        for q in quotes:
            qd = q.to_dict()
            self.latest[q.symbol] = qd
            bus.publish("quote", qd)

            # Keep indicators advancing to new bars (else cross-detection both
            # mis-fires and miss-fires). Throttled per-symbol; datahub caches the
            # underlying history ~60s so this refetches over the network rarely.
            if now - self._ind_refresh.get(q.symbol, 0.0) > self.ind_refresh_s:
                self._ind_refresh[q.symbol] = now
                snap = await self.refresh_indicators(q.symbol)
            else:
                snap = self.indicators.get(q.symbol, {})
            ind = snap or self.indicators.get(q.symbol, {})
            pos = db.get_position(q.symbol)
            await self._eval_rules(q, ind, pos)

    async def refresh_indicators(self, symbol: str) -> dict[str, Any]:
        df = await datahub.get_history(symbol, days=settings.history_lookback_days)
        snap = compute_indicators(df) if not df.empty else {}
        if snap:
            self.indicators[symbol] = snap
            bus.publish("indicators", {"symbol": symbol, "indicators": snap})
        return snap

    # ---- rules + alerts --------------------------------------------------- #
    async def _eval_rules(self, q: Quote, ind: dict, pos: Optional[dict]) -> None:
        rules = db.list_rules(symbol=q.symbol, active_only=True)
        if not rules:
            return
        alerts = self.rules.evaluate(q.symbol, q, ind, pos, rules)
        now = time.time()
        for alert in alerts:
            # cooldown check (per rule)
            cd = next((r["cooldown_s"] for r in rules if r["id"] == alert.rule_id), 300)
            if alert.rule_id is not None:
                if now - db.rule_last_fired(alert.rule_id) < cd:
                    continue
                db.mark_rule_fired(alert.rule_id, now)
            alert.id = db.add_alert(alert)
            bus.publish("alert", alert.to_dict())
            try:
                await self.notifier.notify(alert)
            except Exception as e:
                print(f"[daemon] notify failed: {e}")
            # escalate critical alerts to an AI decision (tracked + error-logged)
            if (settings.auto_ai_on_critical and alert.severity == "critical"):
                self._spawn_bg(self._safe_analyze(q.symbol, alert.to_dict()))

    # ---- context building (shared by analyze + deep_analyze) -------------- #
    async def _build_ctx(self, symbol: str, trigger: Optional[dict] = None,
                         with_news: bool = False, strategy: str = "balanced"
                         ) -> tuple[dict, Optional[Quote]]:
        q = self.latest.get(symbol)
        quote_obj: Optional[Quote] = None
        if q is None:
            quote_obj = await datahub.get_quote(symbol)
            q = quote_obj.to_dict() if quote_obj else {}
        ind = self.indicators.get(symbol) or await self.refresh_indicators(symbol)
        df = await datahub.get_history(symbol, days=settings.history_lookback_days)
        recent = []
        if not df.empty:
            tail = df.tail(20).reset_index()
            for _, row in tail.iterrows():
                recent.append({
                    "ts": float(pd.Timestamp(row.iloc[0]).timestamp())
                    if not isinstance(row.iloc[0], (int, float)) else float(row.iloc[0]),
                    "open": float(row["open"]), "high": float(row["high"]),
                    "low": float(row["low"]), "close": float(row["close"]),
                    "volume": float(row["volume"]),
                })
        news = []
        fundamentals: dict = {}
        social: dict = {}
        if with_news:
            from .fundamentals import get_fundamentals
            from .social import get_social
            try:
                from .news import get_news
                news_res, fund_res, soc_res = await asyncio.gather(
                    get_news(symbol, limit=8), get_fundamentals(symbol),
                    get_social(symbol), return_exceptions=True)
                news = news_res if isinstance(news_res, list) else []
                fundamentals = fund_res if isinstance(fund_res, dict) else {}
                social = soc_res if isinstance(soc_res, dict) else {}
            except Exception as e:
                print(f"[daemon] news/fundamentals/social fetch failed: {e}")
        # decision memory: past calls on this symbol + how they played out, so the
        # model can learn from its own track record (TradingAgents-style reflection)
        history = []
        try:
            for d in db.list_decisions(symbol=symbol, limit=5):
                history.append({
                    "ts": d.get("ts"), "action": d.get("action"),
                    "conviction": d.get("conviction"),
                    "strategy": d.get("strategy"),
                    "realized_return_pct": d.get("realized_return"),
                })
        except Exception:
            pass
        ctx = {
            "symbol": symbol, "quote": q, "indicators": ind, "recent": recent,
            "position": db.get_position(symbol), "trigger": trigger, "news": news,
            "fundamentals": fundamentals, "social": social, "strategy": strategy,
            "history": history,
        }
        return ctx, quote_obj

    # ---- AI analysis ------------------------------------------------------ #
    async def analyze(self, symbol: str, trigger: Optional[dict] = None,
                      provider: Optional[str] = None,
                      strategy: str = "balanced") -> Decision:
        symbol = symbol.strip()
        try:
            parse(symbol)
        except Exception:
            raise ValueError(f"bad symbol {symbol}")

        ctx, quote_obj = await self._build_ctx(symbol, trigger, with_news=True,
                                               strategy=strategy)
        bus.publish("ai_status", {"symbol": symbol, "state": "thinking"})
        decision = await self.brain.decide(ctx, provider=provider)
        try:
            decision.id = db.add_decision(decision)
        except Exception as e:
            print(f"[daemon] add_decision failed: {e}")
        bus.publish("decision", decision.to_dict())

        if quote_obj is None:
            quote_obj = await datahub.get_quote(symbol)
        try:
            order = self.paper.from_decision(decision, quote_obj)
            if order:
                bus.publish("order", order.to_dict())
        except Exception as e:
            print(f"[daemon] paper intake failed: {e}")
        return decision

    async def deep_analyze(self, symbol: str, provider: Optional[str] = None,
                           strategy: str = "balanced", debate: bool = False) -> dict:
        """Multi-agent deep analysis: analyst panel -> (optional bull/bear
        debate) -> synthesis -> decision."""
        symbol = symbol.strip()
        try:
            parse(symbol)
        except Exception:
            raise ValueError(f"bad symbol {symbol}")
        ctx, quote_obj = await self._build_ctx(symbol, with_news=True,
                                               strategy=strategy)
        bus.publish("ai_status", {"symbol": symbol, "state": "deep-thinking"})
        from .deepanalysis import deep_analyze as _deep
        result = await _deep(symbol, ctx, provider=provider, debate=debate)
        decision = result.get("decision")
        if decision is not None:
            try:
                decision.id = db.add_decision(decision)
            except Exception as e:
                print(f"[daemon] add_decision (deep) failed: {e}")
            bus.publish("decision", decision.to_dict())
            if quote_obj is None:
                quote_obj = await datahub.get_quote(symbol)
            try:
                order = self.paper.from_decision(decision, quote_obj)
                if order:
                    bus.publish("order", order.to_dict())
            except Exception as e:
                print(f"[daemon] paper intake (deep) failed: {e}")
        bus.publish("ai_status", {"symbol": symbol, "state": "idle"})
        out = {
            "symbol": symbol,
            "analysts": result.get("analysts", []),
            "decision": decision.to_dict() if decision is not None else None,
            "ts": result.get("ts"),
        }
        if result.get("researchers") is not None:
            out["researchers"] = result["researchers"]
        return out

    async def persona_panel(self, symbol: str, personas=None,
                            provider: Optional[str] = None,
                            strategy: str = "balanced") -> dict:
        """Investor-persona panel: legendary-investor agents vote in parallel,
        aggregated into a confidence-weighted consensus (ai-hedge-fund-style)."""
        symbol = symbol.strip()
        try:
            parse(symbol)
        except Exception:
            raise ValueError(f"bad symbol {symbol}")
        ctx, _ = await self._build_ctx(symbol, with_news=True, strategy=strategy)
        bus.publish("ai_status", {"symbol": symbol, "state": "deep-thinking"})
        from .personas import run_panel
        try:
            result = await run_panel(symbol, ctx, personas=personas, provider=provider)
        finally:
            bus.publish("ai_status", {"symbol": symbol, "state": "idle"})
        bus.publish("persona_panel", result)
        return result

    async def day_t(self, symbol: str, ai: bool = False,
                    provider: Optional[str] = None, use_garch: bool = False) -> dict:
        """做T 当日高低点预测 + 高抛低吸挂单建议（可选 AI 经验分析）。"""
        symbol = symbol.strip()
        try:
            parse(symbol)
        except Exception:
            raise ValueError(f"bad symbol {symbol}")
        from . import intraday
        df = await datahub.get_history(symbol, days=settings.history_lookback_days)
        q = self.latest.get(symbol)
        if q is None:
            quote_obj = await datahub.get_quote(symbol)
            q = quote_obj.to_dict() if quote_obj else {}
        today_open = q.get("open")
        last = q.get("last")
        position = db.get_position(symbol)
        result = intraday.day_t_plan(symbol, df, position, today_open, last,
                                     use_garch=use_garch)
        if ai:
            candles = []
            if df is not None and not df.empty:
                for _, row in df.tail(20).reset_index().iterrows():
                    candles.append({
                        "o": float(row["open"]), "h": float(row["high"]),
                        "l": float(row["low"]), "c": float(row["close"]),
                        "v": float(row["volume"])})
            commentary = await intraday.ai_commentary(symbol, result, candles, provider)
            if commentary is not None:
                result["ai"] = commentary
        return result

    # ---- batch (concurrent) AI analysis --------------------------------- #
    async def analyze_batch(self, symbols: list[str], provider: Optional[str] = None,
                            strategy: str = "balanced", concurrency: int = 3) -> list:
        """Analyze many symbols concurrently; push a notification when done."""
        symbols = [s for s in symbols if s]
        if not symbols:
            return []
        sem = asyncio.Semaphore(max(1, concurrency))
        total = len(symbols)
        done = 0
        bus.publish("batch_status", {"state": "running", "total": total, "done": 0})

        async def one(s: str):
            nonlocal done
            async with sem:
                try:
                    d = await self.analyze(s, provider=provider, strategy=strategy)
                except Exception as e:
                    print(f"[daemon] batch analyze {s} failed: {e}")
                    d = None
                done += 1
                bus.publish("batch_status", {"state": "running", "total": total,
                                             "done": done,
                                             "symbol": s,
                                             "action": d.action if d else "ERR"})
                return d

        decisions = [d for d in await asyncio.gather(*[one(s) for s in symbols]) if d]

        counts: dict[str, int] = {}
        for d in decisions:
            counts[d.action] = counts.get(d.action, 0) + 1
        top = sorted(decisions, key=lambda d: -d.conviction)[:3]
        parts = " ".join(f"{a}×{c}" for a, c in counts.items())
        body = f"完成 {len(decisions)}/{total} 只 · {parts}"
        if top:
            body += " · 高信念: " + ", ".join(
                f"{d.symbol.split(':')[-1]} {d.action}({d.conviction})" for d in top)
        bus.publish("batch_status", {"state": "done", "total": total,
                                     "done": len(decisions), "summary": body})
        try:
            await self.notifier.send_text("🧠 批量分析完成", body, "normal")
        except Exception as e:
            print(f"[daemon] batch notify failed: {e}")
        return decisions

    # ---- AI watchlist briefing ------------------------------------------- #
    async def generate_briefing(self) -> dict:
        """One-call AI briefing over the whole watchlist; stored + pushed."""
        from .briefing import generate
        syms = db.watch_symbols()
        items = []
        for s in syms:
            qd = self.latest.get(s) or {}
            ind = self.indicators.get(s) or {}
            items.append({
                "symbol": s, "market": qd.get("market", ""),
                "name": qd.get("name", ""), "last": qd.get("last"),
                "change_pct": qd.get("change_pct"),
                "tags": ind.get("tags", []), "sentiment": "-", "news_titles": [],
            })
        result = await generate(items)
        try:
            db.kv_set("latest_briefing", result)
        except Exception as e:
            print(f"[daemon] store briefing failed: {e}")
        bus.publish("briefing", result)
        try:
            await self.notifier.send_text("📋 盘前简报", result.get("summary", ""), "info")
        except Exception:
            pass
        return result

    # ---- scheduled routines ---------------------------------------------- #
    async def _scheduler(self) -> None:
        """Fire a daily watchlist digest shortly after US close (16:00 ET).
        Deduped via a DB-persisted marker so a same-day restart can't double-send,
        and driven by the market timezone rather than arbitrary server-local time."""
        et = ZoneInfo("America/New_York")
        while self._running:
            try:
                now_et = datetime.fromtimestamp(time.time(), tz=et)
                day = now_et.strftime("%Y-%m-%d")
                if now_et.hour == 16 and db.kv_get("last_digest_day") != day:
                    db.kv_set("last_digest_day", day)
                    self._spawn_bg(self._daily_digest())
                # AI briefings at configured local HH:MM times
                lt = time.localtime()
                hhmm = f"{lt.tm_hour:02d}:{lt.tm_min:02d}"
                day_local = time.strftime("%Y-%m-%d", lt)
                for slot in [s.strip() for s in settings.briefing_times.split(",")
                             if s.strip()]:
                    if hhmm == slot and db.kv_get("last_briefing_slot") != f"{day_local}:{slot}":
                        db.kv_set("last_briefing_slot", f"{day_local}:{slot}")
                        self._spawn_bg(self.generate_briefing())
                # equity-curve point once per minute when holding positions
                if db.list_positions():
                    try:
                        from .analytics import snapshot_nav
                        pt = snapshot_nav(self.latest)
                        bus.publish("nav", pt)
                    except Exception as e:
                        print(f"[daemon] nav snapshot failed: {e}")
            except asyncio.CancelledError:
                break
            except Exception:
                traceback.print_exc()
            await asyncio.sleep(60)

    async def _daily_digest(self) -> None:
        syms = db.watch_symbols()
        lines = []
        for s in syms:
            qd = self.latest.get(s)
            if qd:
                lines.append(f"{s}: {qd['last']} ({qd['change_pct']:+.2f}%)")
        body = "\n".join(lines) or "No quotes captured today."
        try:
            await self.notifier.send_text("📊 Daily watchlist digest", body, "info")
        except Exception as e:
            print(f"[daemon] digest failed: {e}")


daemon = MonitorDaemon()
