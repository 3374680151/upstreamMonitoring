"""Model-cache lifecycle facade."""

from backend import legacy_runtime as legacy


class ModelCacheWorker:
    def warm(self) -> None:
        legacy.warm_model_cache()

    def refresh_site(self, site_id: int):
        return legacy.refresh_site_model_cache(site_id)

    def schedule_site(self, site_id: int) -> None:
        legacy.schedule_model_cache_refresh(site_id)
