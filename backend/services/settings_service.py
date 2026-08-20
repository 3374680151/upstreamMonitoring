"""Application settings (reconcile mode) service."""
from __future__ import annotations

from typing import Any

from backend.core.errors import ValidationError
from backend.repositories.settings import SettingsRepository


RECONCILE_MODE_DISABLE = "disable"
RECONCILE_MODE_DELETE = "delete"
RECONCILE_MODES = {RECONCILE_MODE_DISABLE, RECONCILE_MODE_DELETE}
SETTING_RECONCILE_MODE = "main_site_reconcile_mode"


class SettingsService:
    def __init__(self, repository: SettingsRepository | None = None) -> None:
        self.repository = repository or SettingsRepository()

    def get(self) -> dict[str, Any]:
        return {
            "success": True,
            "data": {
                SETTING_RECONCILE_MODE: self._mode(),
            },
        }

    def update(self, patch: dict[str, Any]) -> dict[str, Any]:
        mode = str(patch.get(SETTING_RECONCILE_MODE) or "").strip().lower()
        if mode not in RECONCILE_MODES:
            raise ValidationError("reconcile mode 无效")
        self.repository.set(SETTING_RECONCILE_MODE, mode)
        return {
            "success": True,
            "data": {SETTING_RECONCILE_MODE: self._mode()},
        }

    def _mode(self) -> str:
        mode = self.repository.get(SETTING_RECONCILE_MODE, RECONCILE_MODE_DISABLE)
        return mode if mode in RECONCILE_MODES else RECONCILE_MODE_DISABLE
