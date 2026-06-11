import { useEffect, useState } from "react";
import { ConfigResp, api } from "../lib/api";

const LABELS: Record<string, string> = {
  notify_desktop: "桌面通知 (macOS)",
  bark_url: "Bark URL",
  ntfy_url: "ntfy URL",
  telegram_bot_token: "Telegram Bot Token",
  telegram_chat_id: "Telegram Chat ID",
  feishu_webhook: "飞书 Webhook",
  dingtalk_webhook: "钉钉 Webhook",
  wecom_webhook: "企业微信 Webhook",
  smtp_host: "SMTP 服务器",
  smtp_port: "SMTP 端口",
  smtp_user: "SMTP 用户",
  smtp_pass: "SMTP 密码",
  smtp_to: "收件邮箱",
  finnhub_api_key: "Finnhub API Key (美股实时)",
  anthropic_api_key: "Anthropic API Key",
  ai_provider: "AI 提供方",
  ai_ensemble: "双模型集成",
  claude_model: "Claude 模型",
  codex_model: "Codex 模型",
  poll_interval_s: "盘中轮询(秒)",
  poll_interval_offhours_s: "盘后轮询(秒)",
  trading_mode: "交易模式",
  require_human_approval: "下单需人工确认",
  max_position_value: "单仓上限市值",
  auto_ai_on_critical: "关键告警自动 AI 分析",
};

// which channel a notification field maps to (for the per-field test button)
const FIELD_CHANNEL: Record<string, string> = {
  notify_desktop: "desktop",
  bark_url: "bark",
  ntfy_url: "ntfy",
  telegram_bot_token: "telegram",
  feishu_webhook: "feishu",
  dingtalk_webhook: "dingtalk",
  wecom_webhook: "wecom",
  smtp_to: "email",
};

export default function ConfigModal({ onClose }: { onClose: () => void }) {
  const [cfg, setCfg] = useState<ConfigResp | null>(null);
  const [patch, setPatch] = useState<Record<string, unknown>>({});
  const [saving, setSaving] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  const [testing, setTesting] = useState<string | null>(null);

  useEffect(() => {
    api.getConfig().then(setCfg).catch(() => {});
  }, []);

  if (!cfg) return null;

  const isSecret = (f: string) => cfg.secret_fields.includes(f);
  const isBool = (f: string) => cfg.bool_fields.includes(f);

  const curVal = (f: string): string => {
    if (f in patch) return String(patch[f] ?? "");
    const v = cfg.fields[f];
    if (v && typeof v === "object") return ""; // secret: show placeholder only
    return v === null || v === undefined ? "" : String(v);
  };
  const boolVal = (f: string): boolean => {
    if (f in patch) return Boolean(patch[f]);
    return Boolean(cfg.fields[f]);
  };
  const secretHint = (f: string): string => {
    const v = cfg.fields[f] as { set: boolean; hint: string } | undefined;
    return v && v.set ? v.hint : "未设置";
  };

  const setField = (f: string, v: unknown) => setPatch((p) => ({ ...p, [f]: v }));

  const save = async () => {
    setSaving(true);
    setMsg(null);
    try {
      const next = await api.saveConfig(patch);
      setCfg(next);
      setPatch({});
      setMsg("已保存");
      setTimeout(() => setMsg(null), 2000);
    } catch (e) {
      setMsg("保存失败: " + e);
    } finally {
      setSaving(false);
    }
  };

  const test = async (channel: string) => {
    setTesting(channel);
    try {
      const r = await api.testChannel(channel);
      const ok = r[channel] === true;
      setMsg(`${channel} 测试：${ok ? "✅ 成功" : "❌ 失败/未配置"}`);
    } catch {
      setMsg(`${channel} 测试失败`);
    } finally {
      setTesting(null);
      setTimeout(() => setMsg(null), 3000);
    }
  };

  const renderField = (f: string) => {
    const ch = FIELD_CHANNEL[f];
    return (
      <div className="cfg-field" key={f}>
        <label>{LABELS[f] ?? f}</label>
        <div className="cfg-input-row">
          {isBool(f) ? (
            <label
              className={"toggle " + (boolVal(f) ? "on" : "")}
              onClick={() => setField(f, !boolVal(f))}
            >
              <span className="track"><span className="knob" /></span>
              {boolVal(f) ? "开" : "关"}
            </label>
          ) : f === "ai_provider" ? (
            <select value={curVal(f) || "auto"} onChange={(e) => setField(f, e.target.value)}>
              {["auto", "claude", "codex", "anthropic"].map((o) => (
                <option key={o} value={o}>{o}</option>
              ))}
            </select>
          ) : f === "trading_mode" ? (
            <select value={curVal(f) || "signal"} onChange={(e) => setField(f, e.target.value)}>
              <option value="signal">signal 仅信号</option>
              <option value="paper">paper 模拟盘</option>
            </select>
          ) : (
            <input
              type={f.includes("interval") || f.includes("port") || f.includes("max_position") ? "number" : "text"}
              value={curVal(f)}
              placeholder={isSecret(f) ? secretHint(f) : ""}
              onChange={(e) => setField(f, e.target.value)}
            />
          )}
          {ch && (
            <button className="tiny" disabled={testing === ch} onClick={() => test(ch)}>
              {testing === ch ? "…" : "测试"}
            </button>
          )}
        </div>
      </div>
    );
  };

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal cfg-modal" onClick={(e) => e.stopPropagation()}>
        <div className="modal-head">
          <span>⚙ 配置中心</span>
          <button className="ghost tiny" onClick={onClose}>✕</button>
        </div>
        <div className="modal-body">
          {Object.entries(cfg.groups).map(([group, fields]) => (
            <div key={group} className="cfg-group">
              <div className="cfg-group-title">{group}</div>
              {fields.map(renderField)}
            </div>
          ))}
        </div>
        <div className="modal-foot">
          {msg && <span className="cfg-msg">{msg}</span>}
          <button className="ghost" onClick={onClose}>关闭</button>
          <button className="primary" onClick={save} disabled={saving || Object.keys(patch).length === 0}>
            {saving ? "保存中…" : `保存${Object.keys(patch).length ? ` (${Object.keys(patch).length})` : ""}`}
          </button>
        </div>
      </div>
    </div>
  );
}
