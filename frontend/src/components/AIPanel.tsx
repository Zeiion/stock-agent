import { useEffect, useState } from "react";
import {
  AnalystOpinion, Decision, DeepResult, PersonaPanelResult, PersonaOpinion,
  ResearcherView, api, fmtDateTime, fmtNum,
} from "../lib/api";
import {
  cardFilename, copyImageToClipboard, downloadImage, renderDecisionCard,
} from "../lib/decisionCard";

const STRATEGY_LABEL: Record<string, string> = {
  balanced: "均衡", value: "价值投资", momentum: "动量趋势",
  swing: "波段技术", short_term: "短线", contrarian: "逆向",
  buffett: "巴菲特", munger: "芒格", graham: "格雷厄姆", lynch: "彼得·林奇",
  wood: "木头姐", burry: "迈克尔·伯里", livermore: "利弗莫尔", dalio: "达利欧",
};

interface Props {
  symbol: string | null;
  decision: Decision | null;
  thinking: boolean;
  deep: DeepResult | null;
  deepThinking: boolean;
  onDeepAnalyze: (strategy: string, debate?: boolean) => void;
  onBatchAnalyze: (strategy: string) => void;
  batch: { state: string; done: number; total: number; summary?: string } | null;
  error: string | null;
  onAnalyze: (strategy: string) => void;
}

const STANCE: Record<string, { label: string; cls: string }> = {
  bullish: { label: "看多", cls: "bull" },
  bearish: { label: "看空", cls: "bear" },
  neutral: { label: "中性", cls: "neut" },
};

function AnalystCard({ a }: { a: AnalystOpinion }) {
  const s = STANCE[a.stance] ?? STANCE.neutral;
  return (
    <div className="analyst-card">
      <div className="ac-head">
        <span className="ac-dim">{a.dimension}</span>
        <span className={"stance-badge " + s.cls}>{s.label}</span>
        <span className="ac-score" title={`强度 ${a.score}/5`}>
          {"●".repeat(Math.max(0, Math.min(5, a.score)))}
          <span className="empty">{"●".repeat(5 - Math.max(0, Math.min(5, a.score)))}</span>
        </span>
      </div>
      <div className="ac-summary">{a.summary}</div>
      {a.key_points && a.key_points.length > 0 && (
        <ul className="ac-points">
          {a.key_points.map((p, i) => (
            <li key={i}>{p}</li>
          ))}
        </ul>
      )}
      {a.provider && a.provider !== "-" && (
        <div className="ac-foot">{a.provider}</div>
      )}
    </div>
  );
}

function PersonaConsensusBar({ c }: { c: PersonaPanelResult["consensus"] }) {
  const s = STANCE[c.signal] ?? STANCE.neutral;
  return (
    <div className="decision-card" style={{ marginBottom: 8 }}>
      <div className="dc-head">
        <span className={"action-badge " + c.action}>{c.action}</span>
        <span className={"stance-badge " + s.cls}>{s.label}共识</span>
        <span className="horizon" title="加权投票得分 [-1,1]">得分 {c.score}</span>
        <span style={{ marginLeft: "auto", fontSize: 11, color: "var(--text-faint)" }}>
          看多 {c.counts.bullish} · 看空 {c.counts.bearish} · 中性 {c.counts.neutral}
        </span>
      </div>
      <div className="dc-body" style={{ fontSize: 12 }}>
        共识信心 <b>{c.confidence}</b>/100 · 平均信心 {c.avg_confidence} · 参与度{" "}
        {Math.round(c.participation * 100)}%
        {c.dissenters.length > 0 && (
          <span> · 异见：{c.dissenters.join("、")}</span>
        )}
      </div>
    </div>
  );
}

function PersonaCard({ p }: { p: PersonaOpinion }) {
  const s = STANCE[p.signal] ?? STANCE.neutral;
  return (
    <div className="analyst-card">
      <div className="ac-head">
        <span className="ac-dim">{p.label}</span>
        <span className={"stance-badge " + s.cls}>{s.label}</span>
        <span className={"action-badge " + p.action} style={{ fontSize: 10 }}>
          {p.action}
        </span>
        <span className="ac-score" title={`信心 ${p.confidence}/100`}>
          {p.confidence}
        </span>
      </div>
      <div className="ac-summary">{p.reasoning}</div>
      {p.key_points && p.key_points.length > 0 && (
        <ul className="ac-points">
          {p.key_points.map((k, i) => <li key={i}>{k}</li>)}
        </ul>
      )}
      {p.provider && p.provider !== "-" && <div className="ac-foot">{p.provider}</div>}
    </div>
  );
}

function ResearcherCard({ r }: { r: ResearcherView }) {
  const bull = r.side === "bull";
  return (
    <div className="analyst-card">
      <div className="ac-head">
        <span className="ac-dim">{bull ? "🐂 多头研究员" : "🐻 空头研究员"}</span>
        <span className={"stance-badge " + (bull ? "bull" : "bear")}>
          {bull ? "看多" : "看空"}
        </span>
        <span className="ac-score" title={`论据信心 ${r.confidence}/5`}>
          {"●".repeat(Math.max(0, Math.min(5, r.confidence)))}
          <span className="empty">{"●".repeat(5 - Math.max(0, Math.min(5, r.confidence)))}</span>
        </span>
      </div>
      <div className="ac-summary"><b>{r.thesis}</b></div>
      {r.arguments && r.arguments.length > 0 && (
        <ul className="ac-points">{r.arguments.map((a, i) => <li key={i}>{a}</li>)}</ul>
      )}
      {r.rebuttal && <div className="ac-summary" style={{ opacity: 0.8 }}>↪ {r.rebuttal}</div>}
    </div>
  );
}

function ExportControls({ decision }: { decision: Decision }) {
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<{ ok: boolean; text: string } | null>(null);

  const labels = {
    strategyLabel: decision.strategy
      ? STRATEGY_LABEL[decision.strategy] ?? decision.strategy
      : undefined,
    horizonLabel: HORIZON_LABEL[decision.horizon] || decision.horizon,
  };

  function flash(ok: boolean, text: string) {
    setNote({ ok, text });
    setTimeout(() => setNote(null), 2200);
  }

  async function onCopy() {
    setBusy(true);
    try {
      await copyImageToClipboard(await renderDecisionCard(decision, labels));
      flash(true, "已复制 ✓");
    } catch (e) {
      flash(false, e instanceof Error ? e.message : "复制失败");
    } finally {
      setBusy(false);
    }
  }

  async function onDownload() {
    setBusy(true);
    try {
      downloadImage(await renderDecisionCard(decision, labels), cardFilename(decision));
      flash(true, "已下载 ✓");
    } catch (e) {
      flash(false, e instanceof Error ? e.message : "下载失败");
    } finally {
      setBusy(false);
    }
  }

  return (
    <span className="dc-head-right">
      <span className="horizon" style={{ marginLeft: 0 }}>
        {HORIZON_LABEL[decision.horizon] || decision.horizon}
      </span>
      {note && (
        <span className={"dc-export-note " + (note.ok ? "ok" : "err")}>{note.text}</span>
      )}
      <button className="dc-export-btn" onClick={onCopy} disabled={busy} title="将该卡片复制为图片">
        {busy ? "…" : "复制图片"}
      </button>
      <button className="dc-export-btn" onClick={onDownload} disabled={busy} title="将该卡片下载为 PNG">
        下载图片
      </button>
    </span>
  );
}

function Stars({ n }: { n: number }) {
  const full = Math.max(0, Math.min(5, n));
  return (
    <span className="stars" title={`确信度 ${full}/5`}>
      {"★".repeat(full)}
      <span className="empty">{"★".repeat(5 - full)}</span>
    </span>
  );
}

const HORIZON_LABEL: Record<string, string> = {
  intraday: "日内",
  swing: "波段",
  position: "中长线",
};

export default function AIPanel({
  symbol,
  decision,
  thinking,
  deep,
  deepThinking,
  onDeepAnalyze,
  onBatchAnalyze,
  batch,
  error,
  onAnalyze,
}: Props) {
  const [debate, setDebate] = useState(false);
  const [panel, setPanel] = useState<PersonaPanelResult | null>(null);
  const [panelBusy, setPanelBusy] = useState(false);
  const [panelErr, setPanelErr] = useState<string | null>(null);
  const busy = thinking || deepThinking || panelBusy;
  const batchRunning = batch?.state === "running";
  const [strategy, setStrategy] = useState("balanced");
  const [strategies, setStrategies] = useState<
    { key: string; label: string; lens: string; group: string }[]
  >([]);

  useEffect(() => {
    api.aiStrategies().then((r) => setStrategies(r.strategies)).catch(() => {});
  }, []);

  // reset the persona panel when the active symbol changes
  useEffect(() => { setPanel(null); setPanelErr(null); }, [symbol]);

  async function runPanel() {
    if (!symbol) return;
    setPanelBusy(true);
    setPanelErr(null);
    try {
      setPanel(await api.personaPanel(symbol));
    } catch (e) {
      setPanelErr(String(e));
    } finally {
      setPanelBusy(false);
    }
  }

  const lens = strategies.find((s) => s.key === strategy)?.lens;
  const groups = Array.from(new Set(strategies.map((s) => s.group || "风格")));

  return (
    <div>
      <div className="panel-title">AI 决策</div>

      <div className="strategy-row">
        <span className="ctl-label">策略</span>
        <select value={strategy} onChange={(e) => setStrategy(e.target.value)} title={lens}>
          {strategies.length === 0 && <option value="balanced">均衡</option>}
          {groups.map((g) => (
            <optgroup key={g} label={g === "大师" ? "大师人格" : "风格策略"}>
              {strategies
                .filter((s) => (s.group || "风格") === g)
                .map((s) => (
                  <option key={s.key} value={s.key}>{s.label}</option>
                ))}
            </optgroup>
          ))}
        </select>
      </div>
      {lens && <div className="strategy-lens">{lens}</div>}

      <div className="analyze-bar">
        <button
          className="primary"
          onClick={() => onAnalyze(strategy)}
          disabled={!symbol || busy}
        >
          {thinking ? "分析中…" : "快速分析 " + (symbol ? short(symbol) : "")}
        </button>
        <button
          className="ghost deep-btn"
          onClick={() => onDeepAnalyze(strategy, debate)}
          disabled={!symbol || busy}
          title="多智能体深度分析：技术面 / 基本面 / 消息面 → 多空综合"
        >
          {deepThinking ? "深度分析中…" : debate ? "🔬 深度分析·辩论" : "🔬 深度分析"}
        </button>
        <button
          className="ghost deep-btn"
          onClick={runPanel}
          disabled={!symbol || busy}
          title="投资大师 Panel：多位大师并行研判 → 加权投票共识（ai-hedge-fund 式）"
        >
          {panelBusy ? "大师投票中…" : "🎭 大师投票"}
        </button>
        <button
          className="ghost deep-btn"
          onClick={() => onBatchAnalyze(strategy)}
          disabled={batchRunning}
          title="并发分析全部自选股,完成后系统通知"
        >
          {batchRunning ? `批量中 ${batch?.done}/${batch?.total}` : "📊 批量分析"}
        </button>
      </div>

      <label className="ctl-label" style={{ display: "flex", alignItems: "center",
              gap: 6, margin: "4px 0 2px", cursor: "pointer" }}>
        <input type="checkbox" checked={debate}
               onChange={(e) => setDebate(e.target.checked)} />
        深度分析启用「多空辩论」（多头/空头研究员对辩 → 风险调整综合，更慢更深）
      </label>

      {batchRunning && (
        <div className="batch-bar">
          <div className="batch-fill"
               style={{ width: `${batch ? (batch.done / Math.max(1, batch.total)) * 100 : 0}%` }} />
          <span className="batch-label">批量分析 {batch?.done}/{batch?.total}…完成后通知</span>
        </div>
      )}

      {error && <div className="error-banner">{error}</div>}

      {busy && (
        <div className="thinking">
          <span className="spinner" />
          {deepThinking
            ? "多智能体协作中：技术面 · 基本面 · 消息面 → 综合决策…"
            : "模型推理中，正在生成结构化决策…"}
        </div>
      )}

      {panelErr && <div className="error-banner">{panelErr}</div>}

      {panel && (
        <div className="analyst-panel">
          <div className="ap-title">🎭 投资大师投票</div>
          <PersonaConsensusBar c={panel.consensus} />
          <details className="process-details">
            <summary>查看 {panel.panel.length} 位大师的逐一观点</summary>
            {panel.panel.map((p, i) => (
              <PersonaCard key={i} p={p} />
            ))}
          </details>
        </div>
      )}

      {deep && deep.analysts && deep.analysts.length > 0 && (
        <details className="analyst-panel process-details">
          <summary>
            AI 分析中间过程：分析师团队
            {deep.researchers ? " · 多空辩论" : ""}（点击展开预览）
          </summary>
          <div className="ap-title">分析师团队意见</div>
          {deep.analysts.map((a, i) => (
            <AnalystCard key={i} a={a} />
          ))}
          {deep.researchers && (
            <>
              <div className="ap-title">多空辩论</div>
              <ResearcherCard r={deep.researchers.bull} />
              <ResearcherCard r={deep.researchers.bear} />
            </>
          )}
        </details>
      )}

      {!busy && !decision && (
        <div className="empty">
          点击「快速分析」获取结构化决策，或「深度分析」启动多智能体协作研判。
        </div>
      )}

      {decision && (
        <div className="decision-card">
          <div className="dc-head">
            <span className={"action-badge " + decision.action}>
              {decision.action}
            </span>
            <Stars n={decision.conviction} />
            <ExportControls decision={decision} />
          </div>

          <div className="dc-body">
            <div className="dc-rationale">{decision.rationale}</div>

            <div className="dc-grid">
              <div className="dc-cell">
                <div className="k">入场区间</div>
                <div className="v mono">
                  {decision.entry_zone && decision.entry_zone.length
                    ? decision.entry_zone.map((n) => fmtNum(n)).join(" – ")
                    : "—"}
                </div>
              </div>
              <div className="dc-cell">
                <div className="k">止损</div>
                <div className="v mono down">{fmtNum(decision.stop_loss)}</div>
              </div>
              <div className="dc-cell">
                <div className="k">目标</div>
                <div className="v mono up">
                  {decision.take_profit && decision.take_profit.length
                    ? decision.take_profit.map((n) => fmtNum(n)).join(" / ")
                    : "—"}
                </div>
              </div>
            </div>

            {decision.key_risks && decision.key_risks.length > 0 && (
              <>
                <div className="k" style={{ fontSize: 10, color: "var(--text-faint)", textTransform: "uppercase", letterSpacing: "0.5px", marginBottom: 4 }}>
                  主要风险
                </div>
                <ul className="risk-list">
                  {decision.key_risks.map((r, i) => (
                    <li key={i}>{r}</li>
                  ))}
                </ul>
              </>
            )}

            {decision.ensemble && (
              <div
                className={
                  "ensemble-row " +
                  (decision.ensemble.agree ? "agree" : "disagree")
                }
              >
                <b>{decision.ensemble.agree ? "✓ 集成一致" : "⚠ 集成分歧"}</b>{" "}
                · {decision.ensemble.provider} 给出{" "}
                <b>{decision.ensemble.action}</b>（确信 {decision.ensemble.conviction}/5）
              </div>
            )}
          </div>

          <div className="dc-foot">
            {decision.strategy && (
              <span className="provider-pill strat-pill">
                {STRATEGY_LABEL[decision.strategy] ?? decision.strategy}
              </span>
            )}
            <span className="provider-pill">{decision.provider}</span>
            {decision.model && <span className="provider-pill">{decision.model}</span>}
            {!decision.data_freshness_ok && (
              <span className="delayed-pill">数据可能过期</span>
            )}
            <span style={{ marginLeft: "auto" }}>{fmtDateTime(decision.ts)}</span>
          </div>
        </div>
      )}
    </div>
  );
}

function short(symbol: string): string {
  return symbol.includes(":") ? symbol.split(":")[1] : symbol;
}
