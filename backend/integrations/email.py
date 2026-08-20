"""SMTP notification delivery using only the Python standard library."""

from __future__ import annotations

import os
import smtplib
from email.message import EmailMessage

from backend.repositories.notifications import NotificationRepository


def _timeout_seconds() -> int:
    try:
        return max(1, min(120, int(os.getenv("UPSTREAM_HTTP_TIMEOUT") or "15")))
    except ValueError:
        return 15


def send(
    subject: str,
    message: str,
    repository: NotificationRepository | None = None,
) -> tuple[bool, str | None]:
    """Deliver an email and persist the same result fields as the legacy path."""
    notifications = repository or NotificationRepository()
    settings = notifications.settings()
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
            with smtplib.SMTP_SSL(smtp_host, smtp_port, timeout=_timeout_seconds()) as smtp:
                smtp.login(smtp_username, smtp_password)
                smtp.send_message(email)
        else:
            with smtplib.SMTP(smtp_host, smtp_port, timeout=_timeout_seconds()) as smtp:
                smtp.starttls()
                smtp.login(smtp_username, smtp_password)
                smtp.send_message(email)
    except Exception as exc:
        error = f"邮箱推送失败：{exc}"
        notifications.email_failed(smtp_to, message, error)
        return False, error

    notifications.email_sent(smtp_to, message)
    return True, None
