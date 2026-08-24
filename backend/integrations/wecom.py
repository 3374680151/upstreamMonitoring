"""Enterprise WeChat integration facade.

``send_wecom_message`` was moved here from ``backend.legacy_runtime``; the
legacy runtime re-exports it for backward compatibility.  Notification
settings and the notification log still live on the legacy runtime, so they
are imported lazily inside the function to avoid a circular import at module
load time (this module is imported before the legacy runtime finishes loading).
"""

from __future__ import annotations

from typing import Any, Optional, Tuple

from backend.core.time import utc_now_iso
from backend.db.connection import db_execute
from backend.integrations.http import request_json


def send_wecom_message(subject: str, message: str) -> Tuple[bool, Optional[str]]:
    from backend import legacy_runtime as legacy

    settings = legacy.get_notification_settings()
    if not settings.get("wecom_enabled"):
        return True, "企业微信推送未启用，未发送消息"

    webhook = str(settings.get("wecom_webhook") or "").strip()
    if not webhook:
        return False, "企业微信 Webhook 未配置"

    content = f"**{subject}**\n\n{message}"
    ok, payload, error = request_json(
        webhook,
        payload={
            "msgtype": "markdown",
            "markdown": {
                "content": content,
            },
        },
        method="POST",
    )
    if not ok:
        error_text = error or "企业微信推送失败"
        db_execute(
            "UPDATE notification_settings SET wecom_last_error = ?, updated_at = ? WHERE id = 1",
            (error_text, utc_now_iso()),
        )
        legacy.log_notification("wecom", "failed", webhook, message, error_text)
        return False, error_text

    if isinstance(payload, dict) and payload.get("errcode") not in (None, 0):
        error_text = f"企业微信推送失败：{payload.get('errmsg') or payload.get('errcode')}"
        db_execute(
            "UPDATE notification_settings SET wecom_last_error = ?, updated_at = ? WHERE id = 1",
            (error_text, utc_now_iso()),
        )
        legacy.log_notification("wecom", "failed", webhook, message, error_text)
        return False, error_text

    sent_at = utc_now_iso()
    db_execute(
        """
        UPDATE notification_settings
        SET wecom_last_error = NULL, wecom_last_sent_at = ?, updated_at = ?
        WHERE id = 1
        """,
        (sent_at, sent_at),
    )
    legacy.log_notification("wecom", "success", webhook, message, None)
    return True, None


def send(subject: str, message: str):
    return send_wecom_message(subject, message)
