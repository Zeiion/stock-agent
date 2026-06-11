import { useCallback, useEffect, useState } from "react";
import { TrackRecord, api, changeClass, fmtDateTime, fmtNum } from "../lib/api";

const ACTION_LABEL: Record<string, string> = {
  BUY: "买入",
  ADD: "加仓",
  SELL: "卖出",
  REDUCE: "减仓",
  HOLD: "持有",
};

const STRATEGY_LABEL: Record<string, string> = {
  balanced: "均衡", value: "价值投资", momentum: "动量趋势",
  swing: "波段技术", short_term: "短线", contrarian: "逆向",
};

export default function TrackRecordPanel() {
  const [tr, setTr] = useState<TrackRecord | null>(null);
  const [loading, setLoading] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      setTr(await api.trackRecord());
    } catch {
      /* ignore */
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  return (
    <div>
      <div className="panel-title">
        AI 战绩
        <span style={{ marginLeft: "auto", display: "flex", gap: 6 }}>
          <a className="ghost mini exp-link" href={api.exportUrl("decisions")}>导出决策</a>
          <button className="ghost mini" onClick={load}>{loading ? "…" : "刷新"}</button>
        </span>
      </div>

      {!tr || tr.scored === 0 ? (
        <div className="empty">
          还没有可评估的历史决策。AI 给出决策后，会用此后的真实价格走势回测其方向是否正确。
        </div>
      ) : (
        <>
          <div className="pf-stats">
            <div className="pf-stat">
              <div className="k">方向准确率</div>
              <div className="v mono">{tr.accuracy}%</div>
            </div>
            <div className="pf-stat">
              <div className="k">已评估</div>
              <div className="v mono">
                {tr.correct}/{tr.scored}
              </div>
            </div>
            <div className="pf-stat">
              <div className="k">买入信号 α</div>
              <div className={"v mono " + changeClass(tr.buy_signal_alpha)}>
                {fmtNum(tr.buy_signal_alpha)}%
              </div>
            </div>
            <div className="pf-stat">
              <div className="k">平均后续涨跌</div>
              <div className={"v mono " + changeClass(tr.avg_move)}>
                {fmtNum(tr.avg_move)}%
              </div>
            </div>
          </div>

          {/* by-action breakdown */}
          {Object.keys(tr.by_action).length > 0 && (
            <table className="mini-table">
              <thead>
                <tr>
                  <th>动作</th>
                  <th className="r">次数</th>
                  <th className="r">准确率</th>
                  <th className="r">平均涨跌</th>
                </tr>
              </thead>
              <tbody>
                {Object.entries(tr.by_action).map(([a, s]) => (
                  <tr key={a}>
                    <td>{ACTION_LABEL[a] ?? a}</td>
                    <td className="r mono">{s.count}</td>
                    <td className="r mono">{s.accuracy}%</td>
                    <td className={"r mono " + changeClass(s.avg_move)}>
                      {fmtNum(s.avg_move)}%
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}

          {tr.by_strategy && Object.keys(tr.by_strategy).length > 0 && (
            <>
              <div className="sub-title">按策略准确率</div>
              <table className="mini-table">
                <thead>
                  <tr>
                    <th>策略</th>
                    <th className="r">次数</th>
                    <th className="r">准确率</th>
                    <th className="r">平均涨跌</th>
                  </tr>
                </thead>
                <tbody>
                  {Object.entries(tr.by_strategy).map(([s, st]) => (
                    <tr key={s}>
                      <td>{STRATEGY_LABEL[s] ?? s}</td>
                      <td className="r mono">{st.count}</td>
                      <td className="r mono">{st.accuracy}%</td>
                      <td className={"r mono " + changeClass(st.avg_move)}>
                        {fmtNum(st.avg_move)}%
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </>
          )}

          <div className="sub-title">最近决策的后续表现</div>
          <div className="track-list">
            {tr.recent.map((d, i) => (
              <div key={i} className="track-item">
                <span className={"track-dot " + (d.correct ? "ok" : "bad")} />
                <span className="mono ti-sym">{d.symbol.split(":")[1]}</span>
                <span className={"action-badge sm " + d.action}>
                  {ACTION_LABEL[d.action] ?? d.action}
                </span>
                <span className={"mono ti-move " + changeClass(d.move_pct)}>
                  {d.move_pct > 0 ? "+" : ""}
                  {fmtNum(d.move_pct)}%
                </span>
                <span className="ti-time dim small">{fmtDateTime(d.ts)}</span>
              </div>
            ))}
          </div>
        </>
      )}
    </div>
  );
}
