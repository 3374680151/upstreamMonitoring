"""Application services for monitoring reads and checks."""

from __future__ import annotations

from typing import Any

from backend import legacy_runtime as legacy
from backend.repositories.changes import ChangeRepository


class MonitoringService:
    def __init__(self, changes: ChangeRepository | None = None) -> None:
        self.changes = changes or ChangeRepository()

    def overview(self) -> dict[str, Any]:
        return legacy.overview_payload()

    def list_sites(self):
        return legacy.list_sites_payload()

    def list_changes(self, limit: int = 100):
        return self.changes.list(limit)

    def list_site_changes(self, site_id: int, limit: int = 100):
        return self.changes.list_for_site(site_id, limit)

    def snapshots(self, site_id: int):
        return self.changes.snapshots_for_site(site_id)

    def account(self, site: dict[str, Any]):
        return legacy.build_site_account_payload(site)

    def check(self, site_id: int):
        return legacy.detect_site(site_id)
