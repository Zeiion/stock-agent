"""Runtime configuration store.

Lets the UI change notification channels, API keys, AI model, and risk params at
runtime without editing .env. Overrides are applied to the `settings` singleton
(which the daemon / notifier / brain read live) AND persisted to the SQLite `kv`
table so they survive restarts. Secrets are masked when read back.
"""
from __future__ import annotations

from typing import Any

from . import db
from .config import settings

_KV_KEY = "config_overrides"

# field -> python type for coercion
BOOL = bool
INTF = int
FLOATF = float
STR = str

WHITELIST: dict[str, type] = {
    # notifications
    "notify_desktop": BOOL,
    "bark_url": STR, "ntfy_url": STR,
    "telegram_bot_token": STR, "telegram_chat_id": STR,
    "feishu_webhook": STR, "dingtalk_webhook": STR, "wecom_webhook": STR,
    "smtp_host": STR, "smtp_port": INTF, "smtp_user": STR,
    "smtp_pass": STR, "smtp_to": STR,
    # data / AI
    "finnhub_api_key": STR, "anthropic_api_key": STR,
    "claude_model": STR, "codex_model": STR,
    "ai_provider": STR, "ai_ensemble": BOOL,
    "poll_interval_s": FLOATF, "poll_interval_offhours_s": FLOATF,
    # trading / risk
    "trading_mode": STR, "require_human_approval": BOOL,
    "max_position_value": FLOATF, "auto_ai_on_critical": BOOL,
    # social
    "x_kol_handles": STR, "nitter_instance": STR,
    # realtime / briefing / broker
    "realtime_enabled": BOOL, "briefing_times": STR, "broker": STR,
    "alpaca_api_key": STR, "alpaca_api_secret": STR,
    # auth
    "auth_enabled": BOOL, "auth_accounts": STR, "auth_secret": STR,
}

# fields whose value must never be returned in full
SECRET = {
    "bark_url", "ntfy_url", "telegram_bot_token", "feishu_webhook",
    "dingtalk_webhook", "wecom_webhook", "smtp_pass",
    "finnhub_api_key", "anthropic_api_key",
    "alpaca_api_key", "alpaca_api_secret", "auth_accounts", "auth_secret",
}


def _coerce(field: str, value: Any) -> Any:
    t = WHITELIST[field]
    if t is BOOL:
        if isinstance(value, str):
            return value.strip().lower() in ("1", "true", "yes", "on")
        return bool(value)
    if t is INTF:
        return int(value)
    if t is FLOATF:
        return float(value)
    return "" if value is None else str(value)


def _mask(field: str, value: Any) -> Any:
    if field in SECRET:
        s = str(value or "")
        return {"set": bool(s), "hint": ("•••" + s[-4:]) if len(s) >= 4 else ("•••" if s else "")}
    return value


def apply_overrides() -> None:
    """Apply persisted overrides onto `settings` at startup."""
    ov = db.kv_get(_KV_KEY, {}) or {}
    for k, v in ov.items():
        if k in WHITELIST:
            try:
                setattr(settings, k, _coerce(k, v))
            except Exception as e:
                print(f"[config] apply {k} failed: {e}")
    _reflect_ai()


def _reflect_ai() -> None:
    try:
        from .ai.brain import brain
        brain.set_provider(settings.ai_provider)
        brain.set_ensemble(settings.ai_ensemble)
    except Exception:
        pass


def update(patch: dict) -> dict:
    ov = db.kv_get(_KV_KEY, {}) or {}
    for k, v in patch.items():
        if k not in WHITELIST:
            continue
        try:
            val = _coerce(k, v)
        except Exception as e:
            print(f"[config] coerce {k} failed: {e}")
            continue
        setattr(settings, k, val)
        ov[k] = val
    db.kv_set(_KV_KEY, ov)
    _reflect_ai()
    return effective()


def effective() -> dict[str, Any]:
    """Current effective config (secrets masked) grouped for the UI."""
    fields = {f: _mask(f, getattr(settings, f, None)) for f in WHITELIST}
    return {
        "fields": fields,
        "groups": {
            "通知": ["notify_desktop", "bark_url", "ntfy_url", "telegram_bot_token",
                     "telegram_chat_id", "feishu_webhook", "dingtalk_webhook",
                     "wecom_webhook", "smtp_host", "smtp_port", "smtp_user",
                     "smtp_pass", "smtp_to"],
            "数据与AI": ["finnhub_api_key", "anthropic_api_key", "ai_provider",
                         "ai_ensemble", "claude_model", "codex_model",
                         "poll_interval_s", "poll_interval_offhours_s",
                         "realtime_enabled", "briefing_times",
                         "x_kol_handles", "nitter_instance"],
            "交易与风控": ["trading_mode", "require_human_approval",
                          "max_position_value", "auto_ai_on_critical",
                          "broker", "alpaca_api_key", "alpaca_api_secret"],
            "访问鉴权": ["auth_enabled", "auth_accounts", "auth_secret"],
        },
        "secret_fields": sorted(SECRET),
        "bool_fields": sorted(f for f, t in WHITELIST.items() if t is BOOL),
        "channels": ["desktop", "bark", "ntfy", "telegram", "feishu",
                     "dingtalk", "wecom", "email"],
    }
