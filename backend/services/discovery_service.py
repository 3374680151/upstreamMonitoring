"""Channel discovery/import service facade."""

from __future__ import annotations

from typing import Any

from backend import legacy_runtime as legacy


class DiscoveryService:
    def import_sites(self, admin_site: dict[str, Any], payload: dict[str, Any]):
        return legacy.import_discovered_sites(admin_site, payload)

    def links(self, site_id: int):
        return legacy.list_site_discovery_links(site_id)
