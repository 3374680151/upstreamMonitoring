"""Persistence for notification configuration and delivery history.

The notification tables predate the FastAPI migration and are initialized by
the shared schema bootstrap.  This repository deliberately keeps the existing
single-row settings model (``id = 1``) so current MySQL data remains usable.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from backend.db.connection import connection


def _now_iso() -> str:
    """Match the application's configured timestamp shape without legacy code."""
    timezone_name = os.getenv("APP_TIMEZONE") or os.getenv("TZ") or "Asia/Shanghai"
    try:
        current_timezone = ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError:
        current_timezone = timezone(timedelta(hours=8), "Asia/Shanghai")
    return datetime.now(current_timezone).isoformat(timespec="seconds")


def _rows(rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    return [dict(row) for row in rows]


class NotificationRepository:
    """All SQL for ``notification_settings`` and ``notification_logs``."""

    def _fetch_settings(self) -> dict[str, Any] | None:
        with connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute("SELECT * FROM notification_settings WHERE id = %s", (1,))
                row = cursor.fetchone()
        return dict(row) if row else None

    def _ensure_settings(self) -> dict[str, Any]:
        existing = self._fetch_settings()
        if existing is not None:
            return existing

        now = _now_iso()
        with connection() as conn:
            try:
                with conn.cursor() as cursor:
                    # INSERT IGNORE preserves the old race-safe singleton behavior.
                    cursor.execute(
                        """
                        INSERT IGNORE INTO notification_settings
                        (id, qq_enabled, qq_app_id, qq_client_secret, qq_group_openid,
                         email_enabled, smtp_host, smtp_port, smtp_username,
                         smtp_password, smtp_use_ssl, smtp_from, smtp_to,
                         created_at, updated_at)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                                %s, %s, %s)
                        """,
                        (1, 0, "", "", "", 0, "", 465, "", "", 1, "", "", now, now),
                    )
                conn.commit()
            except Exception:
                conn.rollback()
                raise
        return self._fetch_settings() or {}

    def settings(self) -> dict[str, Any]:
        return self._ensure_settings()

    def payload(self) -> dict[str, Any]:
        settings = self.settings()
        return {
            "wecom_enabled": bool(settings.get("wecom_enabled")),
            "wecom_webhook": settings.get("wecom_webhook") or "",
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

    def update(self, body: dict[str, Any]) -> None:
        """Validate and persist the full settings form while preserving secrets.

        The browser deliberately submits an empty SMTP password when it wants
        to retain the saved value.  Likewise, an empty Webhook retains the
        saved value so an enabled channel cannot accidentally lose its secret.
        """
        settings = self.settings()
        wecom_enabled = bool(body.get("wecom_enabled", False))
        wecom_webhook = str(body.get("wecom_webhook") or "").strip()
        email_enabled = bool(body.get("email_enabled", False))
        smtp_host = str(body.get("smtp_host") or "").strip()
        smtp_port = int(body.get("smtp_port") or 465)
        smtp_username = str(body.get("smtp_username") or "").strip()
        smtp_password = str(body.get("smtp_password") or "")
        smtp_use_ssl = bool(body.get("smtp_use_ssl", True))
        smtp_from = str(body.get("smtp_from") or "").strip()
        smtp_to = str(body.get("smtp_to") or "").strip()

        if email_enabled:
            if (
                not smtp_host
                or not smtp_port
                or not smtp_username
                or not (smtp_password or settings.get("smtp_password"))
                or not smtp_to
            ):
                raise ValueError("启用邮箱推送时需要填写 SMTP 服务器、端口、账号、密码和收件人")
            if not smtp_from:
                smtp_from = smtp_username
        if wecom_enabled and not (wecom_webhook or settings.get("wecom_webhook")):
            raise ValueError("启用企业微信推送时需要填写 Webhook 地址")

        fields = [
            "qq_enabled = 0",
            "wecom_enabled = %s",
            "email_enabled = %s",
            "wecom_webhook = %s",
            "smtp_host = %s",
            "smtp_port = %s",
            "smtp_username = %s",
            "smtp_use_ssl = %s",
            "smtp_from = %s",
            "smtp_to = %s",
            "updated_at = %s",
        ]
        params: list[Any] = [
            1 if wecom_enabled else 0,
            1 if email_enabled else 0,
            wecom_webhook if wecom_webhook else (settings.get("wecom_webhook") or ""),
            smtp_host,
            smtp_port,
            smtp_username,
            1 if smtp_use_ssl else 0,
            smtp_from,
            smtp_to,
            _now_iso(),
        ]
        if smtp_password:
            fields.append("smtp_password = %s")
            params.append(smtp_password)
        params.append(1)

        with connection() as conn:
            try:
                with conn.cursor() as cursor:
                    cursor.execute(
                        f"UPDATE notification_settings SET {', '.join(fields)} WHERE id = %s",
                        tuple(params),
                    )
                conn.commit()
            except Exception:
                conn.rollback()
                raise

    def logs(self, limit: int = 30) -> list[dict[str, Any]]:
        with connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    "SELECT * FROM notification_logs ORDER BY id DESC LIMIT %s",
                    (int(limit),),
                )
                return _rows(cursor.fetchall())

    def log(
        self,
        channel: str,
        status: str,
        target: str,
        message: str,
        error_message: str | None = None,
    ) -> None:
        with connection() as conn:
            try:
                with conn.cursor() as cursor:
                    cursor.execute(
                        """
                        INSERT INTO notification_logs
                        (channel, status, target, message, error_message, created_at)
                        VALUES (%s, %s, %s, %s, %s, %s)
                        """,
                        (channel, status, target, message, error_message, _now_iso()),
                    )
                conn.commit()
            except Exception:
                conn.rollback()
                raise

    def email_failed(self, target: str, message: str, error_message: str) -> None:
        self._mark_delivery("email", False, target, message, error_message)

    def email_sent(self, target: str, message: str) -> None:
        self._mark_delivery("email", True, target, message, None)

    def wecom_failed(self, target: str, message: str, error_message: str) -> None:
        self._mark_delivery("wecom", False, target, message, error_message)

    def wecom_sent(self, target: str, message: str) -> None:
        self._mark_delivery("wecom", True, target, message, None)

    def _mark_delivery(
        self,
        channel: str,
        succeeded: bool,
        target: str,
        message: str,
        error_message: str | None,
    ) -> None:
        if channel not in {"email", "wecom"}:
            raise ValueError("unsupported notification channel")

        now = _now_iso()
        error_column = f"{channel}_last_error"
        sent_column = f"{channel}_last_sent_at"
        with connection() as conn:
            try:
                with conn.cursor() as cursor:
                    if succeeded:
                        cursor.execute(
                            f"""
                            UPDATE notification_settings
                            SET {error_column} = NULL, {sent_column} = %s, updated_at = %s
                            WHERE id = %s
                            """,
                            (now, now, 1),
                        )
                    else:
                        cursor.execute(
                            f"""
                            UPDATE notification_settings
                            SET {error_column} = %s, updated_at = %s
                            WHERE id = %s
                            """,
                            (error_message, now, 1),
                        )
                    cursor.execute(
                        """
                        INSERT INTO notification_logs
                        (channel, status, target, message, error_message, created_at)
                        VALUES (%s, %s, %s, %s, %s, %s)
                        """,
                        (
                            channel,
                            "success" if succeeded else "failed",
                            target,
                            message,
                            error_message,
                            now,
                        ),
                    )
                conn.commit()
            except Exception:
                conn.rollback()
                raise
