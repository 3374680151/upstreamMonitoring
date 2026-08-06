"""Snapshot/change read repository."""

from __future__ import annotations

from typing import Any

from backend import legacy_runtime as legacy


class ChangeRepository:
    def list(self, limit: int = 100) -> list[dict[str, Any]]:
        return legacy.list_changes(limit)

    def list_for_site(self, site_id: int, limit: int = 100) -> list[dict[str, Any]]:
        return legacy.list_site_changes(site_id, limit)

    def snapshots_for_site(self, site_id: int) -> list[dict[str, Any]]:
        return legacy.list_snapshots(site_id)
