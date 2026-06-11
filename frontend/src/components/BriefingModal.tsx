import { useEffect, useState } from "react";
import { Briefing, api, fmtDateTime } from "../lib/api";

const ACT: Record<string, string> = {
  BUY: "买入", ADD: "加仓", WATCH: "关注", REDUCE: "减仓", SELL: "卖出", HOLD: "持有",
};

export default function BriefingModal({ onClose }: { onClose: () => void }) {
  const [b, setB] = useState<Briefing | null>(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    api.briefing().then(setB).catch(() => {});
  }, []);

  const generate = async () => {
    setLoading(true);
    try {
      setB(await api.generateBriefing());
    } catch {
      /* ignore */
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal cfg-modal" onClick={(e) => e.stopPropagation()}>
        <div className="modal-head">
          <span>📋 AI 盘前简报</span>
          <button className="ghost tiny" onClick={onClose}>✕</button>
        </div>
        <div className="modal-body">
          {!b || !b.generated_ts ? (
            <div className="empty">还没有简报,点击「生成简报」让 AI 汇总自选股。</div>
          ) : (
            <>
              <div className="brief-summary">{b.summary}</div>

              {b.movers && b.movers.length > 0 && (
                <>
                  <div className="cfg-group-title">异动标的</div>
                  {b.movers.map((m, i) => (
                    <div key={i} className="brief-row">
                      <span className="mono brief-sym">{m.symbol.split(":")[1]}</span>
                      <span className="brief-note">{m.note}</span>
                    </div>
                  ))}
                </>
              )}

              {b.opportunities && b.opportunities.length > 0 && (
                <>
                  <div className="cfg-group-title">机会</div>
                  {b.opportunities.map((o, i) => (
                    <div key={i} className="brief-row">
                      <span className={"action-badge sm " + o.action}>{ACT[o.action] ?? o.action}</span>
                      <span className="mono brief-sym">{o.symbol.split(":")[1]}</span>
                      <span className="brief-note">{o.reason}</span>
                    </div>
                  ))}
                </>
              )}

              {b.risks && b.risks.length > 0 && (
                <>
                  <div className="cfg-group-title">风险提示</div>
                  <ul className="risk-list">
                    {b.risks.map((r, i) => <li key={i}>{r}</li>)}
                  </ul>
                </>
              )}

              <div className="brief-foot">
                {b.provider} · {b.generated_ts ? fmtDateTime(b.generated_ts) : ""}
              </div>
            </>
          )}
        </div>
        <div className="modal-foot">
          <button className="ghost" onClick={onClose}>关闭</button>
          <button className="primary" onClick={generate} disabled={loading}>
            {loading ? "生成中…(约30秒)" : "生成简报"}
          </button>
        </div>
      </div>
    </div>
  );
}
