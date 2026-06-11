import { useCallback, useEffect, useState } from "react";
import { Decision, api, fmtDateTime, fmtNum } from "../lib/api";

const HORIZON: Record<string, string> = {
  intraday: "日内", swing: "波段", position: "中长线",
};
const STRATEGY_LABEL: Record<string, string> = {
  balanced: "均衡", value: "价值", momentum: "动量",
  swing: "波段", short_term: "短线", contrarian: "逆向",
};

export default function DecisionsPanel({ symbol }: { symbol: string | null }) {
  const [list, setList] = useState<Decision[]>([]);
  const [loading, setLoading] = useState(false);
  const [open, setOpen] = useState<number | null>(null);
  const [onlySymbol, setOnlySymbol] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      setList(await api.decisions(onlySymbol && symbol ? symbol : undefined, 100));
    } catch {
      /* ignore */
    } finally {
      setLoading(false);
    }
  }, [onlySymbol, symbol]);

  useEffect(() => {
    load();
  }, [load]);

  return (
    <div>
      <div className="panel-title">
        AI 分析记录
        <span style={{ marginLeft: "auto", display: "flex", gap: 6, alignItems: "center" }}>
          {symbol && (
            <label className="mini-toggle">
              <input type="checkbox" checked={onlySymbol}
                     onChange={(e) => setOnlySymbol(e.target.checked)} />
              仅当前
            </label>
          )}
          <a className="ghost mini exp-link" href={api.exportUrl("decisions")}>导出</a>
          <button className="ghost mini" onClick={load}>{loading ? "…" : "刷新"}</button>
        </span>
      </div>

      {list.length === 0 && (
        <div className="empty">还没有 AI 分析记录。点「分析」或「批量分析」后,所有结果都会记录在此。</div>
      )}

      <div className="dec-list">
        {list.map((d) => {
          const isOpen = open === d.id;
          return (
            <div key={d.id} className="dec-item">
              <div className="dec-head" onClick={() => setOpen(isOpen ? null : d.id ?? null)}>
                <span className={"action-badge sm " + d.action}>{d.action}</span>
                <span className="mono dec-sym">{d.symbol.split(":")[1]}</span>
                <span className="dec-conv" title={`确信 ${d.conviction}/5`}>
                  {"★".repeat(d.conviction)}
                </span>
                {d.strategy && (
                  <span className="dec-strat">{STRATEGY_LABEL[d.strategy] ?? d.strategy}</span>
                )}
                <span className="dec-prov">{d.provider}</span>
                <span className="dec-time dim small">{fmtDateTime(d.ts)}</span>
              </div>
              {isOpen && (
                <div className="dec-body">
                  <div className="dec-meta-row">
                    <span>{HORIZON[d.horizon] ?? d.horizon}</span>
                    {d.stop_loss != null && <span>止损 {fmtNum(d.stop_loss)}</span>}
                    {d.entry_zone && d.entry_zone.length > 0 && (
                      <span>入场 {d.entry_zone.map((n) => fmtNum(n)).join("–")}</span>
                    )}
                    {d.take_profit && d.take_profit.length > 0 && (
                      <span>目标 {d.take_profit.map((n) => fmtNum(n)).join("/")}</span>
                    )}
                    {typeof d.realized_return === "number" && (
                      <span className={d.realized_return >= 0 ? "up" : "down"}>
                        后续 {d.realized_return > 0 ? "+" : ""}{fmtNum(d.realized_return)}%
                      </span>
                    )}
                  </div>
                  <div className="dec-rationale">{d.rationale}</div>
                  {d.key_risks && d.key_risks.length > 0 && (
                    <ul className="risk-list">
                      {d.key_risks.map((r, i) => <li key={i}>{r}</li>)}
                    </ul>
                  )}
                </div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}
