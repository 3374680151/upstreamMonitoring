"""Persistence for application-level settings."""

from __future__ import annotations

from backend.db import execute, query_one, utc_now_iso


class SettingsRepository:
    def get(self, name: str, default: str = "") -> str:
        try:
            row = query_one("SELECT value FROM app_settings WHERE name = ?", (name,))
        except Exception:
            return default
        if not isinstance(row, dict) or row.get("value") is None:
            return default
        return str(row["value"])

    def set(self, name: str, value: str) -> None:
        execute(
            "INSERT INTO app_settings (name, value, updated_at) VALUES (?, ?, ?) "
            "ON DUPLICATE KEY UPDATE value = VALUES(value), updated_at = VALUES(updated_at)",
            (name, value, utc_now_iso()),
        )
