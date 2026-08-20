"""Enterprise WeChat Webhook delivery using only the Python standard library."""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any

from backend.repositories.notifications import NotificationRepository


def _timeout_seconds() -> int:
    try:
        return max(1, min(120, int(os.getenv("UPSTREAM_HTTP_TIMEOUT") or "15")))
    except ValueError:
        return 15


def _post_json(url: str, payload: dict[str, Any]) -> tuple[bool, Any, str | None]:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": "Upstream-Ratio-Watch/1.0",
        },
        method="POST",
    )
    # Notification webhooks use the OS network route, not ambient proxy vars.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(request, timeout=_timeout_seconds()) as response:
            body = response.read().decode("utf-8", errors="replace")
            return True, json.loads(body) if body else {}, None
    except urllib.error.HTTPError as exc:
        try:
            raw = exc.read().decode("utf-8", errors="replace")
        except Exception:
            raw = ""
        return False, {"status": exc.code, "raw": raw}, f"HTTP {exc.code}"
    except Exception as exc:
        return False, {"error": str(exc)}, str(exc)


def send(
    subject: str,
    message: str,
    repository: NotificationRepository | None = None,
) -> tuple[bool, str | None]:
    """Deliver a markdown Webhook message and persist the result."""
    notifications = repository or NotificationRepository()
    settings = notifications.settings()
    if not settings.get("wecom_enabled"):
        return True, "企业微信推送未启用，未发送消息"

    webhook = str(settings.get("wecom_webhook") or "").strip()
    if not webhook:
        return False, "企业微信 Webhook 未配置"

    ok, payload, error = _post_json(
        webhook,
        {
            "msgtype": "markdown",
            "markdown": {"content": f"**{subject}**\n\n{message}"},
        },
    )
    if not ok:
        error_text = error or "企业微信推送失败"
        notifications.wecom_failed(webhook, message, error_text)
        return False, error_text

    if isinstance(payload, dict) and payload.get("errcode") not in (None, 0):
        error_text = f"企业微信推送失败：{payload.get('errmsg') or payload.get('errcode')}"
        notifications.wecom_failed(webhook, message, error_text)
        return False, error_text

    notifications.wecom_sent(webhook, message)
    return True, None
