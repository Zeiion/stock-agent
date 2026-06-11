import { useCallback, useEffect, useRef, useState } from "react";
import {
  AIProvider,
  Alert,
  AppSettings,
  Candle,
  Decision,
  DeepResult,
  Indicators,
  PaperOrder,
  Position,
  Quote,
  Rule,
  Status,
  TradingMode,
  WatchRow,
  api,
  getToken,
  useSSE,
} from "./lib/api";
import TopBar from "./components/TopBar";
import MarketStrip from "./components/MarketStrip";
import ConfigModal from "./components/ConfigModal";
import LoginModal from "./components/LoginModal";
import BriefingModal from "./components/BriefingModal";
import Watchlist from "./components/Watchlist";
import CenterPanel from "./components/CenterPanel";
import RightPanel from "./components/RightPanel";

export default function App() {
  // ---- global state ------------------------------------------------------ //
  const [watch, setWatch] = useState<WatchRow[]>([]);
  const [quotes, setQuotes] = useState<Record<string, Quote>>({});
  const [status, setStatus] = useState<Status | null>(null);
  const [settings, setSettings] = useState<AppSettings | null>(null);
  const [active, setActive] = useState<string | null>(null);

  // ---- per-symbol state -------------------------------------------------- //
  const [candles, setCandles] = useState<Candle[]>([]);
  const [days, setDays] = useState(180);
  const [interval, setInterval] = useState("1d");
  const [indicators, setIndicators] = useState<Indicators | null>(null);
  const [chartLoading, setChartLoading] = useState(false);

  // ---- right column ------------------------------------------------------ //
  const [decision, setDecision] = useState<Decision | null>(null);
  const [thinking, setThinking] = useState(false);
  const [deep, setDeep] = useState<DeepResult | null>(null);
  const [deepThinking, setDeepThinking] = useState(false);
  const [analyzeError, setAnalyzeError] = useState<string | null>(null);
  const [alerts, setAlerts] = useState<Alert[]>([]);
  const [rules, setRules] = useState<Rule[]>([]);
  const [positions, setPositions] = useState<Position[]>([]);
  const [orders, setOrders] = useState<PaperOrder[]>([]);

  const [toast, setToast] = useState<{ msg: string; ok: boolean } | null>(null);
  const [showConfig, setShowConfig] = useState(false);
  const [showBriefing, setShowBriefing] = useState(false);
  const [needLogin, setNeedLogin] = useState(false);
  // which column is visible on phones (CSS-driven; ignored on desktop)
  const [mobileView, setMobileView] = useState<"left" | "center" | "right">("left");
  const [batch, setBatch] = useState<{ state: string; done: number; total: number; summary?: string } | null>(null);
  const activeRef = useRef<string | null>(null);
  activeRef.current = active;

  const showToast = useCallback((msg: string, ok = true) => {
    setToast({ msg, ok });
    setTimeout(() => setToast(null), 2600);
  }, []);

  // ---- bootstrap (auth-aware) -------------------------------------------- //
  const bootstrap = useCallback(async () => {
    try {
      const h = await api.health();           // open endpoint
      if (h.auth && !getToken()) {
        setNeedLogin(true);
        return;
      }
      setNeedLogin(false);
      const [wl, qs, st, alertList, pos, ords] = await Promise.all([
        api.watchlist(),
        api.quotes(),
        api.settings(),
        api.alerts(80),
        api.positions(),
        api.orders(),
      ]);
      setWatch(wl);
      setQuotes(qs.quotes);
      setStatus(h.status);
      setSettings(st);
      setAlerts(alertList);
      setPositions(pos.positions);
      setOrders(ords);
      if (wl.length && !activeRef.current) setActive(wl[0].symbol);
    } catch (e) {
      if (String(e).includes("401")) setNeedLogin(true);
      else showToast("初始化失败: " + e, false);
    }
  }, [showToast]);

  useEffect(() => {
    bootstrap();
    const onUnauth = () => setNeedLogin(true);
    window.addEventListener("sa-unauthorized", onUnauth);
    return () => window.removeEventListener("sa-unauthorized", onUnauth);
  }, [bootstrap]);

  // ---- load per-symbol data on selection / timeframe change -------------- //
  const loadSymbol = useCallback(async (sym: string, d: number, iv: string) => {
    setChartLoading(true);
    try {
      const [hist, ind, decs, rls] = await Promise.all([
        api.history(sym, d, iv),
        api.indicators(sym),
        api.decisions(sym, 1),
        api.rules(sym),
      ]);
      // guard against stale responses after fast switching
      if (activeRef.current !== sym) return;
      setCandles(hist.candles);
      setIndicators(ind.indicators);
      setDecision(decs.length ? decs[0] : null);
      setRules(rls);
      setAnalyzeError(null);
    } catch (e) {
      if (activeRef.current === sym) showToast("加载行情失败: " + e, false);
    } finally {
      if (activeRef.current === sym) setChartLoading(false);
    }
  }, [showToast]);

  const shownRef = useRef<string | null>(null);
  useEffect(() => {
    if (active) {
      // On a real symbol switch (not a timeframe-only change) clear the previous
      // symbol's panels so we never show A's decision/indicators/error under B.
      if (shownRef.current !== active) {
        shownRef.current = active;
        setDecision(null);
        setDeep(null);
        setIndicators(null);
        setRules([]);
        setAnalyzeError(null);
        // candles left as-is so the chart doesn't flash empty during the fetch
      }
      loadSymbol(active, days, interval);
    } else {
      shownRef.current = null;
      setCandles([]);
      setIndicators(null);
      setDecision(null);
      setRules([]);
      setAnalyzeError(null);
    }
  }, [active, days, interval, loadSymbol]);

  // ---- SSE wiring -------------------------------------------------------- //
  const { connected } = useSSE({
    status: (d: Status) => setStatus((prev) => ({ ...(prev ?? {}), ...d } as Status)),
    quote: (q: Quote) => {
      setQuotes((prev) => ({ ...prev, [q.symbol]: q }));
    },
    indicators: (d: { symbol: string; indicators: Indicators }) => {
      if (d.symbol === activeRef.current) setIndicators(d.indicators);
    },
    alert: (a: Alert) => {
      setAlerts((prev) => [a, ...prev].slice(0, 200));
    },
    decision: (d: Decision) => {
      if (d.symbol === activeRef.current) {
        setDecision(d);
        setThinking(false);
      }
    },
    ai_status: (d: { symbol: string; state: string }) => {
      if (d.symbol === activeRef.current) {
        setThinking(d.state === "thinking");
        setDeepThinking(d.state === "deep-thinking");
      }
    },
    order: (_o: PaperOrder) => {
      // refresh orders + positions whenever any order changes
      api.orders().then(setOrders).catch(() => {});
      api.positions().then((p) => setPositions(p.positions)).catch(() => {});
    },
    batch_status: (d: { state: string; done: number; total: number; summary?: string }) => {
      setBatch(d);
      if (d.state === "done") {
        showToast("🧠 批量分析完成 · " + (d.summary || ""));
        setTimeout(() => setBatch(null), 4000);
      }
    },
  });

  const batchAnalyze = useCallback(async (strategy?: string) => {
    try {
      const r = await api.analyzeBatch(undefined, undefined, strategy);
      showToast(`已启动批量分析 ${r.started} 只,完成后将通知`);
    } catch (e) {
      showToast("批量分析启动失败: " + e, false);
    }
  }, [showToast]);

  // ---- actions ----------------------------------------------------------- //
  const addWatch = useCallback(
    async (raw: string) => {
      try {
        const res = await api.addWatch(raw);
        const wl = await api.watchlist();
        setWatch(wl);
        setActive(res.symbol);
        showToast("已添加 " + res.symbol);
      } catch (e) {
        showToast("添加失败: " + e, false);
      }
    },
    [showToast]
  );

  const delWatch = useCallback(
    async (sym: string) => {
      try {
        await api.delWatch(sym);
        const wl = await api.watchlist();
        setWatch(wl);
        if (activeRef.current === sym) {
          setActive(wl.length ? wl[0].symbol : null);
        }
      } catch (e) {
        showToast("移除失败: " + e, false);
      }
    },
    [showToast]
  );

  const analyze = useCallback(async (strategy?: string) => {
    if (!active) return;
    setThinking(true);
    setAnalyzeError(null);
    try {
      const d = await api.analyze(active, undefined, strategy);
      if (activeRef.current === active) setDecision(d);
    } catch (e) {
      setAnalyzeError(String(e));
    } finally {
      setThinking(false);
    }
  }, [active]);

  const deepAnalyze = useCallback(async (strategy?: string, debate?: boolean) => {
    if (!active) return;
    setDeepThinking(true);
    setAnalyzeError(null);
    try {
      const r = await api.deepAnalyze(active, undefined, strategy, debate);
      if (activeRef.current === active) {
        setDeep(r);
        if (r.decision) setDecision(r.decision);
      }
    } catch (e) {
      setAnalyzeError(String(e));
    } finally {
      setDeepThinking(false);
    }
  }, [active]);

  const saveSettings = useCallback(
    async (patch: Parameters<typeof api.saveSettings>[0]) => {
      try {
        const s = await api.saveSettings(patch);
        setSettings(s);
      } catch (e) {
        showToast("设置保存失败: " + e, false);
      }
    },
    [showToast]
  );

  const testNotify = useCallback(async () => {
    try {
      await api.testNotify();
      showToast("测试通知已发送");
    } catch (e) {
      showToast("通知失败: " + e, false);
    }
  }, [showToast]);

  const reloadRules = useCallback(() => {
    if (active) api.rules(active).then(setRules).catch(() => {});
  }, [active]);

  const reloadTrade = useCallback(() => {
    api.orders().then(setOrders).catch(() => {});
    api.positions().then((p) => setPositions(p.positions)).catch(() => {});
  }, []);

  const activeQuote = active ? quotes[active] ?? null : null;
  const pendingCount = orders.filter((o) => o.status === "pending").length;

  // selecting a symbol jumps to the chart view on phones (no-op styling on desktop)
  const selectSymbol = useCallback((sym: string) => {
    setActive(sym);
    setMobileView("center");
  }, []);

  return (
    <div className="app" data-mobile-view={mobileView}>
      <TopBar
        connected={connected}
        status={status}
        settings={settings}
        onChangeProvider={(p: AIProvider) => saveSettings({ ai_provider: p })}
        onToggleEnsemble={(on) => saveSettings({ ai_ensemble: on })}
        onChangeMode={(m: TradingMode) => saveSettings({ trading_mode: m })}
        onTestNotify={testNotify}
        onOpenConfig={() => setShowConfig(true)}
        onOpenBriefing={() => setShowBriefing(true)}
      />

      <MarketStrip />

      <div className="main">
        <Watchlist
          rows={watch}
          quotes={quotes}
          active={active}
          onSelect={selectSymbol}
          onAdd={addWatch}
          onDelete={delWatch}
        />

        <CenterPanel
          symbol={active}
          quote={activeQuote}
          candles={candles}
          indicators={indicators}
          rules={rules}
          days={days}
          onChangeDays={setDays}
          interval={interval}
          onChangeInterval={(iv, d) => { setInterval(iv); if (d) setDays(d); }}
          loading={chartLoading}
        />

        <RightPanel
          symbol={active}
          decision={decision}
          thinking={thinking}
          deep={deep}
          deepThinking={deepThinking}
          onDeepAnalyze={deepAnalyze}
          onBatchAnalyze={batchAnalyze}
          batch={batch}
          analyzeError={analyzeError}
          onAnalyze={analyze}
          onAccountChange={reloadTrade}
          alerts={alerts}
          rules={rules}
          onRulesChanged={reloadRules}
          positions={positions}
          orders={orders}
          onTradeChanged={reloadTrade}
          onAddSymbol={addWatch}
        />
      </div>

      <nav className="mobile-nav" aria-label="移动端导航">
        <button
          className={mobileView === "left" ? "active" : ""}
          onClick={() => setMobileView("left")}
        >
          <span className="mn-ico">☰</span>
          自选
        </button>
        <button
          className={mobileView === "center" ? "active" : ""}
          onClick={() => setMobileView("center")}
        >
          <span className="mn-ico">📈</span>
          行情
        </button>
        <button
          className={mobileView === "right" ? "active" : ""}
          onClick={() => setMobileView("right")}
        >
          <span className="mn-ico">🧠</span>
          智能
          {pendingCount > 0 && <span className="mn-badge">{pendingCount}</span>}
        </button>
      </nav>

      {showConfig && <ConfigModal onClose={() => setShowConfig(false)} />}
      {showBriefing && <BriefingModal onClose={() => setShowBriefing(false)} />}
      {needLogin && <LoginModal onSuccess={() => { setNeedLogin(false); bootstrap(); }} />}

      {toast && <div className={"toast " + (toast.ok ? "ok" : "bad")}>{toast.msg}</div>}
    </div>
  );
}
