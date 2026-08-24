"""SMTP integration facade.

``send_email_message`` was moved here from ``backend.legacy_runtime``; the
legacy runtime re-exports it for backward compatibility.  Notification
settings and the notification log still live on the legacy runtime, so they
are imported lazily inside the function to avoid a circular import at module
load time (this module is imported before the legacy runtime finishes loading).
"""

from __future__ import annotations

import smtplib
from email.message import EmailMessage
from typing import Optional, Tuple

from backend.core.config import HTTP_TIMEOUT_SECONDS
from backend.core.time import utc_now_iso
from backend.db.connection import db_execute


def send_email_message(subject: str, message: str) -> Tuple[bool, Optional[str]]:
    from backend import legacy_runtime as legacy

    settings = legacy.get_notification_settings()
    if not settings.get("email_enabled"):
        return True, "邮箱推送未启用，未发送测试邮件"

    smtp_host = str(settings.get("smtp_host") or "").strip()
    smtp_port = int(settings.get("smtp_port") or 465)
    smtp_username = str(settings.get("smtp_username") or "").strip()
    smtp_password = str(settings.get("smtp_password") or "")
    smtp_from = str(settings.get("smtp_from") or smtp_username).strip()
    smtp_to = str(settings.get("smtp_to") or "").strip()
    smtp_use_ssl = bool(settings.get("smtp_use_ssl"))
    if not smtp_host or not smtp_port or not smtp_username or not smtp_password or not smtp_to:
        return False, "邮箱 SMTP 配置不完整"

    recipients = [item.strip() for item in smtp_to.replace("，", ",").split(",") if item.strip()]
    email = EmailMessage()
    email["Subject"] = subject
    email["From"] = smtp_from
    email["To"] = ", ".join(recipients)
    email.set_content(message)

    try:
        if smtp_use_ssl:
            with smtplib.SMTP_SSL(smtp_host, smtp_port, timeout=HTTP_TIMEOUT_SECONDS) as smtp:
                smtp.login(smtp_username, smtp_password)
                smtp.send_message(email)
        else:
            with smtplib.SMTP(smtp_host, smtp_port, timeout=HTTP_TIMEOUT_SECONDS) as smtp:
                smtp.starttls()
                smtp.login(smtp_username, smtp_password)
                smtp.send_message(email)
    except Exception as exc:
        error = f"邮箱推送失败：{exc}"
        db_execute(
            "UPDATE notification_settings SET email_last_error = ?, updated_at = ? WHERE id = 1",
            (error, utc_now_iso()),
        )
        legacy.log_notification("email", "failed", smtp_to, message, error)
        return False, error

    sent_at = utc_now_iso()
    db_execute(
        """
        UPDATE notification_settings
        SET email_last_error = NULL, email_last_sent_at = ?, updated_at = ?
        WHERE id = 1
        """,
        (sent_at, sent_at),
    )
    legacy.log_notification("email", "success", smtp_to, message, None)
    return True, None


def send(subject: str, message: str):
    return send_email_message(subject, message)
