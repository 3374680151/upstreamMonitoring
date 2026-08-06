"""Notification persistence repository."""

from __future__ import annotations

from typing import Any

from backend import legacy_runtime as legacy


class NotificationRepository:
    def settings(self) -> dict[str, Any]:
        return legacy.get_notification_settings()

    def payload(self) -> dict[str, Any]:
        return legacy.notification_settings_payload()

    def logs(self, limit: int = 30) -> list[dict[str, Any]]:
        return legacy.db_query_all(
            "SELECT * FROM notification_logs ORDER BY id DESC LIMIT ?",
            (limit,),
        )
