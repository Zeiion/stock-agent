import { useState } from "react";
import { IntradayResult, api, fmtNum } from "../lib/api";

const REC_CLS: Record<string, string> = {
  "做T": "bull", "观望": "neut", "不建议": "bear",
};

function short(symbol: string): string {
  return symbol.includes(":") ? symbol.split(":")[1] : symbol;
}

export default function DayTPanel({ symbol }: { symbol: string | null }) {
  const [res, setRes] = useState<IntradayResult | null>(null);
  const [busy, setBusy] = useState(false);
  const [ai, setAi] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  async function run() {
    if (!symbol) return;
    setBusy(true);
    setErr(null);
    try {
      setRes(await api.dayT(symbol, ai));
    } catch (e) {
      setErr(String(e));
    } finally {
      setBusy(false);
    }
  }

  const plan = res?.plan;
  return (
    <div>
      <div className="panel-title">做 T · 当日高低点预测</div>
      <div className="muted" style={{ fontSize: 11, marginBottom: 8 }}>
        多方法集成（Camarilla 枢轴 · ATR 投影 · 历史日内极值分位 · 波动率 Yang-Zhang/EWMA）
        预估今日高/低点，给高抛低吸挂单建议。仅供参考，非投资建议。
      </div>

      <div className="analyze-bar">
        <button className="primary" onClick={run} disabled={!symbol || busy}>
          {busy ? "预测中…" : "预测今日高低点 " + (symbol ? short(symbol) : "")}
        </button>
        <label className="ctl-label" style={{ display: "flex", alignItems: "center",
                gap: 6, cursor: "pointer" }}>
          <input type="checkbox" checked={ai} onChange={(e) => setAi(e.target.checked)} />
          AI 经验分析
        </label>
      </div>

      {err && <div className="error-banner">{err}</div>}
      {res && res.predicted_high == null && (
        <div className="empty">{res.note || "历史数据不足，无法预测。"}</div>
      )}

      {res && res.predicted_high != null && (
        <>
          <div className="decision-card" style={{ marginTop: 8 }}>
            <div className="dc-head">
              <span className="action-badge SELL">高 {fmtNum(res.predicted_high)}</span>
              <span className="action-badge BUY">低 {fmtNum(res.predicted_low)}</span>
              <span className="horizon">振幅 ≈{res.expected_range_pct}%</span>
              <span style={{ marginLeft: "auto", fontSize: 11, color: "var(--text-faint)" }}>
                置信 {res.confidence}/100 · 锚 {res.anchor_kind === "today_open" ? "今开"
                  : res.anchor_kind === "last" ? "现价" : "昨收"} {fmtNum(res.anchor)}
              </span>
            </div>
            {plan && (
              <div className="dc-body">
                <div className="dc-grid">
                  <div className="dc-cell">
                    <div className="k">低吸挂单</div>
                    <div className="v mono up">{fmtNum(plan.buy_limit)}</div>
                  </div>
                  <div className="dc-cell">
                    <div className="k">高抛挂单</div>
                    <div className="v mono down">{fmtNum(plan.sell_limit)}</div>
                  </div>
                  <div className="dc-cell">
                    <div className="k">价差 / 建议量</div>
                    <div className="v mono">
                      {plan.spread_pct}%{plan.suggested_qty > 0 ? ` · ${plan.suggested_qty}股` : ""}
                    </div>
                  </div>
                </div>
                {!plan.viable && (
                  <div className="delayed-pill" style={{ marginBottom: 6 }}>
                    振幅偏窄，做T性价比低
                  </div>
                )}
                <ul className="risk-list">
                  {plan.actions.map((a, i) => <li key={i}>{a}</li>)}
                </ul>
                {(plan.stop_below != null || plan.breakout_above != null) && (
                  <div className="muted" style={{ fontSize: 11 }}>
                    突破失效：升破 {fmtNum(plan.breakout_above)} / 跌破 {fmtNum(plan.stop_below)}
                  </div>
                )}
              </div>
            )}
          </div>

          {res.ai && (
            <div className="decision-card" style={{ marginTop: 8 }}>
              <div className="dc-head">
                <span className={"stance-badge " + (REC_CLS[res.ai.recommend] || "neut")}>
                  AI：{res.ai.recommend}
                </span>
                <span className="horizon">买 {fmtNum(res.ai.buy_limit)} / 卖 {fmtNum(res.ai.sell_limit)}</span>
                <span style={{ marginLeft: "auto", fontSize: 11 }}>{res.ai.provider}</span>
              </div>
              <div className="dc-body">
                <div className="dc-rationale">{res.ai.narrative}</div>
                {res.ai.risks.length > 0 && (
                  <ul className="risk-list">
                    {res.ai.risks.map((r, i) => <li key={i}>{r}</li>)}
                  </ul>
                )}
              </div>
            </div>
          )}

          <details className="analyst-panel process-details">
            <summary>各方法预测明细（{res.methods.length} 种方法 · 点击展开预览）</summary>
            <table className="mini-table">
              <thead>
                <tr><th>方法</th><th>高</th><th>低</th><th>说明</th></tr>
              </thead>
              <tbody>
                {res.methods.map((m, i) => (
                  <tr key={i}>
                    <td>{m.name}</td>
                    <td className="mono">{fmtNum(m.high)}</td>
                    <td className="mono">{fmtNum(m.low)}</td>
                    <td className="muted" style={{ fontSize: 11 }}>{m.note}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            {plan && plan.caveats.length > 0 && (
              <ul className="risk-list" style={{ marginTop: 8 }}>
                {plan.caveats.map((c, i) => <li key={i}>{c}</li>)}
              </ul>
            )}
          </details>
        </>
      )}

      {!res && !busy && (
        <div className="empty">选择标的后点击「预测今日高低点」。</div>
      )}
    </div>
  );
}
