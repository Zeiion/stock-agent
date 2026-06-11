import { useState } from "react";
import { api, setToken } from "../lib/api";

export default function LoginModal({ onSuccess }: { onSuccess: () => void }) {
  const [u, setU] = useState("");
  const [p, setP] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const submit = async () => {
    if (!u || !p || busy) return;
    setBusy(true);
    setErr(null);
    try {
      const r = await api.login(u, p);
      if (r.token) {
        setToken(r.token);
        onSuccess();
      } else {
        setErr("登录失败");
      }
    } catch (e) {
      setErr(String(e).includes("401") ? "用户名或密码错误" : String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="modal-overlay">
      <div className="modal login-modal" onClick={(e) => e.stopPropagation()}>
        <div className="modal-head"><span>🔒 登录 Stock Agent</span></div>
        <div className="modal-body">
          <div className="cfg-field">
            <label>用户名</label>
            <input value={u} autoFocus onChange={(e) => setU(e.target.value)}
                   onKeyDown={(e) => e.key === "Enter" && submit()} />
          </div>
          <div className="cfg-field">
            <label>密码</label>
            <input type="password" value={p} onChange={(e) => setP(e.target.value)}
                   onKeyDown={(e) => e.key === "Enter" && submit()} />
          </div>
          {err && <div className="error-banner">{err}</div>}
        </div>
        <div className="modal-foot">
          <button className="primary" onClick={submit} disabled={busy} style={{ flex: 1 }}>
            {busy ? "登录中…" : "登录"}
          </button>
        </div>
      </div>
    </div>
  );
}
