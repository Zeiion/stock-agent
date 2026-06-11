import { useState } from "react";
import {
  BacktestResult,
  OptimizeResult,
  api,
  changeClass,
  fmtNum,
  fmtPct,
} from "../lib/api";
import EquityChart from "./EquityChart";

interface Props {
  symbol: string | null;
}

interface ParamSpec {
  key: string;
  label: string;
  default: number;
  step?: number;
}

const STRATEGIES: Record<string, { label: string; params: ParamSpec[] }> = {
  ma_cross: {
    label: "均线交叉 (MA Cross)",
    params: [
      { key: "fast", label: "快线", default: 5 },
      { key: "slow", label: "慢线", default: 20 },
    ],
  },
  rsi_reversion: {
    label: "RSI 反转",
    params: [
      { key: "n", label: "RSI 周期", default: 14 },
      { key: "lower", label: "买入阈值", default: 30 },
      { key: "upper", label: "卖出阈值", default: 70 },
    ],
  },
  macd: { label: "MACD 交叉", params: [] },
  boll_breakout: {
    label: "布林突破 (BOLL)",
    params: [
      { key: "n", label: "周期", default: 20 },
      { key: "k", label: "标准差倍数", default: 2, step: 0.5 },
    ],
  },
  kdj_cross: {
    label: "KDJ 交叉",
    params: [{ key: "n", label: "周期", default: 9 }],
  },
};

const STRAT_KEYS = Object.keys(STRATEGIES);
const DAY_OPTIONS = [180, 365, 730, 1095];

function defaultsFor(s: string): Record<string, number> {
  const next: Record<string, number> = {};
  for (const p of STRATEGIES[s].params) next[p.key] = p.default;
  return next;
}

export default function BacktestPanel({ symbol }: Props) {
  const [strategy, setStrategy] = useState("ma_cross");
  const [params, setParams] = useState<Record<string, number>>(defaultsFor("ma_cross"));
  const [days, setDays] = useState(365);
  const [busy, setBusy] = useState(false);
  const [optimizing, setOptimizing] = useState(false);
  const [result, setResult] = useState<BacktestResult | null>(null);
  const [opt, setOpt] = useState<OptimizeResult | null>(null);
  const [err, setErr] = useState<string | null>(null);

  const def = STRATEGIES[strategy];

  const switchStrategy = (s: string) => {
    setStrategy(s);
    setParams(defaultsFor(s));
    setOpt(null);
  };

  const run = async (p?: Record<string, number>) => {
    if (!symbol) return;
    const use = p ?? params;
    if (p) setParams(p);
    setBusy(true);
    setErr(null);
    try {
      setResult(await api.backtest({ symbol, strategy, params: use, days }));
    } catch (e) {
      setErr(String(e));
      setResult(null);
    } finally {
      setBusy(false);
    }
  };

  const optimize = async () => {
    if (!symbol) return;
    setOptimizing(true);
    setErr(null);
    try {
      const r = await api.optimizeBacktest({ symbol, strategy, days });
      setOpt(r);
    } catch (e) {
      setErr(String(e));
    } finally {
      setOptimizing(false);
    }
  };

  const s = result?.stats;

  return (
    <div>
      <div className="panel-title">回测 · Backtest</div>
      {!symbol && <div className="empty">选择标的后回测。</div>}

      {symbol && (
        <div className="form">
          {err && <div className="error-banner">{err}</div>}
          <div className="form-row">
            <label>策略</label>
            <select value={strategy} onChange={(e) => switchStrategy(e.target.value)}>
              {STRAT_KEYS.map((k) => (
                <option key={k} value={k}>
                  {STRATEGIES[k].label}
                </option>
              ))}
            </select>
          </div>

          {def.params.length > 0 && (
            <div className="param-grid">
              {def.params.map((p) => (
                <div className="param-field" key={p.key}>
                  <label>{p.label}</label>
                  <input
                    type="number"
                    step={p.step ?? 1}
                    value={params[p.key] ?? p.default}
                    onChange={(e) =>
                      setParams((prev) => ({ ...prev, [p.key]: Number(e.target.value) }))
                    }
                  />
                </div>
              ))}
            </div>
          )}

          <div className="form-row">
            <label>区间</label>
            <select value={days} onChange={(e) => setDays(Number(e.target.value))}>
              {DAY_OPTIONS.map((d) => (
                <option key={d} value={d}>
                  {d} 天
                </option>
              ))}
            </select>
            <button className="primary" onClick={() => run()} disabled={busy}>
              {busy ? "回测中…" : "运行"}
            </button>
            <button className="ghost" onClick={optimize} disabled={optimizing} title="网格参数寻优">
              {optimizing ? "寻优中…" : "🔧 寻优"}
            </button>
          </div>
        </div>
      )}

      {opt && opt.best && (
        <div className="opt-box">
          <div className="sub-title">
            参数寻优 · 测试 {opt.tested} 组,最优收益 {fmtPct(opt.best.total_return)}
          </div>
          <div className="opt-best">
            最优参数：
            <span className="mono">{JSON.stringify(opt.best.params)}</span>
            <button className="tiny" onClick={() => run(opt.best!.params)}>应用</button>
          </div>
          <table className="mini-table">
            <thead>
              <tr>
                <th>参数</th>
                <th className="r">收益</th>
                <th className="r">回撤</th>
                <th className="r">夏普</th>
                <th className="r">次数</th>
              </tr>
            </thead>
            <tbody>
              {opt.results.slice(0, 8).map((r, i) => (
                <tr key={i}>
                  <td className="mono small">{JSON.stringify(r.params)}</td>
                  <td className={"r mono " + changeClass(r.total_return)}>{fmtNum(r.total_return)}%</td>
                  <td className="r mono down">{fmtNum(r.max_drawdown)}%</td>
                  <td className="r mono">{fmtNum(r.sharpe, 2)}</td>
                  <td className="r mono">{r.num_trades}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {s && (
        <>
          <div className="stat-grid">
            <div className="stat-card">
              <div className="sc-label">策略收益</div>
              <div className={"sc-value mono " + changeClass(s.total_return)}>
                {fmtPct(s.total_return)}
              </div>
            </div>
            <div className="stat-card">
              <div className="sc-label">买入持有</div>
              <div className={"sc-value mono " + changeClass(s.buy_hold_return)}>
                {fmtPct(s.buy_hold_return)}
              </div>
            </div>
            <div className="stat-card">
              <div className="sc-label">最大回撤</div>
              <div className="sc-value mono down">{fmtPct(s.max_drawdown)}</div>
            </div>
            <div className="stat-card">
              <div className="sc-label">胜率</div>
              <div className="sc-value mono">{fmtNum(s.win_rate, 1)}%</div>
            </div>
            <div className="stat-card">
              <div className="sc-label">夏普</div>
              <div className="sc-value mono">{fmtNum(s.sharpe, 2)}</div>
            </div>
            <div className="stat-card">
              <div className="sc-label">交易次数</div>
              <div className="sc-value mono">{s.num_trades}</div>
            </div>
          </div>

          {result && result.equity_curve.length > 0 && (
            <div className="equity-box">
              <EquityChart data={result.equity_curve} />
            </div>
          )}
        </>
      )}
    </div>
  );
}
