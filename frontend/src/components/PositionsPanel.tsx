import { useState } from "react";
import {
  PaperOrder,
  Position,
  api,
  changeClass,
  fmtNum,
  fmtTime,
} from "../lib/api";

interface Props {
  symbol: string | null;
  positions: Position[];
  orders: PaperOrder[];
  onChanged: () => void;
}

export default function PositionsPanel({
  symbol,
  positions,
  orders,
  onChanged,
}: Props) {
  const [side, setSide] = useState<"BUY" | "SELL">("BUY");
  const [qty, setQty] = useState("100");
  const [limit, setLimit] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const pending = orders.filter((o) => o.status === "pending");
  const recent = orders.slice(0, 12);

  const submit = async () => {
    if (!symbol) return;
    const q = Number(qty);
    if (!q || q <= 0) {
      setErr("数量无效");
      return;
    }
    setBusy(true);
    setErr(null);
    try {
      await api.submitOrder({
        symbol,
        side,
        qty: q,
        limit_price: limit ? Number(limit) : undefined,
      });
      onChanged();
    } catch (e) {
      setErr(String(e));
    } finally {
      setBusy(false);
    }
  };

  const act = async (id: number, approve: boolean) => {
    if (approve) await api.approveOrder(id);
    else await api.rejectOrder(id);
    onChanged();
  };

  return (
    <div>
      <div className="panel-title">持仓 · Positions</div>
      {positions.length === 0 ? (
        <div className="empty">暂无持仓。</div>
      ) : (
        <table className="grid">
          <thead>
            <tr>
              <th>标的</th>
              <th>数量</th>
              <th>成本</th>
              <th>现价</th>
              <th>盈亏</th>
            </tr>
          </thead>
          <tbody>
            {positions.map((p) => (
              <tr key={p.symbol}>
                <td>{shortSym(p.symbol)}</td>
                <td className="mono">{fmtNum(p.qty, 0)}</td>
                <td className="mono">{fmtNum(p.avg_cost)}</td>
                <td className="mono">{fmtNum(p.last)}</td>
                <td className={"mono " + changeClass(p.pnl)}>{fmtNum(p.pnl)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      <div className="section-divider" />

      <div className="panel-title">
        待审批订单
        {pending.length > 0 && (
          <span style={{ marginLeft: "auto", color: "var(--warn)" }}>
            {pending.length}
          </span>
        )}
      </div>
      {pending.length === 0 ? (
        <div className="empty" style={{ padding: "12px 0" }}>
          无待审批订单。
        </div>
      ) : (
        pending.map((o) => (
          <div key={o.id} className="rule-item">
            <div className="ri-main">
              <div className="ri-type">
                <span className={"side-tag " + o.side}>{o.side}</span>{" "}
                {shortSym(o.symbol)}{" "}
                <span className="mono" style={{ color: "var(--text-dim)" }}>
                  ×{fmtNum(o.qty, 0)}
                </span>
              </div>
              <div className="ri-params">
                {o.limit_price ? "限价 " + fmtNum(o.limit_price) : "市价"} · {o.source}
              </div>
            </div>
            <div className="order-actions">
              <button className="tiny primary" onClick={() => act(o.id, true)}>
                批准
              </button>
              <button className="tiny ghost" onClick={() => act(o.id, false)}>
                拒绝
              </button>
            </div>
          </div>
        ))
      )}

      <div className="section-divider" />

      <div className="panel-title">手动下单</div>
      {!symbol && <div className="empty">选择标的后下单。</div>}
      {symbol && (
        <div className="form">
          {err && <div className="error-banner">{err}</div>}
          <div className="form-row">
            <label>方向</label>
            <div className="seg" style={{ flex: 1 }}>
              <button
                className={side === "BUY" ? "active" : ""}
                onClick={() => setSide("BUY")}
                style={{ flex: 1 }}
              >
                买入
              </button>
              <button
                className={side === "SELL" ? "active" : ""}
                onClick={() => setSide("SELL")}
                style={{ flex: 1 }}
              >
                卖出
              </button>
            </div>
          </div>
          <div className="form-row">
            <label>数量</label>
            <input
              type="number"
              value={qty}
              onChange={(e) => setQty(e.target.value)}
            />
          </div>
          <div className="form-row">
            <label>限价</label>
            <input
              type="number"
              placeholder="留空 = 市价"
              value={limit}
              onChange={(e) => setLimit(e.target.value)}
            />
          </div>
          <div className="form-row">
            <label></label>
            <button className="primary" onClick={submit} disabled={busy} style={{ flex: 1 }}>
              提交模拟单
            </button>
          </div>
        </div>
      )}

      <div className="section-divider" />

      <div className="panel-title">近期订单</div>
      {recent.length === 0 ? (
        <div className="empty" style={{ padding: "12px 0" }}>
          无订单记录。
        </div>
      ) : (
        <table className="grid">
          <thead>
            <tr>
              <th>标的</th>
              <th>方向</th>
              <th>数量</th>
              <th>状态</th>
              <th>时间</th>
            </tr>
          </thead>
          <tbody>
            {recent.map((o) => (
              <tr key={o.id}>
                <td>{shortSym(o.symbol)}</td>
                <td className={"side-tag " + o.side}>{o.side}</td>
                <td className="mono">{fmtNum(o.qty, 0)}</td>
                <td>
                  <span className={"status-pill " + o.status}>{o.status}</span>
                </td>
                <td className="mono">{fmtTime(o.ts)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}

function shortSym(symbol: string): string {
  return symbol.includes(":") ? symbol.split(":")[1] : symbol;
}
