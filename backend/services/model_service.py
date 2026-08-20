"""Pricing, perf-metrics, and model catalog read service."""
from __future__ import annotations

from typing import Any

from backend.core.errors import NotFoundError, UpstreamError, ValidationError
from backend.integrations.newapi import NewApiClient, clamp_perf_hours
from backend.repositories.sites import SiteRepository
from backend.services.model_cache import MODEL_CACHE_TTL_SECONDS, ModelCacheService


class ModelService:
    def __init__(
        self,
        repository: SiteRepository | None = None,
        cache: ModelCacheService | None = None,
    ) -> None:
        self.repository = repository or SiteRepository()
        self.cache = cache or ModelCacheService()
        self.newapi = NewApiClient()

    def _site_or_error(self, site_id: int):
        site = self.repository.get(int(site_id))
        if not site:
            raise NotFoundError("site not found")
        return site

    def get_pricing(self, site_id: int) -> dict[str, Any]:
        site = self._site_or_error(site_id)
        if (site.get("platform") or "newapi") != "newapi":
            raise ValidationError("pricing 仅支持 NewAPI 站点")
        ok, payload, error_message = self.newapi.fetch_pricing_for_site(site)
        if not ok:
            raise UpstreamError(error_message or "读取 NewAPI pricing 失败", upstream=payload if isinstance(payload, dict) else None)
        if isinstance(payload, dict):
            payload = dict(payload)
            payload["site_id"] = site_id
            payload["base_url"] = site["base_url"]
            auth_mode = str(site.get("auth_mode") or "token").strip().lower()
            if auth_mode == "browser":
                payload["auth_used"] = bool(
                    site.get("browser_cookie")
                    or site.get("browser_refresh_cookie")
                    or site.get("browser_session_id")
                    or site.get("access_token")
                )
            else:
                payload["auth_used"] = bool(
                    site.get("access_token") and site.get("access_user_id")
                )
        return payload

    def get_perf_summary(
        self, site_id: int, hours: float = 24
    ) -> dict[str, Any]:
        site = self._site_or_error(site_id)
        if (site.get("platform") or "newapi") != "newapi":
            raise ValidationError("perf-metrics 仅支持 NewAPI 站点")
        hours = clamp_perf_hours(hours, 24)
        ok, payload, error_message = self.newapi.fetch_perf_summary_for_site(
            site, hours=hours
        )
        if not ok:
            raise UpstreamError(error_message or "读取 NewAPI perf summary 失败", upstream=payload if isinstance(payload, dict) else None)
        if isinstance(payload, dict):
            payload = dict(payload)
            payload["site_id"] = site_id
            payload["hours"] = hours
            payload["note"] = (
                "summary 为全站模型级汇总，不随 group 筛选变化；"
                "分组仅用于 pricing 过滤模型名单（与 NewAPI 前端列表一致）"
            )
        return payload

    def get_perf_detail(
        self,
        site_id: int,
        model: str,
        hours: float = 24,
        group: str = "",
    ) -> dict[str, Any]:
        site = self._site_or_error(site_id)
        if (site.get("platform") or "newapi") != "newapi":
            raise ValidationError("perf-metrics 仅支持 NewAPI 站点")
        ok, payload, error_message = self.newapi.fetch_perf_detail_for_site(
            site, model=model, hours=hours, group=group
        )
        if not ok:
            raise UpstreamError(error_message or "读取 NewAPI perf detail 失败", upstream=payload if isinstance(payload, dict) else None)
        return payload

    def get_models(self, site_id: int) -> dict[str, Any]:
        self._site_or_error(site_id)
        cached_payload, cache_age = self.cache.get(site_id)
        if cached_payload is not None:
            cached_payload["cache_hit"] = True
            cached_payload["cache_age_seconds"] = round(cache_age, 1)
            if cache_age >= MODEL_CACHE_TTL_SECONDS:
                self.cache.schedule(site_id)
                cached_payload["refreshing"] = True
            return cached_payload

        status, payload = self.cache.refresh(site_id)
        if status == 404:
            raise NotFoundError("site not found")
        if status >= 400:
            message = str(payload.get("message") or "读取模型失败") if isinstance(payload, dict) else "读取模型失败"
            if status == 409:
                raise ValidationError(message)
            raise UpstreamError(message, upstream=payload if isinstance(payload, dict) else None)
        payload["cache_hit"] = False
        return payload
