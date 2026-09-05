"""Notification repository: persistence for ``notification_settings`` / ``notification_logs``.

Functions moved out of ``backend.legacy_runtime``.  The legacy runtime
re-exports every public name below so existing ``legacy.*`` callers keep
working unchanged.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from backend.core.time import utc_now_iso
from backend.db.connection import db_execute, db_query_all, db_query_one


def get_notification_settings() -> Dict[str, Any]:
    row = db_query_one("SELECT * FROM notification_settings WHERE id = 1")
    if row:
        return row
    now = utc_now_iso()
    db_execute(
        """
        INSERT IGNORE INTO notification_settings
        (id, qq_enabled, qq_app_id, qq_client_secret, qq_group_openid, email_enabled, smtp_host, smtp_port, smtp_username, smtp_password, smtp_use_ssl, smtp_from, smtp_to, created_at, updated_at)
        VALUES (1, 0, '', '', '', 0, '', 465, '', '', 1, '', '', ?, ?)
        """,
        (now, now),
    )
    return db_query_one("SELECT * FROM notification_settings WHERE id = 1") or {}


def _mask_wecom_webhook(value: Any) -> str:
    """Mask the WeCom webhook echo: keep only the last 8 chars of the ``key``
    query param — the key is a push credential, equivalent to a password
    (audit P1-2)."""
    text = str(value or "").strip()
    if not text:
        return ""
    matched = re.search(r"([?&]key=)([^&]+)", text, flags=re.IGNORECASE)
    if not matched:
        return "****"
    key_value = matched.group(2)
    tail = key_value[-8:] if len(key_value) > 8 else "***"
    return f"{text[: matched.start()]}{matched.group(1)}***{tail}"


def notification_settings_payload() -> Dict[str, Any]:
    settings = get_notification_settings()
    return {
        "wecom_enabled": bool(settings.get("wecom_enabled")),
        "wecom_webhook_masked": _mask_wecom_webhook(settings.get("wecom_webhook")),
        "wecom_has_webhook": bool(settings.get("wecom_webhook")),
        "wecom_last_error": settings.get("wecom_last_error"),
        "wecom_last_sent_at": settings.get("wecom_last_sent_at"),
        "email_enabled": bool(settings.get("email_enabled")),
        "smtp_host": settings.get("smtp_host") or "",
        "smtp_port": int(settings.get("smtp_port") or 465),
        "smtp_username": settings.get("smtp_username") or "",
        "has_smtp_password": bool(settings.get("smtp_password")),
        "smtp_use_ssl": bool(settings.get("smtp_use_ssl")),
        "smtp_from": settings.get("smtp_from") or "",
        "smtp_to": settings.get("smtp_to") or "",
        "email_last_error": settings.get("email_last_error"),
        "email_last_sent_at": settings.get("email_last_sent_at"),
        "updated_at": settings.get("updated_at"),
    }


def update_notification_settings(body: Dict[str, Any]) -> None:
    settings = get_notification_settings()
    wecom_enabled = bool(body.get("wecom_enabled", False))
    # Blank webhook keeps the stored one (same pattern as smtp_password): the
    # masked GET payload can never be replayed verbatim by the client (P1-2).
    wecom_webhook = (
        str(body.get("wecom_webhook") or "").strip()
        or str(settings.get("wecom_webhook") or "")
    )
    email_enabled = bool(body.get("email_enabled", False))
    smtp_host = str(body.get("smtp_host") or "").strip()
    smtp_port = int(body.get("smtp_port") or 465)
    smtp_username = str(body.get("smtp_username") or "").strip()
    smtp_password = str(body.get("smtp_password") or "")
    smtp_use_ssl = bool(body.get("smtp_use_ssl", True))
    smtp_from = str(body.get("smtp_from") or "").strip()
    smtp_to = str(body.get("smtp_to") or "").strip()

    if email_enabled:
        if not smtp_host or not smtp_port or not smtp_username or not (smtp_password or settings.get("smtp_password")) or not smtp_to:
            raise ValueError("启用邮箱推送时需要填写 SMTP 服务器、端口、账号、密码和收件人")
        if not smtp_from:
            smtp_from = smtp_username
    if wecom_enabled and not (wecom_webhook or settings.get("wecom_webhook")):
        raise ValueError("启用企业微信推送时需要填写 Webhook 地址")

    fields = [
        "qq_enabled = 0",
        "wecom_enabled = ?",
        "email_enabled = ?",
        "wecom_webhook = ?",
        "smtp_host = ?",
        "smtp_port = ?",
        "smtp_username = ?",
        "smtp_use_ssl = ?",
        "smtp_from = ?",
        "smtp_to = ?",
        "updated_at = ?",
    ]
    params: List[Any] = [
        1 if wecom_enabled else 0,
        1 if email_enabled else 0,
        wecom_webhook if wecom_webhook else (settings.get("wecom_webhook") or ""),
        smtp_host,
        smtp_port,
        smtp_username,
        1 if smtp_use_ssl else 0,
        smtp_from,
        smtp_to,
        utc_now_iso(),
    ]
    if smtp_password:
        fields.append("smtp_password = ?")
        params.append(smtp_password)
    params.append(1)
    db_execute(f"UPDATE notification_settings SET {', '.join(fields)} WHERE id = ?", params)


def log_notification(channel: str, status: str, target: str, message: str, error_message: Optional[str] = None) -> None:
    db_execute(
        """
        INSERT INTO notification_logs (channel, status, target, message, error_message, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (channel, status, target, message, error_message, utc_now_iso()),
    )


class NotificationRepository:
    """Thin OO facade retained for callers that prefer object-style access."""

    def settings(self) -> Dict[str, Any]:
        return get_notification_settings()

    def payload(self) -> Dict[str, Any]:
        return notification_settings_payload()

    def logs(self, limit: int = 30) -> List[Dict[str, Any]]:
        return db_query_all(
            "SELECT * FROM notification_logs ORDER BY id DESC LIMIT ?",
            (limit,),
        )
