import { useState } from "react";
import {
  Alert,
  Decision,
  DeepResult,
  PaperOrder,
  Position,
  Rule,
} from "../lib/api";
import AIPanel from "./AIPanel";
import AlertsPanel from "./AlertsPanel";
import BacktestPanel from "./BacktestPanel";
import DayTPanel from "./DayTPanel";
import PositionsPanel from "./PositionsPanel";
import RulesPanel from "./RulesPanel";
import NewsPanel from "./NewsPanel";
import PortfolioPanel from "./PortfolioPanel";
import TrackRecordPanel from "./TrackRecordPanel";
import ScreenerPanel from "./ScreenerPanel";
import DecisionsPanel from "./DecisionsPanel";

type Tab =
  | "ai"
  | "records"
  | "news"
  | "alerts"
  | "rules"
  | "trade"
  | "portfolio"
  | "track"
  | "screen"
  | "backtest"
  | "dayt";

interface Props {
  symbol: string | null;
  decision: Decision | null;
  thinking: boolean;
  deep: DeepResult | null;
  deepThinking: boolean;
  onDeepAnalyze: () => void;
  onBatchAnalyze: () => void;
  batch: { state: string; done: number; total: number; summary?: string } | null;
  analyzeError: string | null;
  onAnalyze: () => void;
  alerts: Alert[];
  rules: Rule[];
  onRulesChanged: () => void;
  positions: Position[];
  orders: PaperOrder[];
  onTradeChanged: () => void;
  onAddSymbol: (raw: string) => Promise<void>;
  onAccountChange: () => void;
}

const TABS: { key: Tab; label: string }[] = [
  { key: "ai", label: "AI 决策" },
  { key: "records", label: "AI记录" },
  { key: "news", label: "新闻" },
  { key: "alerts", label: "提醒" },
  { key: "rules", label: "规则" },
  { key: "trade", label: "持仓/订单" },
  { key: "portfolio", label: "组合" },
  { key: "track", label: "战绩" },
  { key: "screen", label: "选股" },
  { key: "backtest", label: "回测" },
  { key: "dayt", label: "做T" },
];

export default function RightPanel(props: Props) {
  const [tab, setTab] = useState<Tab>("ai");
  const pendingCount = props.orders.filter((o) => o.status === "pending").length;

  return (
    <div className="col right">
      <div className="tabs">
        {TABS.map((t) => (
          <button
            key={t.key}
            className={tab === t.key ? "active" : ""}
            onClick={() => setTab(t.key)}
          >
            {t.label}
            {t.key === "trade" && pendingCount > 0 ? ` (${pendingCount})` : ""}
            {t.key === "alerts" && props.alerts.length > 0
              ? ` (${props.alerts.length})`
              : ""}
          </button>
        ))}
      </div>

      <div className="tab-body">
        {tab === "ai" && (
          <AIPanel
            symbol={props.symbol}
            decision={props.decision}
            thinking={props.thinking}
            deep={props.deep}
            deepThinking={props.deepThinking}
            onDeepAnalyze={props.onDeepAnalyze}
            onBatchAnalyze={props.onBatchAnalyze}
            batch={props.batch}
            error={props.analyzeError}
            onAnalyze={props.onAnalyze}
          />
        )}
        {tab === "records" && <DecisionsPanel symbol={props.symbol} />}
        {tab === "news" && <NewsPanel symbol={props.symbol} />}
        {tab === "alerts" && <AlertsPanel alerts={props.alerts} />}
        {tab === "rules" && (
          <RulesPanel
            symbol={props.symbol}
            rules={props.rules}
            onChanged={props.onRulesChanged}
          />
        )}
        {tab === "trade" && (
          <PositionsPanel
            symbol={props.symbol}
            positions={props.positions}
            orders={props.orders}
            onChanged={props.onTradeChanged}
          />
        )}
        {tab === "portfolio" && <PortfolioPanel onAccountChange={props.onAccountChange} />}
        {tab === "track" && <TrackRecordPanel />}
        {tab === "screen" && <ScreenerPanel onAdd={props.onAddSymbol} />}
        {tab === "backtest" && <BacktestPanel symbol={props.symbol} />}
        {tab === "dayt" && <DayTPanel symbol={props.symbol} />}
      </div>
    </div>
  );
}
