import { useCallback, useEffect, useState } from "react";
import {
  NavPoint,
  Portfolio,
  RealizedTrade,
  api,
  changeClass,
  fmtDateTime,
  fmtNum,
} from "../lib/api";
import EquityChart from "./EquityChart";

const MKT: Record<string, string> = { US: "美股", HK: "港股", CN: "A股" };

function signCls(v: number | null | undefined) {
  if (v === null || v === undefined || v === 0) return "";
  return v > 0 ? "up" : "down";
}

export default function PortfolioPanel({ onAccountChange }: { onAccountChange?: () => void }) {
  const [pf, setPf] = useState<Portfolio | null>(null);
  const [hist, setHist] = useState<NavPoint[]>([]);
  const [realized, setRealized] = useState<RealizedTrade[]>([]);
  const [loading, setLoading] = useState(false);
  const [accounts, setAccounts] = useState<string[]>(["default"]);
  const [current, setCurrent] = useState("default");

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [p, h, r, a] = await Promise.all([
        api.portfolio(),
        api.portfolioHistory(),
        api.realized(),
        api.accounts(),
      ]);
      setPf(p);
      setHist(h.history);
      setRealized(r.trades);
      setAccounts(a.accounts);
      setCurrent(a.current);
    } catch {
      /* ignore */
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const switchAcct = async (name: string) => {
    if (name === "__new__") {
      const n = window.prompt("新建账户名称");
      if (!n) return;
      await api.createAccount(n.trim(), true);
    } else {
      await api.switchAccount(name);
    }
    await load();
    onAccountChange?.();
  };

  const resetAcct = async () => {
    if (!window.confirm(`确定清空账户「${current}」的全部持仓/订单/成交/净值记录?`)) return;
    await api.resetAccount();
    await load();
    onAccountChange?.();
  };

  const deleteAcct = async () => {
    if (current === "default") return;
    if (!window.confirm(`删除账户「${current}」(及其全部记录)?`)) return;
    await api.deleteAccount(current);
    await load();
    onAccountChange?.();
  };

  const r = pf?.realized;

  return (
    <div>
      <div className="panel-title">
        模拟组合
        <span style={{ marginLeft: "auto", display: "flex", gap: 6 }}>
          <a className="ghost mini exp-link" href={api.exportUrl("nav")}>导出净值</a>
          <a className="ghost mini exp-link" href={api.exportUrl("trades")}>导出成交</a>
          <button className="ghost mini" onClick={load}>{loading ? "…" : "刷新"}</button>
        </span>
      </div>

      <div className="acct-bar">
        <span className="acct-label">账户</span>
        <select value={current} onChange={(e) => switchAcct(e.target.value)}>
          {accounts.map((a) => <option key={a} value={a}>{a}</option>)}
          <option value="__new__">+ 新建账户…</option>
        </select>
        <button className="tiny" onClick={resetAcct} title="清空当前账户记录">重置</button>
        {current !== "default" && (
          <button className="tiny" onClick={deleteAcct} title="删除当前账户">删除</button>
        )}
      </div>

      {!pf || (pf.positions.length === 0 && (r?.closed_trades ?? 0) === 0) ? (
        <div className="empty">
          暂无模拟持仓。把模式切到「模拟」并让 AI/手动下单后，这里会显示净值曲线与盈亏。
        </div>
      ) : (
        <>
          {/* headline stats */}
          <div className="pf-stats">
            <div className="pf-stat">
              <div className="k">净值 NAV</div>
              <div className="v mono">{fmtNum(pf.nav)}</div>
            </div>
            <div className="pf-stat">
              <div className="k">浮动盈亏</div>
              <div className={"v mono " + signCls(pf.unrealized)}>
                {fmtNum(pf.unrealized)} ({fmtNum(pf.unrealized_pct)}%)
              </div>
            </div>
            <div className="pf-stat">
              <div className="k">已实现</div>
              <div className={"v mono " + signCls(r?.realized_pnl)}>
                {fmtNum(r?.realized_pnl ?? 0)}
              </div>
            </div>
            <div className="pf-stat">
              <div className="k">胜率</div>
              <div className="v mono">
                {r?.win_rate ?? 0}% ({r?.wins ?? 0}/{r?.closed_trades ?? 0})
              </div>
            </div>
          </div>

          {/* NAV equity curve */}
          {hist.length > 1 && (
            <div className="pf-chart">
              <EquityChart data={hist.map((p) => ({ ts: p.ts, equity: p.nav }))} />
            </div>
          )}

          {/* exposure by market */}
          {pf.exposure && Object.keys(pf.exposure).length > 0 && (
            <div className="pf-exposure">
              {Object.entries(pf.exposure).map(([m, v]) => (
                <div key={m} className="exp-row">
                  <span className={"mtag m-" + m}>{MKT[m] ?? m}</span>
                  <div className="exp-bar">
                    <div
                      className={"exp-fill m-" + m}
                      style={{ width: `${pf.exposure_pct?.[m] ?? 0}%` }}
                    />
                  </div>
                  <span className="mono exp-val">{fmtNum(v)}</span>
                </div>
              ))}
            </div>
          )}

          {/* positions */}
          {pf.positions.length > 0 && (
            <>
              <div className="sub-title">持仓</div>
              <table className="mini-table">
                <thead>
                  <tr>
                    <th>标的</th>
                    <th className="r">数量</th>
                    <th className="r">成本</th>
                    <th className="r">现价</th>
                    <th className="r">盈亏%</th>
                  </tr>
                </thead>
                <tbody>
                  {pf.positions.map((p) => (
                    <tr key={p.symbol}>
                      <td>
                        <span className={"mtag m-" + p.market}>
                          {p.symbol.split(":")[1]}
                        </span>
                      </td>
                      <td className="r mono">{fmtNum(p.qty)}</td>
                      <td className="r mono">{fmtNum(p.avg_cost)}</td>
                      <td className="r mono">{fmtNum(p.last)}</td>
                      <td className={"r mono " + changeClass(p.unrealized_pct)}>
                        {fmtNum(p.unrealized_pct)}%
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </>
          )}

          {/* realized trades */}
          {realized.length > 0 && (
            <>
              <div className="sub-title">已平仓 ({realized.length})</div>
              <table className="mini-table">
                <thead>
                  <tr>
                    <th>标的</th>
                    <th className="r">数量</th>
                    <th className="r">盈亏</th>
                    <th className="r">收益%</th>
                    <th className="r">时间</th>
                  </tr>
                </thead>
                <tbody>
                  {realized.slice(0, 30).map((t) => (
                    <tr key={t.id}>
                      <td className="mono">{t.symbol.split(":")[1]}</td>
                      <td className="r mono">{fmtNum(t.qty)}</td>
                      <td className={"r mono " + signCls(t.pnl)}>{fmtNum(t.pnl)}</td>
                      <td className={"r mono " + signCls(t.ret_pct)}>
                        {fmtNum(t.ret_pct)}%
                      </td>
                      <td className="r dim small">{fmtDateTime(t.ts)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </>
          )}
        </>
      )}
    </div>
  );
}
