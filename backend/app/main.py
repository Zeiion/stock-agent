"""FastAPI application: REST + SSE, serves the built frontend, owns the daemon.

Run:  uvicorn app.main:app --host 127.0.0.1 --port 8848
(or)  python -m app   (see __main__.py)
"""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Optional

from fastapi import Body, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool

from . import db
from .bus import bus, event_stream
from .config import PROJECT_ROOT, settings
from .daemon import daemon
from .datahub import datahub
from .models import Rule, Severity
from .symbols import canonical, parse


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_db()
    from .configstore import apply_overrides
    apply_overrides()                       # restore runtime config from DB
    await daemon.start()
    try:
        yield
    finally:
        await daemon.stop()


app = FastAPI(title="Stock Agent", version="0.1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins + ["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# --------------------------------------------------------------------------- #
# Optional auth gate (no-op unless settings.auth_enabled) — see app.auth
# --------------------------------------------------------------------------- #
_AUTH_OPEN = {"/api/login", "/api/health"}


@app.middleware("http")
async def _auth_gate(request, call_next):
    from .auth import is_enabled, check_request_token
    path = request.url.path
    if (is_enabled() and path.startswith("/api/")
            and request.method != "OPTIONS" and path not in _AUTH_OPEN):
        from fastapi.responses import JSONResponse
        user = check_request_token(request.headers.get("Authorization"),
                                   request.query_params.get("token"))
        if not user:
            return JSONResponse({"detail": "unauthorized"}, status_code=401)
    return await call_next(request)


@app.post("/api/login")
async def login(payload: dict = Body(...)) -> dict[str, Any]:
    from .auth import is_enabled, login as _login
    if not is_enabled():
        return {"ok": True, "token": "", "auth": False}
    tok = _login(payload.get("username", ""), payload.get("password", ""))
    if not tok:
        raise HTTPException(401, "用户名或密码错误")
    return {"ok": True, "token": tok, "auth": True}


# --------------------------------------------------------------------------- #
# Health / status / stream
# --------------------------------------------------------------------------- #
@app.get("/api/health")
async def health() -> dict[str, Any]:
    from .auth import is_enabled
    return {"ok": True, "auth": is_enabled(), "status": daemon.status()}


@app.get("/api/stream")
async def stream() -> StreamingResponse:
    q = await bus.subscribe()
    # prime with a status snapshot
    bus.publish("status", daemon.status())
    return StreamingResponse(
        event_stream(q),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no",
                 "Connection": "keep-alive"},
    )


# --------------------------------------------------------------------------- #
# Watchlist
# --------------------------------------------------------------------------- #
@app.get("/api/watchlist")
async def get_watchlist() -> list[dict[str, Any]]:
    return await run_in_threadpool(db.list_watch)


@app.post("/api/watchlist")
async def add_watchlist(payload: dict = Body(...)) -> dict[str, Any]:
    raw = (payload.get("symbol") or "").strip()
    if not raw:
        raise HTTPException(400, "symbol required")
    name = payload.get("name", "")

    # Only an explicit "MARKET:CODE" or a pure-digit code is unambiguous; resolve
    # anything else (names, Chinese text, plain tickers) through the same search
    # the add-box uses, so e.g. "比亚迪" -> CN:002594 instead of garbage "US:比亚迪".
    bare = raw.split(":")[-1]
    sym: Optional[str] = None
    if (":" in raw) or bare.isdigit():
        try:
            sym = canonical(raw)
            parse(sym)
        except Exception:
            sym = None
    if sym is None:
        from .search import search_symbols
        hits = await search_symbols(raw, limit=5)
        if hits:
            sym = hits[0]["symbol"]
            if not name:
                hit_name = hits[0].get("name", "") or ""
                name = "" if hit_name.startswith("（") else hit_name
        elif raw.isascii() and len(raw) <= 6 and all(c.isalnum() or c in ".-" for c in raw):
            sym = canonical(raw)                 # obscure US ticker search didn't know
        else:
            raise HTTPException(400, f"无法识别标的 {raw!r}：请输入股票代码或从搜索结果中选择")

    try:
        market, _ = parse(sym)
    except Exception as e:
        raise HTTPException(400, f"bad symbol: {e}")
    await run_in_threadpool(db.add_watch, sym, market, name)
    # warm indicators in the background (tracked + error-logged)
    daemon._spawn_bg(daemon.refresh_indicators(sym))
    q = await datahub.get_quote(sym)
    if q:
        daemon.latest[sym] = q.to_dict()
        bus.publish("quote", q.to_dict())
    return {"ok": True, "symbol": sym}


@app.get("/api/search")
async def search(q: str = Query("", description="name or code keyword"),
                 limit: int = Query(12, ge=1, le=30)) -> dict[str, Any]:
    from .search import search_symbols
    return {"query": q, "results": await search_symbols(q, limit)}


@app.delete("/api/watchlist/{symbol:path}")
async def del_watchlist(symbol: str) -> dict[str, Any]:
    sym = canonical(symbol)
    await run_in_threadpool(db.remove_watch, sym)
    daemon.latest.pop(sym, None)
    daemon.indicators.pop(sym, None)
    return {"ok": True}


# --------------------------------------------------------------------------- #
# Quotes / history / indicators
# --------------------------------------------------------------------------- #
@app.get("/api/quotes")
async def quotes() -> dict[str, Any]:
    return {"quotes": daemon.latest}


@app.get("/api/quote/{symbol:path}")
async def quote(symbol: str) -> dict[str, Any]:
    sym = canonical(symbol)
    q = await datahub.get_quote(sym)
    if not q:
        raise HTTPException(404, "no quote")
    daemon.latest[sym] = q.to_dict()
    return q.to_dict()


@app.get("/api/history/{symbol:path}")
async def history(symbol: str, days: int = Query(200, ge=1, le=3000),
                  interval: str = "1d") -> dict[str, Any]:
    sym = canonical(symbol)
    df = await datahub.get_history(sym, days=days, interval=interval)
    if df.empty:
        return {"symbol": sym, "candles": []}
    import pandas as pd
    candles = []
    for idx, row in df.reset_index().iterrows():
        ts_val = row.iloc[0]
        try:
            ts = float(pd.Timestamp(ts_val).timestamp())
        except Exception:
            ts = float(ts_val)
        candles.append({"ts": ts, "open": float(row["open"]),
                        "high": float(row["high"]), "low": float(row["low"]),
                        "close": float(row["close"]), "volume": float(row["volume"])})
    return {"symbol": sym, "candles": candles}


@app.get("/api/indicators/{symbol:path}")
async def indicators(symbol: str) -> dict[str, Any]:
    sym = canonical(symbol)
    snap = daemon.indicators.get(sym) or await daemon.refresh_indicators(sym)
    return {"symbol": sym, "indicators": snap}


# --------------------------------------------------------------------------- #
# Rules
# --------------------------------------------------------------------------- #
@app.get("/api/rules")
async def get_rules(symbol: Optional[str] = None) -> list[dict[str, Any]]:
    sym = canonical(symbol) if symbol else None
    return await run_in_threadpool(db.list_rules, sym, False)


@app.post("/api/rules")
async def add_rule(payload: dict = Body(...)) -> dict[str, Any]:
    try:
        sym = canonical(payload["symbol"])
    except Exception as e:
        raise HTTPException(400, f"bad symbol: {e}")
    rule = Rule(
        id=None, symbol=sym, type=payload["type"],
        params=payload.get("params", {}),
        severity=payload.get("severity", Severity.NORMAL.value),
        cooldown_s=int(payload.get("cooldown_s", 300)),
        active=bool(payload.get("active", True)),
        note=payload.get("note", ""),
    )
    rid = await run_in_threadpool(db.add_rule, rule)
    return {"ok": True, "id": rid}


@app.patch("/api/rules/{rule_id}")
async def patch_rule(rule_id: int, payload: dict = Body(...)) -> dict[str, Any]:
    if "active" in payload:
        await run_in_threadpool(db.set_rule_active, rule_id, bool(payload["active"]))
    return {"ok": True}


@app.delete("/api/rules/{rule_id}")
async def delete_rule(rule_id: int) -> dict[str, Any]:
    await run_in_threadpool(db.delete_rule, rule_id)
    return {"ok": True}


# --------------------------------------------------------------------------- #
# AI decisions
# --------------------------------------------------------------------------- #
@app.get("/api/ai-strategies")
async def ai_strategies() -> dict[str, Any]:
    from .ai.prompts import ANALYSIS_STRATEGIES
    return {"strategies": [
        {"key": k, "label": v["label"], "lens": v["lens"],
         "group": v.get("group", "风格")}
        for k, v in ANALYSIS_STRATEGIES.items()]}


@app.post("/api/analyze/{symbol:path}")
async def analyze(symbol: str, payload: dict = Body(default={})) -> dict[str, Any]:
    sym = canonical(symbol)
    decision = await daemon.analyze(
        sym, provider=payload.get("provider"),
        strategy=payload.get("strategy", "balanced"))
    return decision.to_dict()


@app.post("/api/deep-analyze/{symbol:path}")
async def deep_analyze(symbol: str, payload: dict = Body(default={})) -> dict[str, Any]:
    sym = canonical(symbol)
    return await daemon.deep_analyze(sym, provider=payload.get("provider"),
                                     strategy=payload.get("strategy", "balanced"),
                                     debate=bool(payload.get("debate", False)))


@app.get("/api/personas")
async def list_personas() -> dict[str, Any]:
    from .personas import PERSONA_KEYS, DEFAULT_PANEL
    from .ai.prompts import ANALYSIS_STRATEGIES
    return {
        "personas": [
            {"key": k, "label": ANALYSIS_STRATEGIES[k]["label"],
             "lens": ANALYSIS_STRATEGIES[k]["lens"]}
            for k in PERSONA_KEYS
        ],
        "default": DEFAULT_PANEL,
    }


@app.post("/api/persona-panel/{symbol:path}")
async def persona_panel(symbol: str, payload: dict = Body(default={})) -> dict[str, Any]:
    sym = canonical(symbol)
    return await daemon.persona_panel(
        sym, personas=payload.get("personas"), provider=payload.get("provider"),
        strategy=payload.get("strategy", "balanced"))


@app.post("/api/day-t/{symbol:path}")
async def day_t(symbol: str, payload: dict = Body(default={})) -> dict[str, Any]:
    """做T 当日高低点预测 + 高抛低吸挂单建议（`{ai?, provider?, use_garch?}`）。"""
    sym = canonical(symbol)
    return await daemon.day_t(sym, ai=bool(payload.get("ai", False)),
                              provider=payload.get("provider"),
                              use_garch=bool(payload.get("use_garch", False)))


@app.get("/api/day-t/{symbol:path}")
async def day_t_get(symbol: str, ai: bool = Query(False),
                    use_garch: bool = Query(False)) -> dict[str, Any]:
    sym = canonical(symbol)
    return await daemon.day_t(sym, ai=ai, use_garch=use_garch)


@app.post("/api/analyze-batch")
async def analyze_batch(payload: dict = Body(default={})) -> dict[str, Any]:
    raw = payload.get("symbols") or db.watch_symbols()
    syms = []
    for s in raw:
        try:
            syms.append(canonical(s))
        except Exception:
            continue
    if not syms:
        raise HTTPException(400, "no symbols to analyze")
    daemon._spawn_bg(daemon.analyze_batch(
        syms, provider=payload.get("provider"),
        strategy=payload.get("strategy", "balanced")))
    return {"ok": True, "started": len(syms)}


# --------------------------------------------------------------------------- #
# Accounts (paper trading books)
# --------------------------------------------------------------------------- #
@app.get("/api/accounts")
async def get_accounts() -> dict[str, Any]:
    return {"accounts": await run_in_threadpool(db.list_accounts),
            "current": await run_in_threadpool(db.current_account)}


@app.post("/api/accounts")
async def create_account(payload: dict = Body(...)) -> dict[str, Any]:
    name = (payload.get("name") or "").strip()
    if not name:
        raise HTTPException(400, "name required")
    await run_in_threadpool(db.add_account, name)
    if payload.get("switch"):
        await run_in_threadpool(db.set_current_account, name)
    return {"ok": True, "accounts": await run_in_threadpool(db.list_accounts),
            "current": await run_in_threadpool(db.current_account)}


@app.post("/api/accounts/switch")
async def switch_account(payload: dict = Body(...)) -> dict[str, Any]:
    await run_in_threadpool(db.set_current_account, payload.get("name", "default"))
    return {"ok": True, "current": await run_in_threadpool(db.current_account)}


@app.post("/api/accounts/reset")
async def reset_account(payload: dict = Body(default={})) -> dict[str, Any]:
    await run_in_threadpool(db.reset_account, payload.get("name"))
    return {"ok": True}


@app.delete("/api/accounts/{name}")
async def delete_account(name: str) -> dict[str, Any]:
    await run_in_threadpool(db.delete_account, name)
    return {"ok": True, "accounts": await run_in_threadpool(db.list_accounts),
            "current": await run_in_threadpool(db.current_account)}


@app.get("/api/decisions")
async def decisions(symbol: Optional[str] = None,
                    limit: int = Query(100, le=500)) -> list[dict[str, Any]]:
    sym = canonical(symbol) if symbol else None
    return await run_in_threadpool(db.list_decisions, sym, limit)


@app.get("/api/social/{symbol:path}")
async def social(symbol: str) -> dict[str, Any]:
    from .social import get_social
    return await get_social(canonical(symbol))


@app.get("/api/market")
async def market() -> dict[str, Any]:
    from .market import get_market_overview
    return await get_market_overview()


@app.get("/api/fundamentals/{symbol:path}")
async def fundamentals(symbol: str) -> dict[str, Any]:
    from .fundamentals import get_fundamentals, LABELS
    sym = canonical(symbol)
    return {"symbol": sym, "fundamentals": await get_fundamentals(sym),
            "labels": LABELS}


@app.get("/api/news/{symbol:path}")
async def news(symbol: str, limit: int = Query(10, ge=1, le=30)) -> dict[str, Any]:
    from .news import get_news
    from .sentiment import score_headlines
    sym = canonical(symbol)
    items = await get_news(sym, limit)
    scored = score_headlines(items)
    return {"symbol": sym, "news": scored["items"], "sentiment": scored["aggregate"]}


@app.get("/api/export/{kind}")
async def export(kind: str) -> Response:
    from .export import export_csv
    try:
        filename, text = await run_in_threadpool(export_csv, kind)
    except ValueError:
        raise HTTPException(404, f"unknown export kind: {kind}")
    return Response(
        content=text, media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'})


@app.get("/api/strategies")
async def strategies() -> dict[str, Any]:
    from .backtest import STRATEGIES, DEFAULT_GRID
    return {"strategies": STRATEGIES, "default_grid": DEFAULT_GRID}


@app.post("/api/backtest/optimize")
async def backtest_optimize(payload: dict = Body(...)) -> dict[str, Any]:
    from .backtest import optimize
    sym = canonical(payload["symbol"])
    days = int(payload.get("days", 365))
    df = await datahub.get_history(sym, days=days)
    if df.empty:
        raise HTTPException(400, "no history for optimize")
    return await run_in_threadpool(
        optimize, sym, df, payload.get("strategy", "ma_cross"),
        payload.get("grid"), payload.get("metric", "total_return"))


@app.post("/api/backtest/walk-forward")
async def backtest_walk_forward(payload: dict = Body(...)) -> dict[str, Any]:
    """Out-of-sample (anchored walk-forward) validation — exposes overfitting."""
    from .backtest import walk_forward
    sym = canonical(payload["symbol"])
    days = int(payload.get("days", 730))
    df = await datahub.get_history(sym, days=days)
    if df.empty:
        raise HTTPException(400, "no history for walk-forward")
    return await run_in_threadpool(
        walk_forward, sym, df, payload.get("strategy", "ma_cross"),
        payload.get("grid"), int(payload.get("folds", 4)),
        float(payload.get("train_ratio", 0.5)),
        payload.get("metric", "sharpe"))


@app.get("/api/portfolio")
async def portfolio() -> dict[str, Any]:
    from .analytics import compute_portfolio
    return await run_in_threadpool(compute_portfolio, daemon.latest)


@app.get("/api/portfolio/history")
async def portfolio_history(limit: int = Query(2000, le=5000)) -> dict[str, Any]:
    return {"history": await run_in_threadpool(db.list_nav, limit)}


@app.get("/api/realized")
async def realized(limit: int = Query(200, le=1000)) -> dict[str, Any]:
    return {"trades": await run_in_threadpool(db.list_realized_trades, limit)}


@app.get("/api/track-record")
async def track_record() -> dict[str, Any]:
    from .reflection import track_record as _tr
    return await run_in_threadpool(_tr, daemon.latest)


@app.get("/api/briefing")
async def get_briefing() -> dict[str, Any]:
    return await run_in_threadpool(db.kv_get, "latest_briefing", {
        "summary": "", "movers": [], "opportunities": [], "risks": [],
        "generated_ts": 0})


@app.post("/api/briefing/generate")
async def gen_briefing() -> dict[str, Any]:
    return await daemon.generate_briefing()


@app.get("/api/broker/status")
async def broker_status() -> dict[str, Any]:
    out: dict[str, Any] = {"broker": settings.broker, "alpaca_available": False,
                           "account": {}, "positions": []}
    if settings.broker == "alpaca":
        from .brokers.alpaca_broker import AlpacaBroker
        b = AlpacaBroker(settings.alpaca_api_key, settings.alpaca_api_secret)
        out["alpaca_available"] = b.available()
        if b.available():
            out["account"] = await run_in_threadpool(b.account)
            out["positions"] = await run_in_threadpool(b.positions)
    return out


@app.get("/api/screen/fields")
async def screen_fields() -> dict[str, Any]:
    from .screener import FILTER_FIELDS
    return {"fields": FILTER_FIELDS, "ops": [">", "<", ">=", "<=", "==", "!="]}


@app.post("/api/screen")
async def screen(payload: dict = Body(...)) -> dict[str, Any]:
    from .screener import screen as _screen
    return await _screen(
        universe=payload.get("universe", "watchlist"),
        symbols=payload.get("symbols"),
        filters=payload.get("filters", []),
        limit=int(payload.get("limit", 60)),
    )


@app.get("/api/alerts")
async def alerts(limit: int = Query(100, le=500)) -> list[dict[str, Any]]:
    return await run_in_threadpool(db.list_alerts, limit)


# --------------------------------------------------------------------------- #
# Positions / paper orders
# --------------------------------------------------------------------------- #
@app.get("/api/positions")
async def positions() -> dict[str, Any]:
    pos = await run_in_threadpool(db.list_positions)
    # enrich with live price / pnl
    out = []
    for p in pos:
        qd = daemon.latest.get(p["symbol"])
        last = qd["last"] if qd else None
        pnl = ((last - p["avg_cost"]) * p["qty"]) if last else None
        out.append({**p, "last": last, "pnl": round(pnl, 2) if pnl is not None else None})
    return {"positions": out}


@app.get("/api/orders")
async def orders(status: Optional[str] = None) -> list[dict[str, Any]]:
    return await run_in_threadpool(db.list_paper_orders, status, 200)


@app.post("/api/orders")
async def submit_order(payload: dict = Body(...)) -> dict[str, Any]:
    sym = canonical(payload["symbol"])
    order = await daemon.paper.submit_manual(
        sym, payload["side"], float(payload["qty"]),
        payload.get("limit_price"))
    bus.publish("order", order.to_dict())
    return order.to_dict()


@app.post("/api/orders/{order_id}/approve")
async def approve_order(order_id: int) -> dict[str, Any]:
    res = await daemon.paper.approve_async(order_id)
    o = await run_in_threadpool(db.get_paper_order, order_id)
    if o:
        bus.publish("order", o)
    return res


@app.post("/api/orders/{order_id}/reject")
async def reject_order(order_id: int) -> dict[str, Any]:
    res = await run_in_threadpool(daemon.paper.reject, order_id)
    o = await run_in_threadpool(db.get_paper_order, order_id)
    if o:
        bus.publish("order", o)
    return res


# --------------------------------------------------------------------------- #
# Backtest
# --------------------------------------------------------------------------- #
@app.post("/api/backtest")
async def backtest(payload: dict = Body(...)) -> dict[str, Any]:
    from .backtest import run_backtest
    sym = canonical(payload["symbol"])
    days = int(payload.get("days", 365))
    df = await datahub.get_history(sym, days=days)
    if df.empty:
        raise HTTPException(400, "no history for backtest")
    result = await run_in_threadpool(
        run_backtest, sym, df, payload.get("strategy", "ma_cross"),
        payload.get("params", {}))
    return result


# --------------------------------------------------------------------------- #
# Settings (runtime AI provider switch etc.)
# --------------------------------------------------------------------------- #
@app.get("/api/settings")
async def get_settings() -> dict[str, Any]:
    return {
        "ai_provider": daemon.brain.provider,
        "ai_ensemble": daemon.brain.ensemble,
        "ai_providers_available": settings.ai_providers_available(),
        "trading_mode": settings.trading_mode,
        "require_human_approval": settings.require_human_approval,
        "poll_interval_s": settings.poll_interval_s,
        "notify_channels": daemon.notifier.enabled_channels(),
    }


@app.post("/api/settings")
async def set_settings(payload: dict = Body(...)) -> dict[str, Any]:
    if "ai_provider" in payload:
        daemon.brain.set_provider(payload["ai_provider"])
    if "ai_ensemble" in payload:
        daemon.brain.set_ensemble(bool(payload["ai_ensemble"]))
    if "trading_mode" in payload and payload["trading_mode"] in ("signal", "paper"):
        settings.trading_mode = payload["trading_mode"]
    if "require_human_approval" in payload:
        settings.require_human_approval = bool(payload["require_human_approval"])
    return await get_settings()


@app.get("/api/config")
async def get_config() -> dict[str, Any]:
    from .configstore import effective
    return effective()


@app.post("/api/config")
async def set_config(payload: dict = Body(...)) -> dict[str, Any]:
    from .configstore import update
    return update(payload)


@app.post("/api/test-notify")
async def test_notify(payload: dict = Body(default={})) -> dict[str, Any]:
    channel = payload.get("channel")
    title = payload.get("title", "🔔 Stock Agent 测试")
    body = payload.get("body", "通知通道工作正常。")
    if channel:
        res = await daemon.notifier._send_one(channel, title, body)
        return {channel: res, "configured": res is not None}
    return await daemon.notifier.send_text(title, body, "normal")


# --------------------------------------------------------------------------- #
# Static frontend (serve built SPA if present)
# --------------------------------------------------------------------------- #
_FRONTEND_DIST = PROJECT_ROOT / "frontend" / "dist"
if _FRONTEND_DIST.exists():
    app.mount("/assets", StaticFiles(directory=_FRONTEND_DIST / "assets"),
              name="assets")

    @app.get("/")
    async def index() -> FileResponse:
        return FileResponse(_FRONTEND_DIST / "index.html")

    @app.get("/{path:path}")
    async def spa(path: str) -> FileResponse:
        target = _FRONTEND_DIST / path
        if target.exists() and target.is_file():
            return FileResponse(target)
        return FileResponse(_FRONTEND_DIST / "index.html")
else:
    @app.get("/")
    async def index_dev() -> dict[str, Any]:
        return {"ok": True,
                "msg": "Backend running. Frontend dev server: cd frontend && pnpm dev",
                "api": "/api/health"}
