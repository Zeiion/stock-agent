"""Self-contained async push channels.

Each function takes everything it needs as explicit parameters (it does NOT read
`settings`), returns True on success / False on any failure, and NEVER raises —
errors are caught and printed so a single bad channel can't crash the daemon.

HTTP channels use a short-lived httpx.AsyncClient (~10s timeout). Blocking work
(osascript, smtplib) is wrapped so it doesn't stall the event loop.
"""
from __future__ import annotations

import asyncio
import smtplib
from email.mime.text import MIMEText
from urllib.parse import quote

import httpx

_HTTP_TIMEOUT = 10.0


# --------------------------------------------------------------------------- #
# Desktop (macOS Notification Center)
# --------------------------------------------------------------------------- #
async def notify_desktop(title: str, body: str) -> bool:
    """Show a macOS desktop notification via `osascript`.

    Spawned as an asyncio subprocess so the event loop is never blocked.
    """
    try:
        # Escape double-quotes for the AppleScript string literals.
        safe_title = title.replace('"', '\\"')
        safe_body = body.replace('"', '\\"')
        script = f'display notification "{safe_body}" with title "{safe_title}"'
        proc = await asyncio.create_subprocess_exec(
            "osascript", "-e", script,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await proc.communicate()
        if proc.returncode != 0:
            print(f"[notify.desktop] osascript failed: {stderr.decode(errors='replace').strip()}")
            return False
        return True
    except Exception as e:
        print(f"[notify.desktop] error: {e}")
        return False


# --------------------------------------------------------------------------- #
# Bark (iOS) — https://github.com/Finb/Bark
# --------------------------------------------------------------------------- #
async def send_bark(base_url: str, title: str, body: str, *,
                    level: str = "active", sound: str = "") -> bool:
    """GET {base_url}/{title}/{body}?level=&sound=.

    `level="critical"` (with sound, e.g. "alarm") bypasses silent/DND for
    must-not-miss alerts.
    """
    try:
        base = base_url.rstrip("/")
        params: dict[str, str] = {}
        if level:
            params["level"] = level
        if sound:
            params["sound"] = sound
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
            # Title/body go in the path and MUST be percent-encoded ourselves —
            # httpx does NOT pre-encode path segments, so a raw newline raises
            # InvalidURL and '/'/'#' would corrupt the title/body split.
            url = f"{base}/{quote(title, safe='')}/{quote(body, safe='')}"
            resp = await client.get(url, params=params)
            resp.raise_for_status()
        return True
    except Exception as e:
        print(f"[notify.bark] error: {e}")
        return False


# --------------------------------------------------------------------------- #
# ntfy.sh
# --------------------------------------------------------------------------- #
async def send_ntfy(url: str, title: str, body: str, *, priority: int = 3) -> bool:
    """POST the message body to an ntfy topic URL, with Title/Priority headers.

    Priority: 1 (min) .. 5 (max); 3 is default.
    """
    try:
        headers = {
            "Title": title,
            "Priority": str(priority),
        }
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
            resp = await client.post(url, content=body.encode("utf-8"), headers=headers)
            resp.raise_for_status()
        return True
    except Exception as e:
        print(f"[notify.ntfy] error: {e}")
        return False


# --------------------------------------------------------------------------- #
# Telegram Bot API
# --------------------------------------------------------------------------- #
async def send_telegram(token: str, chat_id: str, text: str) -> bool:
    """POST to the Bot API sendMessage endpoint."""
    try:
        api = f"https://api.telegram.org/bot{token}/sendMessage"
        payload = {"chat_id": chat_id, "text": text}
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
            resp = await client.post(api, json=payload)
            resp.raise_for_status()
        return True
    except Exception as e:
        print(f"[notify.telegram] error: {e}")
        return False


# --------------------------------------------------------------------------- #
# Feishu / Lark custom bot webhook
# --------------------------------------------------------------------------- #
async def send_feishu(webhook: str, title: str, body: str) -> bool:
    """POST a text message to a Feishu custom-bot webhook."""
    try:
        payload = {
            "msg_type": "text",
            "content": {"text": f"{title}\n{body}"},
        }
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
            resp = await client.post(webhook, json=payload)
            resp.raise_for_status()
        return True
    except Exception as e:
        print(f"[notify.feishu] error: {e}")
        return False


# --------------------------------------------------------------------------- #
# DingTalk custom robot webhook
# --------------------------------------------------------------------------- #
async def send_dingtalk(webhook: str, title: str, body: str) -> bool:
    """POST a text message to a DingTalk custom-robot webhook."""
    try:
        payload = {
            "msgtype": "text",
            "text": {"content": f"{title}\n{body}"},
        }
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
            resp = await client.post(webhook, json=payload)
            resp.raise_for_status()
        return True
    except Exception as e:
        print(f"[notify.dingtalk] error: {e}")
        return False


# --------------------------------------------------------------------------- #
# WeCom (企业微信) group-bot webhook
# --------------------------------------------------------------------------- #
async def send_wecom(webhook: str, title: str, body: str) -> bool:
    """POST a text message to a WeCom group-robot webhook."""
    try:
        payload = {
            "msgtype": "text",
            "text": {"content": f"{title}\n{body}"},
        }
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
            resp = await client.post(webhook, json=payload)
            resp.raise_for_status()
        return True
    except Exception as e:
        print(f"[notify.wecom] error: {e}")
        return False


# --------------------------------------------------------------------------- #
# Email (SMTP + STARTTLS)
# --------------------------------------------------------------------------- #
async def send_email(host: str, port: int, user: str, password: str,
                     to: str, subject: str, body: str) -> bool:
    """Send a plain-text email via SMTP STARTTLS.

    Blocking smtplib work runs in a worker thread via asyncio.to_thread.
    """
    def _send() -> bool:
        try:
            msg = MIMEText(body, "plain", "utf-8")
            msg["Subject"] = subject
            msg["From"] = user
            msg["To"] = to
            with smtplib.SMTP(host, port, timeout=_HTTP_TIMEOUT) as server:
                server.ehlo()
                server.starttls()
                server.ehlo()
                if user and password:
                    server.login(user, password)
                recipients = [r.strip() for r in to.split(",") if r.strip()]
                server.sendmail(user, recipients, msg.as_string())
            return True
        except Exception as e:
            print(f"[notify.email] error: {e}")
            return False

    try:
        return await asyncio.to_thread(_send)
    except Exception as e:
        print(f"[notify.email] error: {e}")
        return False
