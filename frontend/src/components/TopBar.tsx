import { AIProvider, AppSettings, Status, TradingMode } from "../lib/api";

interface Props {
  connected: boolean;
  status: Status | null;
  settings: AppSettings | null;
  onChangeProvider: (p: AIProvider) => void;
  onToggleEnsemble: (on: boolean) => void;
  onChangeMode: (m: TradingMode) => void;
  onTestNotify: () => void;
  onOpenConfig: () => void;
  onOpenBriefing: () => void;
}

const PROVIDERS: AIProvider[] = ["auto", "claude", "codex", "anthropic"];
const MARKETS: ("US" | "HK" | "CN")[] = ["US", "HK", "CN"];

export default function TopBar({
  connected,
  status,
  settings,
  onChangeProvider,
  onToggleEnsemble,
  onChangeMode,
  onTestNotify,
  onOpenConfig,
  onOpenBriefing,
}: Props) {
  const provider = (settings?.ai_provider ?? "auto") as AIProvider;
  const ensemble = settings?.ai_ensemble ?? false;
  const mode = (settings?.trading_mode ?? "signal") as TradingMode;
  const marketsOpen = status?.markets_open ?? { US: false, HK: false, CN: false };
  const running = status?.running ?? false;

  return (
    <header className="topbar">
      <div className="brand">
        <span className="logo">智</span>
        <div>
          Stock Agent
          <div className="sub">AI 盯盘 · 决策 · 轻量化量化</div>
        </div>
      </div>

      <div className="conn" title={running ? "守护进程运行中" : "守护进程已停止"}>
        <span className={"dot " + (connected && running ? "on" : "off")} />
        {connected ? (running ? "实时" : "已连接") : "连接中…"}
      </div>

      <div className="market-badges">
        {MARKETS.map((m) => (
          <span key={m} className={"mbadge " + (marketsOpen[m] ? "open" : "")}>
            {m}
          </span>
        ))}
      </div>

      <div className="spacer" />

      <div className="controls">
        <span className="ctl-label">AI</span>
        <select
          value={provider}
          onChange={(e) => onChangeProvider(e.target.value as AIProvider)}
          title="AI 决策提供方"
        >
          {PROVIDERS.map((p) => (
            <option key={p} value={p}>
              {p}
            </option>
          ))}
        </select>

        <label
          className={"toggle " + (ensemble ? "on" : "")}
          onClick={() => onToggleEnsemble(!ensemble)}
          title="启用双模型集成（第二意见）"
        >
          <span className="track">
            <span className="knob" />
          </span>
          集成
        </label>

        <span className="ctl-label">模式</span>
        <div className="seg">
          <button
            className={mode === "signal" ? "active" : ""}
            onClick={() => onChangeMode("signal")}
            title="仅信号 — 不下模拟单"
          >
            信号
          </button>
          <button
            className={mode === "paper" ? "active" : ""}
            onClick={() => onChangeMode("paper")}
            title="模拟盘 — 决策生成模拟订单"
          >
            模拟
          </button>
        </div>

        <button className="ghost" onClick={onOpenBriefing} title="AI 盘前简报">
          📋 简报
        </button>
        <button className="ghost" onClick={onTestNotify} title="发送一条测试通知">
          测试通知
        </button>
        <button className="ghost" onClick={onOpenConfig} title="配置中心">
          ⚙ 设置
        </button>
      </div>
    </header>
  );
}
