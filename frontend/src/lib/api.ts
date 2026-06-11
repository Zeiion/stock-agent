/**
 * Typed REST client + SSE hook for the Stock Agent backend.
 *
 * All requests use the RELATIVE base "/api" so the same code works in dev
 * (Vite proxy -> 127.0.0.1:8848) and in prod (FastAPI serves the built SPA).
 *
 * Types mirror the backend dataclasses in app/models.py and the JSON shapes
 * returned by app/main.py exactly.
 */
import { useEffect, useRef, useState } from "react";

export const API_BASE = "/api";

/* --------------------------------------------------------------------------- */
/* Types                                                                       */
/* --------------------------------------------------------------------------- */
export type MarketCode = "US" | "HK" | "CN";
export type ActionType = "BUY" | "SELL" | "HOLD" | "REDUCE" | "ADD";
export type Severity = "info" | "normal" | "critical";
export type AIProvider = "auto" | "claude" | "codex" | "anthropic";
export type TradingMode = "signal" | "paper";

export interface Quote {
  symbol: string;
  market: string;
  last: number;
  prev_close: number;
  name?: string;
  long_name?: string;
  open?: number;
  high?: number;
  low?: number;
  volume?: number;
  currency?: string;
  ts?: number;
  source?: string;
  delayed?: boolean;
  change?: number;
  change_pct?: number;
}

export interface Candle {
  ts: number; // epoch seconds
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
}

export interface Indicators {
  close?: number | null;
  ma5?: number | null;
  ma10?: number | null;
  ma20?: number | null;
  ma60?: number | null;
  rsi14?: number | null;
  macd?: number | null;
  macd_signal?: number | null;
  macd_hist?: number | null;
  boll_upper?: number | null;
  boll_mid?: number | null;
  boll_lower?: number | null;
  k?: number | null;
  d?: number | null;
  j?: number | null;
  atr14?: number | null;
  vol?: number | null;
  vol_ma20?: number | null;
  bars?: number;
  tags?: string[];
  [k: string]: unknown;
}

export interface WatchRow {
  symbol: string;
  name: string;
  market: string;
  added_ts: number;
}

export interface SearchHit {
  symbol: string;
  name: string;
  market: string;
  source: string;
}

export type RuleType =
  | "price_above"
  | "price_below"
  | "pct_move"
  | "rsi_above"
  | "rsi_below"
  | "ma_cross"
  | "macd_cross"
  | "kdj_cross"
  | "volume_spike"
  | "stop_loss"
  | "take_profit";

export interface Rule {
  id: number;
  symbol: string;
  type: RuleType;
  params: Record<string, number | string>;
  severity: Severity;
  cooldown_s: number;
  active: boolean;
  note: string;
}

export interface Alert {
  id?: number;
  symbol: string;
  rule_id: number | null;
  rule_type: string;
  severity: Severity;
  message: string;
  snapshot: Record<string, unknown>;
  ts: number;
}

export interface EnsembleInfo {
  provider: string;
  model: string;
  action: ActionType;
  conviction: number;
  agree: boolean;
  rationale: string;
}

export interface Decision {
  id?: number;
  symbol: string;
  action: ActionType;
  conviction: number;
  horizon: "intraday" | "swing" | "position";
  rationale: string;
  key_risks: string[];
  entry_zone: number[] | null;
  stop_loss: number | null;
  take_profit: number[] | null;
  data_freshness_ok: boolean;
  strategy?: string;
  provider: string;
  model: string;
  ensemble: EnsembleInfo | null;
  snapshot?: Record<string, unknown>;
  realized_return?: number | null;
  ts: number;
}

export interface Position {
  id?: number;
  symbol: string;
  qty: number;
  avg_cost: number;
  last: number | null;
  pnl: number | null;
}

export interface Sentiment {
  label: "bullish" | "bearish" | "neutral";
  score: number;
  bull: number;
  bear: number;
  neutral?: number;
  n?: number;
}

export interface NewsItem {
  title: string;
  publisher: string;
  ts: number;
  link: string;
  summary: string;
  sentiment?: Sentiment;
}

export interface OptimizeResult {
  strategy: string;
  tested: number;
  best: {
    params: Record<string, number>;
    total_return: number;
    max_drawdown: number;
    sharpe: number;
    num_trades: number;
    win_rate: number;
  } | null;
  results: {
    params: Record<string, number>;
    total_return: number;
    max_drawdown: number;
    sharpe: number;
    num_trades: number;
    win_rate: number;
  }[];
}

export interface AnalystOpinion {
  dimension: string;
  stance: "bullish" | "bearish" | "neutral";
  score: number;
  summary: string;
  key_points: string[];
  provider: string;
}

export interface ResearcherView {
  side: "bull" | "bear";
  thesis: string;
  arguments: string[];
  rebuttal: string;
  confidence: number;
  provider: string;
}

export interface DeepResult {
  symbol: string;
  analysts: AnalystOpinion[];
  decision: Decision | null;
  ts: number;
  researchers?: { bull: ResearcherView; bear: ResearcherView };
}

export interface PersonaOpinion {
  key: string;
  label: string;
  signal: "bullish" | "bearish" | "neutral";
  action: string;
  confidence: number;
  reasoning: string;
  key_points: string[];
  provider: string;
}

export interface PersonaConsensus {
  signal: "bullish" | "bearish" | "neutral";
  action: string;
  score: number;
  confidence: number;
  avg_confidence: number;
  counts: { bullish: number; bearish: number; neutral: number };
  participation: number;
  dissenters: string[];
}

export interface PersonaPanelResult {
  symbol: string;
  panel: PersonaOpinion[];
  consensus: PersonaConsensus;
  ts: number;
}

export interface IntradayMethod {
  name: string;
  high: number;
  low: number;
  note: string;
}

export interface DayTPlan {
  viable: boolean;
  buy_limit: number | null;
  sell_limit: number | null;
  buy_zone: (number | null)[];
  sell_zone: (number | null)[];
  spread_pct: number;
  stop_below: number | null;
  breakout_above: number | null;
  position_qty: number;
  avg_price: number | null;
  suggested_qty: number;
  last: number | null;
  actions: string[];
  caveats: string[];
}

export interface DayTAI {
  recommend: "做T" | "观望" | "不建议";
  narrative: string;
  buy_limit: number | null;
  sell_limit: number | null;
  confidence: number;
  risks: string[];
  provider: string;
}

export interface IntradayResult {
  symbol: string;
  market: string;
  anchor: number | null;
  anchor_kind: string;
  prev_close: number | null;
  predicted_high: number | null;
  predicted_low: number | null;
  expected_range_pct: number;
  confidence: number;
  conviction: number;
  atr14: number | null;
  methods: IntradayMethod[];
  pivots: Record<string, Record<string, number | null>>;
  volatility: Record<string, number | null>;
  limits: { applied: boolean; limit_pct?: number; limit_up?: number; limit_down?: number };
  plan?: DayTPlan;
  ai?: DayTAI;
  note?: string;
  ts: number;
}

export interface PortfolioPosition {
  symbol: string;
  market: string;
  name: string;
  qty: number;
  avg_cost: number;
  last: number;
  value: number;
  unrealized: number;
  unrealized_pct: number;
}

export interface RealizedSummary {
  closed_trades: number;
  wins: number;
  losses: number;
  win_rate: number;
  realized_pnl: number;
  avg_return_pct: number;
  best: number;
  worst: number;
}

export interface Portfolio {
  nav: number;
  holdings_value: number;
  cost_basis: number;
  unrealized: number;
  unrealized_pct: number;
  realized: RealizedSummary;
  positions: PortfolioPosition[];
  exposure: Record<string, number>;
  exposure_pct: Record<string, number>;
}

export interface NavPoint {
  ts: number;
  nav: number;
  holdings_value: number;
  unrealized: number;
  realized_cum: number;
}

export interface RealizedTrade {
  id: number;
  symbol: string;
  qty: number;
  avg_cost: number;
  exit_price: number;
  pnl: number;
  ret_pct: number;
  ts: number;
}

export interface Fundamentals {
  market_cap?: number | null;
  pe?: number | null;
  forward_pe?: number | null;
  pb?: number | null;
  dividend_yield?: number | null;
  roe?: number | null;
  profit_margin?: number | null;
  revenue_growth?: number | null;
  earnings_growth?: number | null;
  debt_to_equity?: number | null;
  beta?: number | null;
  high_52w?: number | null;
  low_52w?: number | null;
  pos_52w?: number | null;
  target_price?: number | null;
  recommendation?: string | null;
  sector?: string;
  industry?: string;
}

export interface SocialPost {
  title: string;
  author: string;
  link: string;
  ts: number;
  source: string;
  sentiment?: Sentiment;
}

export interface SocialResp {
  symbol: string;
  posts: SocialPost[];
  aggregate: Sentiment & { n: number };
  fear_greed: { score?: number; rating?: string };
  cn_buzz: { hot_rank?: number; comment_score?: number; institution_pct?: number };
  kol_handles: string[];
}

export interface MarketResp {
  indices: { ticker: string; name: string; group: string; last: number; change_pct: number }[];
  fear_greed: { score?: number; rating?: string };
  ts: number;
}

export interface Briefing {
  summary: string;
  movers: { symbol: string; note: string }[];
  opportunities: { symbol: string; action: string; reason: string }[];
  risks: string[];
  provider?: string;
  generated_ts: number;
}

export interface BrokerStatus {
  broker: string;
  alpaca_available: boolean;
  account: Record<string, unknown>;
  positions: { symbol: string; qty: number; avg_cost: number; last: number; pnl: number }[];
}

export interface ConfigResp {
  fields: Record<string, unknown>;
  groups: Record<string, string[]>;
  secret_fields: string[];
  bool_fields: string[];
  channels: string[];
}

export interface ScreenMatch {
  symbol: string;
  market: string;
  name: string;
  last: number;
  change_pct: number;
  rsi14: number | null;
  j: number | null;
  macd_hist: number | null;
  ma5: number | null;
  ma20: number | null;
  tags: string[];
}

export interface ScreenResult {
  universe: string;
  scanned: number;
  matched: number;
  matches: ScreenMatch[];
}

export interface TrackRecord {
  scored: number;
  correct: number;
  accuracy: number;
  avg_move: number;
  buy_signal_alpha: number;
  by_action: Record<string, { count: number; correct: number; accuracy: number; avg_move: number }>;
  by_strategy: Record<string, { count: number; correct: number; accuracy: number; avg_move: number }>;
  recent: {
    symbol: string;
    action: string;
    conviction: number;
    provider: string;
    entry: number;
    current: number;
    move_pct: number;
    correct: boolean;
    ts: number;
  }[];
}

export interface PaperOrder {
  id: number;
  symbol: string;
  side: "BUY" | "SELL";
  qty: number;
  limit_price: number | null;
  status: "pending" | "approved" | "filled" | "rejected" | "cancelled";
  fill_price: number | null;
  source: string;
  note: string;
  ts: number;
}

export interface BacktestStats {
  total_return: number;
  buy_hold_return: number;
  max_drawdown: number;
  num_trades: number;
  win_rate: number;
  sharpe: number;
}

export interface BacktestTrade {
  entry_ts: number;
  entry_price: number;
  exit_ts: number;
  exit_price: number;
  return_pct: number;
}

export interface BacktestResult {
  symbol: string;
  strategy: string;
  params: Record<string, number>;
  market: string;
  stats: BacktestStats;
  equity_curve: { ts: number; equity: number }[];
  trades: BacktestTrade[];
}

export interface Status {
  running: boolean;
  last_poll_ts: number;
  last_error?: string;
  watch_count?: number;
  markets_open: Record<MarketCode, boolean>;
  ai_provider: string;
  ai_ensemble: boolean;
  trading_mode: TradingMode;
  subscribers?: number;
}

export interface AppSettings {
  ai_provider: string;
  ai_ensemble: boolean;
  ai_providers_available: string[];
  trading_mode: TradingMode;
  require_human_approval: boolean;
  poll_interval_s: number;
  notify_channels: string[];
}

/* --------------------------------------------------------------------------- */
/* Low-level fetch helpers                                                     */
/* --------------------------------------------------------------------------- */
let authToken = (typeof localStorage !== "undefined" && localStorage.getItem("sa_token")) || "";
export function setToken(t: string) {
  authToken = t || "";
  try {
    if (t) localStorage.setItem("sa_token", t);
    else localStorage.removeItem("sa_token");
  } catch { /* ignore */ }
}
export function getToken() {
  return authToken;
}

async function req<T>(path: string, init?: RequestInit): Promise<T> {
  const headers: Record<string, string> = { "Content-Type": "application/json" };
  if (authToken) headers["Authorization"] = "Bearer " + authToken;
  const res = await fetch(API_BASE + path, {
    ...init,
    headers: { ...headers, ...((init?.headers as Record<string, string>) || {}) },
  });
  if (res.status === 401) {
    setToken("");
    if (typeof window !== "undefined")
      window.dispatchEvent(new CustomEvent("sa-unauthorized"));
    throw new Error("401 未授权，请登录");
  }
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      detail = (body && (body.detail || body.message)) || detail;
    } catch {
      /* ignore non-JSON error bodies */
    }
    throw new Error(`${res.status} ${detail}`);
  }
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

const get = <T>(p: string) => req<T>(p);
const post = <T>(p: string, body?: unknown) =>
  req<T>(p, { method: "POST", body: body ? JSON.stringify(body) : "{}" });
const patch = <T>(p: string, body?: unknown) =>
  req<T>(p, { method: "PATCH", body: JSON.stringify(body ?? {}) });
const del = <T>(p: string) => req<T>(p, { method: "DELETE" });

/* --------------------------------------------------------------------------- */
/* Typed API surface                                                           */
/* --------------------------------------------------------------------------- */
export const api = {
  health: () => get<{ ok: boolean; auth: boolean; status: Status }>("/health"),

  // watchlist
  watchlist: () => get<WatchRow[]>("/watchlist"),
  addWatch: (symbol: string, name?: string) =>
    post<{ ok: boolean; symbol: string }>("/watchlist", { symbol, name }),
  delWatch: (symbol: string) =>
    del<{ ok: boolean }>("/watchlist/" + encodeURIComponent(symbol)),
  search: (q: string) =>
    get<{ query: string; results: SearchHit[] }>(
      "/search?q=" + encodeURIComponent(q)
    ),

  // quotes / history / indicators
  quotes: () => get<{ quotes: Record<string, Quote> }>("/quotes"),
  quote: (symbol: string) => get<Quote>("/quote/" + encodeURIComponent(symbol)),
  history: (symbol: string, days = 200, interval = "1d") =>
    get<{ symbol: string; candles: Candle[] }>(
      `/history/${encodeURIComponent(symbol)}?days=${days}&interval=${interval}`
    ),
  indicators: (symbol: string) =>
    get<{ symbol: string; indicators: Indicators }>(
      "/indicators/" + encodeURIComponent(symbol)
    ),

  // rules
  rules: (symbol?: string) =>
    get<Rule[]>("/rules" + (symbol ? "?symbol=" + encodeURIComponent(symbol) : "")),
  addRule: (payload: {
    symbol: string;
    type: RuleType;
    params: Record<string, number | string>;
    severity?: Severity;
    cooldown_s?: number;
    note?: string;
  }) => post<{ ok: boolean; id: number }>("/rules", payload),
  patchRule: (id: number, payload: { active?: boolean }) =>
    patch<{ ok: boolean }>("/rules/" + id, payload),
  delRule: (id: number) => del<{ ok: boolean }>("/rules/" + id),

  // AI
  aiStrategies: () =>
    get<{ strategies: { key: string; label: string; lens: string; group: string }[] }>(
      "/ai-strategies"
    ),
  fundamentals: (symbol: string) =>
    get<{ symbol: string; fundamentals: Fundamentals; labels: Record<string, string> }>(
      "/fundamentals/" + encodeURIComponent(symbol)
    ),
  social: (symbol: string) =>
    get<SocialResp>("/social/" + encodeURIComponent(symbol)),
  market: () => get<MarketResp>("/market"),
  analyze: (symbol: string, provider?: AIProvider, strategy?: string) =>
    post<Decision>("/analyze/" + encodeURIComponent(symbol), { provider, strategy }),
  deepAnalyze: (symbol: string, provider?: AIProvider, strategy?: string, debate?: boolean) =>
    post<DeepResult>("/deep-analyze/" + encodeURIComponent(symbol),
      { provider, strategy, debate }),
  personaPanel: (symbol: string, personas?: string[], provider?: AIProvider) =>
    post<PersonaPanelResult>("/persona-panel/" + encodeURIComponent(symbol),
      { personas, provider }),
  dayT: (symbol: string, ai?: boolean, useGarch?: boolean) =>
    post<IntradayResult>("/day-t/" + encodeURIComponent(symbol),
      { ai, use_garch: useGarch }),
  analyzeBatch: (symbols?: string[], provider?: AIProvider, strategy?: string) =>
    post<{ ok: boolean; started: number }>("/analyze-batch", { symbols, provider, strategy }),
  // accounts (paper books)
  accounts: () => get<{ accounts: string[]; current: string }>("/accounts"),
  createAccount: (name: string, switchTo = true) =>
    post<{ accounts: string[]; current: string }>("/accounts", { name, switch: switchTo }),
  switchAccount: (name: string) =>
    post<{ current: string }>("/accounts/switch", { name }),
  resetAccount: (name?: string) =>
    post<{ ok: boolean }>("/accounts/reset", name ? { name } : {}),
  deleteAccount: (name: string) =>
    del<{ accounts: string[]; current: string }>("/accounts/" + encodeURIComponent(name)),
  decisions: (symbol?: string, limit = 50) =>
    get<Decision[]>(
      "/decisions?limit=" + limit + (symbol ? "&symbol=" + encodeURIComponent(symbol) : "")
    ),
  alerts: (limit = 100) => get<Alert[]>("/alerts?limit=" + limit),

  // intelligence / portfolio
  news: (symbol: string, limit = 10) =>
    get<{ symbol: string; news: NewsItem[]; sentiment: Sentiment }>(
      `/news/${encodeURIComponent(symbol)}?limit=${limit}`
    ),
  strategies: () =>
    get<{ strategies: string[]; default_grid: Record<string, Record<string, number[]>> }>(
      "/strategies"
    ),
  optimizeBacktest: (payload: { symbol: string; strategy: string; days: number }) =>
    post<OptimizeResult>("/backtest/optimize", payload),
  exportUrl: (kind: string) => API_BASE + "/export/" + kind,
  portfolio: () => get<Portfolio>("/portfolio"),
  portfolioHistory: () => get<{ history: NavPoint[] }>("/portfolio/history"),
  realized: () => get<{ trades: RealizedTrade[] }>("/realized"),
  trackRecord: () => get<TrackRecord>("/track-record"),
  screenFields: () => get<{ fields: string[]; ops: string[] }>("/screen/fields"),
  screen: (payload: {
    universe: string;
    filters: { field: string; op: string; value: number }[];
    limit?: number;
  }) => post<ScreenResult>("/screen", payload),

  // positions / orders
  positions: () => get<{ positions: Position[] }>("/positions"),
  orders: (status?: string) =>
    get<PaperOrder[]>("/orders" + (status ? "?status=" + status : "")),
  submitOrder: (payload: {
    symbol: string;
    side: "BUY" | "SELL";
    qty: number;
    limit_price?: number;
  }) => post<PaperOrder>("/orders", payload),
  approveOrder: (id: number) => post<unknown>(`/orders/${id}/approve`),
  rejectOrder: (id: number) => post<unknown>(`/orders/${id}/reject`),

  // backtest
  backtest: (payload: {
    symbol: string;
    strategy: string;
    params: Record<string, number>;
    days: number;
  }) => post<BacktestResult>("/backtest", payload),

  // settings
  settings: () => get<AppSettings>("/settings"),
  saveSettings: (payload: Partial<{
    ai_provider: AIProvider;
    ai_ensemble: boolean;
    trading_mode: TradingMode;
    require_human_approval: boolean;
  }>) => post<AppSettings>("/settings", payload),

  testNotify: () => post<Record<string, unknown>>("/test-notify"),
  testChannel: (channel: string) =>
    post<Record<string, unknown>>("/test-notify", { channel }),
  getConfig: () => get<ConfigResp>("/config"),
  saveConfig: (patch: Record<string, unknown>) =>
    post<ConfigResp>("/config", patch),
  login: (username: string, password: string) =>
    post<{ ok: boolean; token: string; auth: boolean }>("/login", { username, password }),
  briefing: () => get<Briefing>("/briefing"),
  generateBriefing: () => post<Briefing>("/briefing/generate"),
  brokerStatus: () => get<BrokerStatus>("/broker/status"),
};

/* --------------------------------------------------------------------------- */
/* SSE hook                                                                     */
/* --------------------------------------------------------------------------- */
export type SSEEventType =
  | "hello"
  | "status"
  | "quote"
  | "indicators"
  | "alert"
  | "decision"
  | "order"
  | "ai_status"
  | "batch_status"
  | "briefing"
  | "nav";

export interface SSEEnvelope<T = unknown> {
  type: SSEEventType;
  data: T;
  ts: number;
}

export type SSEHandlers = Partial<{
  [K in SSEEventType]: (data: any) => void;
}> & {
  onOpen?: () => void;
  onClose?: () => void;
};

/**
 * Subscribe to /api/stream via native EventSource.
 * - Dispatches each named event to its handler.
 * - Auto-reconnects with backoff on error.
 * - Exposes a `connected` boolean.
 *
 * Handlers are kept in a ref so the EventSource is created exactly once and
 * does not churn when callers pass fresh handler closures each render.
 */
export function useSSE(handlers: SSEHandlers): { connected: boolean } {
  const [connected, setConnected] = useState(false);
  const handlersRef = useRef(handlers);
  handlersRef.current = handlers;

  useEffect(() => {
    let es: EventSource | null = null;
    let retry: ReturnType<typeof setTimeout> | null = null;
    let backoff = 1000;
    let closed = false;

    const NAMED: SSEEventType[] = [
      "hello",
      "status",
      "quote",
      "indicators",
      "alert",
      "decision",
      "order",
      "ai_status",
      "batch_status",
      "briefing",
      "nav",
    ];

    const connect = () => {
      const t = getToken();
      es = new EventSource(API_BASE + "/stream" + (t ? "?token=" + encodeURIComponent(t) : ""));

      es.onopen = () => {
        backoff = 1000;
        setConnected(true);
        handlersRef.current.onOpen?.();
      };

      const dispatch = (type: SSEEventType) => (ev: MessageEvent) => {
        try {
          const env = JSON.parse(ev.data) as SSEEnvelope;
          const fn = handlersRef.current[type];
          if (fn) fn(env.data);
        } catch {
          /* ignore malformed frames */
        }
      };

      for (const t of NAMED) es.addEventListener(t, dispatch(t));

      es.onerror = () => {
        setConnected(false);
        handlersRef.current.onClose?.();
        es?.close();
        es = null;
        if (closed) return;
        retry = setTimeout(connect, backoff);
        backoff = Math.min(backoff * 2, 15000);
      };
    };

    connect();

    return () => {
      closed = true;
      if (retry) clearTimeout(retry);
      es?.close();
    };
  }, []);

  return { connected };
}

/* --------------------------------------------------------------------------- */
/* Formatting helpers                                                          */
/* --------------------------------------------------------------------------- */
export function fmtNum(n: number | null | undefined, dp = 2): string {
  if (n === null || n === undefined || Number.isNaN(n)) return "—";
  return n.toLocaleString(undefined, {
    minimumFractionDigits: dp,
    maximumFractionDigits: dp,
  });
}

export function fmtPct(n: number | null | undefined, dp = 2): string {
  if (n === null || n === undefined || Number.isNaN(n)) return "—";
  const sign = n > 0 ? "+" : "";
  return `${sign}${n.toFixed(dp)}%`;
}

export function fmtVol(n: number | null | undefined): string {
  if (n === null || n === undefined || Number.isNaN(n)) return "—";
  const a = Math.abs(n);
  if (a >= 1e8) return (n / 1e8).toFixed(2) + "亿";
  if (a >= 1e4) return (n / 1e4).toFixed(2) + "万";
  return fmtNum(n, 0);
}

export function fmtTime(tsSeconds: number | null | undefined): string {
  if (!tsSeconds) return "—";
  const d = new Date(tsSeconds * 1000);
  return d.toLocaleTimeString("zh-CN", { hour12: false });
}

export function fmtDateTime(tsSeconds: number | null | undefined): string {
  if (!tsSeconds) return "—";
  const d = new Date(tsSeconds * 1000);
  return d.toLocaleString("zh-CN", { hour12: false });
}

// Up = green, Down = red (international convention; flip the two color vars in
// index.css to use the Chinese red-up / green-down convention if preferred).
export function changeClass(n: number | null | undefined): string {
  if (n === null || n === undefined || n === 0) return "flat";
  return n > 0 ? "up" : "down";
}

export function currencySymbol(market: string | undefined): string {
  switch (market) {
    case "US":
      return "$";
    case "HK":
      return "HK$";
    case "CN":
      return "¥";
    default:
      return "";
  }
}
