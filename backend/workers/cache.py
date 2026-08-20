"""Model-cache lifecycle facade."""

from backend.repositories.sites import SiteRepository
from backend.services.model_cache import ModelCacheService


class ModelCacheWorker:
    def __init__(self) -> None:
        self.sites = SiteRepository()
        self.cache = ModelCacheService()

    def warm(self) -> None:
        # Lifecycle scheduling is owned by this worker boundary; the cache
        # service performs the actual upstream reads asynchronously.
        for site in self.sites.list():
            if site.get("enabled"):
                self.schedule_site(int(site["id"]))

    def refresh_site(self, site_id: int):
        return self.cache.refresh(site_id)

    def schedule_site(self, site_id: int) -> None:
        self.cache.schedule(site_id)
