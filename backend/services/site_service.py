"""Site service facade."""

from __future__ import annotations

from typing import Any

from backend.repositories.sites import SiteRepository, get_site_or_404
from backend.services.monitoring_service import detect_site, get_site_model_cache


class SiteService:
    def __init__(self, repository: SiteRepository | None = None) -> None:
        self.repository = repository or SiteRepository()

    def get_or_404(self, site_id: int):
        return get_site_or_404(site_id)

    def check(self, site_id: int) -> dict[str, Any]:
        return detect_site(site_id)

    def model_cache(self, site_id: int):
        return get_site_model_cache(site_id)
