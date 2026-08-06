"""NewAPI integration facade.

The protocol-specific implementation remains centralized in the compatibility
runtime for this migration step; callers use this module instead of importing
transport code from routers.
"""

from __future__ import annotations

from typing import Any

from backend import legacy_runtime as legacy


class NewApiClient:
    def fetch_groups(self, base_url: str):
        return legacy.fetch_newapi_groups(base_url)

    def fetch_account(self, base_url: str, access_token: str, user_id: str = ""):
        return legacy.fetch_newapi_account(base_url, access_token, user_id)

    def fetch_pricing_for_site(self, site: dict[str, Any]):
        return legacy.fetch_newapi_pricing_for_site(site)

    def fetch_perf_summary_for_site(self, site: dict[str, Any], hours: float = 24):
        return legacy.fetch_newapi_perf_summary_for_site(site, hours=hours)

    def fetch_perf_detail_for_site(self, site: dict[str, Any], model: str, hours: float = 24, group: str = ""):
        return legacy.fetch_newapi_perf_detail_for_site(site, model_name=model, hours=hours, group=group)
