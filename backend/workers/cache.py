"""Model-cache lifecycle facade."""

from backend.services.monitoring_service import (
    refresh_site_model_cache,
    schedule_model_cache_refresh,
    warm_model_cache,
)


class ModelCacheWorker:
    def warm(self) -> None:
        warm_model_cache()

    def refresh_site(self, site_id: int):
        return refresh_site_model_cache(site_id)

    def schedule_site(self, site_id: int) -> None:
        schedule_model_cache_refresh(site_id)
