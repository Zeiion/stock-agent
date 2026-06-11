import { useState } from "react";
import { Rule, RuleType, Severity, api } from "../lib/api";

interface Props {
  symbol: string | null;
  rules: Rule[];
  onChanged: () => void;
}

interface ParamSpec {
  key: string;
  label: string;
  default: number;
}

// Per-rule-type parameter schema -> drives the dynamic form.
const RULE_DEFS: Record<RuleType, { label: string; params: ParamSpec[] }> = {
  price_above: { label: "价格突破上方", params: [{ key: "price", label: "价格", default: 0 }] },
  price_below: { label: "价格跌破下方", params: [{ key: "price", label: "价格", default: 0 }] },
  pct_move: { label: "日内涨跌幅 ≥", params: [{ key: "pct", label: "% 幅度", default: 5 }] },
  rsi_above: { label: "RSI 高于", params: [{ key: "value", label: "RSI", default: 70 }] },
  rsi_below: { label: "RSI 低于", params: [{ key: "value", label: "RSI", default: 30 }] },
  ma_cross: {
    label: "均线金叉/死叉",
    params: [
      { key: "fast", label: "快线", default: 5 },
      { key: "slow", label: "慢线", default: 20 },
    ],
  },
  macd_cross: { label: "MACD 交叉", params: [] },
  kdj_cross: { label: "KDJ-J 阈值", params: [{ key: "level", label: "阈值", default: 80 }] },
  volume_spike: { label: "放量", params: [{ key: "mult", label: "倍数", default: 2 }] },
  stop_loss: { label: "止损价", params: [{ key: "price", label: "价格", default: 0 }] },
  take_profit: { label: "止盈价", params: [{ key: "price", label: "价格", default: 0 }] },
};

const RULE_TYPES = Object.keys(RULE_DEFS) as RuleType[];
const SEVERITIES: Severity[] = ["info", "normal", "critical"];

export default function RulesPanel({ symbol, rules, onChanged }: Props) {
  const [type, setType] = useState<RuleType>("price_above");
  const [params, setParams] = useState<Record<string, number>>({ price: 0 });
  const [severity, setSeverity] = useState<Severity>("normal");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const def = RULE_DEFS[type];

  const switchType = (t: RuleType) => {
    setType(t);
    const next: Record<string, number> = {};
    for (const p of RULE_DEFS[t].params) next[p.key] = p.default;
    setParams(next);
  };

  const submit = async () => {
    if (!symbol) return;
    setBusy(true);
    setErr(null);
    try {
      await api.addRule({ symbol, type, params, severity });
      onChanged();
    } catch (e) {
      setErr(String(e));
    } finally {
      setBusy(false);
    }
  };

  const toggle = async (r: Rule) => {
    await api.patchRule(r.id, { active: !r.active });
    onChanged();
  };

  const remove = async (r: Rule) => {
    await api.delRule(r.id);
    onChanged();
  };

  return (
    <div>
      <div className="panel-title">
        规则 · Rules
        {symbol && <span style={{ marginLeft: "auto" }}>{shortSym(symbol)}</span>}
      </div>

      {!symbol && <div className="empty">先选择一支标的来管理规则。</div>}

      {symbol &&
        rules.map((r) => (
          <div key={r.id} className="rule-item">
            <label
              className={"toggle " + (r.active ? "on" : "")}
              onClick={() => toggle(r)}
            >
              <span className="track">
                <span className="knob" />
              </span>
            </label>
            <div className="ri-main">
              <div className="ri-type">
                {RULE_DEFS[r.type as RuleType]?.label ?? r.type}
                <span
                  className={"status-pill " + r.severity}
                  style={{ marginLeft: 6, fontSize: 9 }}
                >
                  {r.severity}
                </span>
              </div>
              <div className="ri-params">
                {Object.entries(r.params)
                  .map(([k, v]) => `${k}=${v}`)
                  .join("  ") || "—"}
              </div>
            </div>
            <button className="tiny ghost down" onClick={() => remove(r)}>
              删除
            </button>
          </div>
        ))}

      {symbol && rules.length === 0 && (
        <div className="empty" style={{ padding: "14px 0" }}>
          该标的暂无规则。
        </div>
      )}

      {symbol && (
        <div className="form">
          {err && <div className="error-banner">{err}</div>}
          <div className="form-row">
            <label>类型</label>
            <select value={type} onChange={(e) => switchType(e.target.value as RuleType)}>
              {RULE_TYPES.map((t) => (
                <option key={t} value={t}>
                  {RULE_DEFS[t].label}
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
                    value={params[p.key] ?? p.default}
                    onChange={(e) =>
                      setParams((prev) => ({
                        ...prev,
                        [p.key]: Number(e.target.value),
                      }))
                    }
                  />
                </div>
              ))}
            </div>
          )}

          <div className="form-row">
            <label>级别</label>
            <select
              value={severity}
              onChange={(e) => setSeverity(e.target.value as Severity)}
            >
              {SEVERITIES.map((s) => (
                <option key={s} value={s}>
                  {s}
                </option>
              ))}
            </select>
            <button className="primary" onClick={submit} disabled={busy}>
              添加规则
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

function shortSym(symbol: string): string {
  return symbol.includes(":") ? symbol.split(":")[1] : symbol;
}
