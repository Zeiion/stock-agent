import { Alert, api, fmtTime } from "../lib/api";

interface Props {
  alerts: Alert[];
}

export default function AlertsPanel({ alerts }: Props) {
  return (
    <div>
      <div className="panel-title">
        提醒 · Alerts
        <span style={{ marginLeft: "auto", display: "flex", gap: 6, alignItems: "center" }}>
          <a className="ghost mini exp-link" href={api.exportUrl("alerts")}>导出</a>
          <span style={{ color: "var(--text-faint)" }}>{alerts.length}</span>
        </span>
      </div>

      {alerts.length === 0 && <div className="empty">暂无触发的提醒。</div>}

      {alerts.map((a, i) => (
        <div key={a.id ?? i} className={"alert-item " + a.severity}>
          <div className="ai-body">
            <div className="ai-msg">{a.message}</div>
            <div className="ai-meta">
              <span>{shortSym(a.symbol)}</span>
              <span className="mono">{a.rule_type}</span>
              <span className="mono" style={{ marginLeft: "auto" }}>
                {fmtTime(a.ts)}
              </span>
            </div>
          </div>
        </div>
      ))}
    </div>
  );
}

function shortSym(symbol: string): string {
  return symbol.includes(":") ? symbol.split(":")[1] : symbol;
}
