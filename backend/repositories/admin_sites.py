"""Admin-site repository facade."""

from __future__ import annotations

from typing import Any

from backend import legacy_runtime as legacy


class AdminSiteRepository:
    def get(self, admin_site_id: int) -> dict[str, Any] | None:
        row, _error, _status = legacy.get_admin_site_or_404(admin_site_id)
        return row

    def list_payload(self) -> list[dict[str, Any]]:
        return legacy.list_admin_sites_payload()
