"""Notification service facade."""

from __future__ import annotations

from typing import Any

from backend import legacy_runtime as legacy
from backend.repositories.notifications import NotificationRepository


class NotificationService:
    def __init__(self, repository: NotificationRepository | None = None) -> None:
        self.repository = repository or NotificationRepository()

    def settings_payload(self) -> dict[str, Any]:
        return self.repository.payload()

    def logs(self, limit: int = 30):
        return self.repository.logs(limit)

    def update(self, payload: dict[str, Any]) -> None:
        legacy.update_notification_settings(payload)

    def send_email(self, subject: str, message: str):
        return legacy.send_email_message(subject, message)

    def send_wecom(self, subject: str, message: str):
        return legacy.send_wecom_message(subject, message)
