"""Admin-site service facade."""

from __future__ import annotations

from typing import Any

from backend import legacy_runtime as legacy


class AdminSiteService:
    def list(self) -> list[dict[str, Any]]:
        return legacy.list_admin_sites_payload()

    def get(self, admin_site_id: int):
        return legacy.get_admin_site_or_404(admin_site_id)

    def test(self, payload: dict[str, Any]):
        return legacy.test_admin_site_connection(payload)
