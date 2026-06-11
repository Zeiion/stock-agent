"""Notifier — severity-routed dispatch over the configured push channels.

Reads the configured channels from `settings` (or an injected settings object)
and routes an Alert (or an ad-hoc title/body) to the right combination of
channels based on severity, falling through to the next configured channel when
a chosen one fails. Channel functions themselves are self-contained (they take
explicit params and never read settings) — see app.notify.channels.

Routing:
  critical : desktop (if on) + Bark(level=critical, sound=alarm)
             + the FIRST available of [telegram, feishu, dingtalk, wecom]
             as a redundant channel.
  normal   : desktop (if on) + FIRST available mobile channel
             (bark / ntfy / telegram / feishu / dingtalk / wecom).
  info     : email if configured, else desktop.

Desktop is always also attempted whenever notify_desktop is enabled.
"""
from __future__ import annotations

from typing import Any, Optional

from ..config import settings as _global_settings
from ..models import Alert, Severity
from . import channels


class Notifier:
    def __init__(self, settings_obj: Any = None) -> None:
        self.settings = settings_obj if settings_obj is not None else _global_settings

    # ---- introspection ---------------------------------------------------- #
    def enabled_channels(self) -> list[str]:
        """Return the list of channel names that are currently configured."""
        s = self.settings
        out: list[str] = []
        if getattr(s, "notify_desktop", False):
            out.append("desktop")
        if getattr(s, "bark_url", ""):
            out.append("bark")
        if getattr(s, "ntfy_url", ""):
            out.append("ntfy")
        if getattr(s, "telegram_bot_token", "") and getattr(s, "telegram_chat_id", ""):
            out.append("telegram")
        if getattr(s, "feishu_webhook", ""):
            out.append("feishu")
        if getattr(s, "dingtalk_webhook", ""):
            out.append("dingtalk")
        if getattr(s, "wecom_webhook", ""):
            out.append("wecom")
        if getattr(s, "smtp_host", "") and getattr(s, "smtp_to", ""):
            out.append("email")
        return out

    # ---- single-channel dispatch ----------------------------------------- #
    async def _send_one(self, name: str, title: str, body: str, *,
                        critical: bool = False) -> Optional[bool]:
        """Dispatch to one named channel. Returns the channel's bool result,
        or None if the channel is not configured."""
        s = self.settings
        if name == "desktop":
            if not getattr(s, "notify_desktop", False):
                return None
            return await channels.notify_desktop(title, body)
        if name == "bark":
            if not getattr(s, "bark_url", ""):
                return None
            if critical:
                return await channels.send_bark(s.bark_url, title, body,
                                                level="critical", sound="alarm")
            return await channels.send_bark(s.bark_url, title, body)
        if name == "ntfy":
            if not getattr(s, "ntfy_url", ""):
                return None
            return await channels.send_ntfy(s.ntfy_url, title, body,
                                            priority=5 if critical else 3)
        if name == "telegram":
            if not (getattr(s, "telegram_bot_token", "") and getattr(s, "telegram_chat_id", "")):
                return None
            return await channels.send_telegram(s.telegram_bot_token, s.telegram_chat_id,
                                                f"{title}\n{body}")
        if name == "feishu":
            if not getattr(s, "feishu_webhook", ""):
                return None
            return await channels.send_feishu(s.feishu_webhook, title, body)
        if name == "dingtalk":
            if not getattr(s, "dingtalk_webhook", ""):
                return None
            return await channels.send_dingtalk(s.dingtalk_webhook, title, body)
        if name == "wecom":
            if not getattr(s, "wecom_webhook", ""):
                return None
            return await channels.send_wecom(s.wecom_webhook, title, body)
        if name == "email":
            if not (getattr(s, "smtp_host", "") and getattr(s, "smtp_to", "")):
                return None
            return await channels.send_email(
                s.smtp_host, getattr(s, "smtp_port", 587),
                getattr(s, "smtp_user", ""), getattr(s, "smtp_pass", ""),
                s.smtp_to, title, body,
            )
        return None

    async def _send_first_available(self, names: list[str], title: str, body: str, *,
                                    results: dict[str, bool], critical: bool = False) -> bool:
        """Try each configured channel in order; stop at the first SUCCESS.
        Records every attempted channel's result. Returns True if one succeeded."""
        for name in names:
            res = await self._send_one(name, title, body, critical=critical)
            if res is None:
                continue  # not configured
            results[name] = res
            if res:
                return True  # delivered; don't spam the rest
        return False

    # ---- public API ------------------------------------------------------- #
    async def notify(self, alert: Alert) -> dict:
        """Route an Alert by its severity. Returns {channel: bool}."""
        title, body = self._format_alert(alert)
        sev = (alert.severity or "").lower()
        return await self._dispatch(sev, title, body)

    async def send_text(self, title: str, body: str, severity: str = "normal") -> dict:
        """Route an ad-hoc message by severity. Returns {channel: bool}."""
        return await self._dispatch((severity or "normal").lower(), title, body)

    # ---- routing core ----------------------------------------------------- #
    async def _dispatch(self, severity: str, title: str, body: str) -> dict:
        results: dict[str, bool] = {}

        if severity == Severity.CRITICAL.value:
            # desktop always (if on)
            d = await self._send_one("desktop", title, body)
            if d is not None:
                results["desktop"] = d
            # Bark critical (best-effort, even if other channels also fire)
            b = await self._send_one("bark", title, body, critical=True)
            if b is not None:
                results["bark"] = b
            # redundant mobile/IM channel: first available
            await self._send_first_available(
                ["telegram", "feishu", "dingtalk", "wecom"],
                title, body, results=results, critical=True,
            )
            # if literally nothing landed, fall back to desktop already attempted

        elif severity == Severity.INFO.value:
            e = await self._send_one("email", title, body)
            if e is not None:
                results["email"] = e
            if not results.get("email"):
                d = await self._send_one("desktop", title, body)
                if d is not None:
                    results["desktop"] = d

        else:  # normal (default)
            d = await self._send_one("desktop", title, body)
            if d is not None:
                results["desktop"] = d
            await self._send_first_available(
                ["bark", "ntfy", "telegram", "feishu", "dingtalk", "wecom"],
                title, body, results=results,
            )

        return results

    # ---- formatting ------------------------------------------------------- #
    @staticmethod
    def _format_alert(alert: Alert) -> tuple[str, str]:
        sev = (alert.severity or "").upper()
        prefix = {"CRITICAL": "🔴", "NORMAL": "🟡", "INFO": "🔵"}.get(sev, "🔔")
        title = f"{prefix} {alert.symbol} · {alert.rule_type}"
        body = alert.message or ""
        snap = alert.snapshot or {}
        # the rules engine nests the quote under snap["quote"] (fall back to flat)
        q = snap.get("quote") or snap
        last = q.get("last") or q.get("close")
        pct = q.get("change_pct")
        bits = []
        if last is not None:
            bits.append(f"last={last}")
        if pct is not None:
            try:
                bits.append(f"chg={float(pct):+.2f}%")
            except (TypeError, ValueError):
                pass
        if bits:
            body = (body + "\n" if body else "") + "  ".join(bits)
        return title, body


# module-level singleton
notifier = Notifier()
