"""Runtime configuration, loaded from environment / .env.

All settings have sane defaults so the app runs out of the box with NO API keys:
  - data: yfinance (all markets, delayed) + akshare (A-shares, near-real-time)
  - AI:   claude / codex CLI (uses your existing CLI auth; no API key needed)
  - push: macOS desktop notifications

Optional keys (Anthropic API, Finnhub, Bark, Telegram, Feishu...) unlock
real-time US data, faster AI, and mobile push.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

# Load .env if present (cheap manual parser; avoids a hard dependency)
def _load_dotenv() -> None:
    for candidate in (Path(__file__).resolve().parents[2] / ".env",
                      Path.cwd() / ".env"):
        if candidate.exists():
            for line in candidate.read_text().splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
            break


_load_dotenv()


def _b(name: str, default: bool) -> bool:
    return os.environ.get(name, str(default)).lower() in ("1", "true", "yes", "on")


def _i(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


def _f(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


PKG_ROOT = Path(__file__).resolve().parents[1]          # backend/
PROJECT_ROOT = PKG_ROOT.parent                          # stock-agent/


@dataclass
class Settings:
    # --- server ---
    host: str = os.environ.get("HOST", "127.0.0.1")
    port: int = _i("PORT", 8848)

    # --- storage ---
    db_path: str = os.environ.get(
        "DB_PATH", str(PROJECT_ROOT / "data" / "stockagent.db")
    )

    # --- data polling ---
    poll_interval_s: float = _f("POLL_INTERVAL_S", 5.0)      # in-hours watchlist poll
    poll_interval_offhours_s: float = _f("POLL_INTERVAL_OFFHOURS_S", 30.0)
    history_lookback_days: int = _i("HISTORY_LOOKBACK_DAYS", 200)

    # --- data sources ---
    finnhub_api_key: str = os.environ.get("FINNHUB_API_KEY", "")
    prefer_akshare_for_cn: bool = _b("PREFER_AKSHARE_FOR_CN", True)

    # --- AI brain ---
    # provider: "claude" | "codex" | "anthropic" | "auto"
    ai_provider: str = os.environ.get("AI_PROVIDER", "auto")
    ai_ensemble: bool = _b("AI_ENSEMBLE", False)            # cross-check w/ the other CLI
    anthropic_api_key: str = os.environ.get("ANTHROPIC_API_KEY", "")
    claude_model: str = os.environ.get("CLAUDE_MODEL", "claude-opus-4-8")
    codex_model: str = os.environ.get("CODEX_MODEL", "")    # "" = codex default
    ai_timeout_s: int = _i("AI_TIMEOUT_S", 180)
    ai_login_shell: bool = _b("AI_LOGIN_SHELL", True)       # spawn via `zsh -lc` (alias fix)

    # --- notifications ---
    notify_desktop: bool = _b("NOTIFY_DESKTOP", True)       # macOS Notification Center
    bark_url: str = os.environ.get("BARK_URL", "")          # https://api.day.app/<key>
    ntfy_url: str = os.environ.get("NTFY_URL", "")          # https://ntfy.sh/<topic>
    telegram_bot_token: str = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    telegram_chat_id: str = os.environ.get("TELEGRAM_CHAT_ID", "")
    feishu_webhook: str = os.environ.get("FEISHU_WEBHOOK", "")
    dingtalk_webhook: str = os.environ.get("DINGTALK_WEBHOOK", "")
    wecom_webhook: str = os.environ.get("WECOM_WEBHOOK", "")
    smtp_host: str = os.environ.get("SMTP_HOST", "")
    smtp_port: int = _i("SMTP_PORT", 587)
    smtp_user: str = os.environ.get("SMTP_USER", "")
    smtp_pass: str = os.environ.get("SMTP_PASS", "")
    smtp_to: str = os.environ.get("SMTP_TO", "")

    # --- trading (signal-only / paper by default) ---
    trading_mode: str = os.environ.get("TRADING_MODE", "signal")  # signal | paper
    require_human_approval: bool = _b("REQUIRE_HUMAN_APPROVAL", True)
    max_position_value: float = _f("MAX_POSITION_VALUE", 100000.0)

    # --- behaviour ---
    auto_ai_on_critical: bool = _b("AUTO_AI_ON_CRITICAL", True)
    cors_origins: list[str] = field(default_factory=lambda: os.environ.get(
        "CORS_ORIGINS", "http://localhost:8888,http://127.0.0.1:8888"
    ).split(","))

    # --- realtime (Finnhub WebSocket for US; needs finnhub key) ---
    realtime_enabled: bool = _b("REALTIME_ENABLED", True)

    # --- social / KOL sources ---
    x_kol_handles: str = os.environ.get(
        "X_KOL_HANDLES", "")          # comma list e.g. "CathieDWood,elonmusk"
    nitter_instance: str = os.environ.get("NITTER_INSTANCE", "https://nitter.net")

    # --- AI pre-market briefing (HH:MM local times, comma-separated) ---
    briefing_times: str = os.environ.get("BRIEFING_TIMES", "08:30,21:00")

    # --- broker for paper execution: "internal" | "alpaca" ---
    broker: str = os.environ.get("BROKER", "internal")
    alpaca_api_key: str = os.environ.get("ALPACA_API_KEY", "")
    alpaca_api_secret: str = os.environ.get("ALPACA_API_SECRET", "")

    # --- auth (OFF by default; when off the API is fully open) ---
    auth_enabled: bool = _b("AUTH_ENABLED", False)
    auth_accounts: str = os.environ.get("AUTH_ACCOUNTS", "")   # "user:pass,user2:pass2"
    auth_secret: str = os.environ.get("AUTH_SECRET", "")

    def ai_providers_available(self) -> list[str]:
        out = []
        if self.anthropic_api_key:
            out.append("anthropic")
        out.append("claude")   # CLI assumed present in this env
        out.append("codex")
        return out


settings = Settings()
