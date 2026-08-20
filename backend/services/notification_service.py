"""Application service for notification settings and delivery."""

from __future__ import annotations

from typing import Any

from backend.core.errors import ValidationError
from backend.domain.notification_message import format_message, format_subject
from backend.integrations import email, wecom
from backend.repositories.notifications import NotificationRepository


class NotificationService:
    def __init__(self, repository: NotificationRepository | None = None) -> None:
        self.repository = repository or NotificationRepository()

    def settings_payload(self) -> dict[str, Any]:
        return self.repository.payload()

    def logs(self, limit: int = 30) -> list[dict[str, Any]]:
        return self.repository.logs(limit)

    def update(self, payload: dict[str, Any]) -> None:
        try:
            self.repository.update(payload)
        except (TypeError, ValueError) as exc:
            # Keep malformed notification settings at the domain boundary so
            # every HTTP caller receives the same JSON 400 envelope.
            raise ValidationError(str(exc) or "通知配置无效") from exc

    def update_settings(self, payload: dict[str, Any]) -> dict[str, Any]:
        self.update(payload)
        return {"success": True, "data": self.settings_payload()}

    def test_email(self, payload: dict[str, Any]) -> dict[str, Any]:
        if payload:
            self.update(payload)
        message = "这是一封上游分组倍率监控测试邮件。"
        ok, error_message = self.send_email("上游倍率监控邮箱测试", message)
        return {"success": ok, "message": error_message or "测试邮件已发送"}

    def test_wecom(self, payload: dict[str, Any]) -> dict[str, Any]:
        if payload:
            self.update(payload)
        message = "这是一条上游分组倍率监控测试消息。"
        ok, error_message = self.send_wecom("上游倍率监控企业微信测试", message)
        return {"success": ok, "message": error_message or "测试消息已发送"}

    def send_email(self, subject: str, message: str) -> tuple[bool, str | None]:
        return email.send(subject, message, repository=self.repository)

    def send_wecom(self, subject: str, message: str) -> tuple[bool, str | None]:
        return wecom.send(subject, message, repository=self.repository)

    def notify_changes(
        self, site: dict[str, Any], changes: list[dict[str, Any]], checked_at: str
    ) -> None:
        if not changes:
            return
        subject = format_subject(site, changes)
        message = format_message(site, changes, checked_at)
        self.send_email(subject, message)
        self.send_wecom(subject, message)
