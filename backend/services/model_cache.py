"""Model-cache state and model-health refresh orchestration.

The cache is process-local, matching the current deployment model.  Upstream
requests go through integration clients and response mapping lives in the
domain layer; this service owns only cache lifecycle and payload assembly.
"""

from __future__ import annotations

import json
import threading
import time
from typing import Any, Callable

from backend.core.time import utc_now_iso
from backend.domain.model_health import (
    merge_sub2api_group_models,
    parse_newapi_models_by_group,
    parse_sub2api_channel_models,
    parse_sub2api_monitor_models,
    site_groups_from_row,
    strip_sub2api_auth_context,
)
from backend.integrations.newapi import NewApiClient
from backend.integrations.transport import normalize_base_url
from backend.repositories.sites import SiteRepository
from backend.services.sub2api_auth_service import Sub2ApiSiteAuthService


MODEL_CACHE_TTL_SECONDS = 90
UPTIME_CACHE_TTL_SECONDS = 300


class ModelCacheStore:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._entries: dict[int, dict[str, Any]] = {}
        self._refreshing: set[int] = set()

    def invalidate(self, site_id: int) -> None:
        with self._lock:
            self._entries.pop(int(site_id), None)

    def put(self, site_id: int, payload: dict[str, Any]) -> None:
        copied = json.loads(json.dumps(payload, ensure_ascii=False))
        with self._lock:
            self._entries[int(site_id)] = {
                "payload": copied,
                "updated_monotonic": time.monotonic(),
            }

    def get(self, site_id: int) -> tuple[dict[str, Any] | None, float]:
        with self._lock:
            entry = self._entries.get(int(site_id))
            if not entry or not isinstance(entry.get("payload"), dict):
                return None, float("inf")
            age = time.monotonic() - float(entry.get("updated_monotonic") or 0)
            return json.loads(json.dumps(entry["payload"], ensure_ascii=False)), age

    def schedule(
        self,
        site_id: int,
        refresh: Callable[[int], tuple[int, dict[str, Any]]],
    ) -> bool:
        site_id = int(site_id)
        with self._lock:
            if site_id in self._refreshing:
                return False
            self._refreshing.add(site_id)

        def run() -> None:
            try:
                refresh(site_id)
            except Exception:
                pass
            finally:
                with self._lock:
                    self._refreshing.discard(site_id)

        threading.Thread(
            target=run,
            name=f"model-cache-{site_id}",
            daemon=True,
        ).start()
        return True


class NewApiUptimeStore:
    """Stale-while-refresh cache for NewAPI's public uptime feed.

    The cache key deliberately excludes tokens, cookies and browser session
    identifiers.  A successful refresh invalidates model payloads for sites
    sharing that public upstream URL, preserving the previous UI behavior.
    """

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._entries: dict[str, dict[str, Any]] = {}
        self._refreshing: set[str] = set()

    @staticmethod
    def _key(site: dict[str, Any]) -> str:
        try:
            site_id = int(site.get("id") or 0)
        except (TypeError, ValueError):
            site_id = 0
        try:
            expires = int(site.get("browser_access_expires_at") or 0)
        except (TypeError, ValueError):
            expires = 0
        return "|".join(
            (
                normalize_base_url(str(site.get("base_url") or "")),
                str(site_id),
                str(site.get("auth_mode") or "token").strip().lower(),
                str(expires // 60),
            )
        )

    @staticmethod
    def _copy(payload: dict[str, Any]) -> dict[str, Any]:
        return json.loads(json.dumps(payload, ensure_ascii=False))

    def get(
        self,
        site: dict[str, Any],
        fetch: Callable[[dict[str, Any]], tuple[bool, dict[str, Any], str | None]],
        on_success: Callable[[dict[str, Any]], None],
    ) -> tuple[dict[str, Any], str | None]:
        key = self._key(site)
        should_refresh = False
        with self._lock:
            entry = self._entries.get(key)
            age = (
                time.monotonic() - float(entry.get("updated_monotonic") or 0)
                if entry
                else float("inf")
            )
            if age >= UPTIME_CACHE_TTL_SECONDS and key not in self._refreshing:
                self._refreshing.add(key)
                should_refresh = True
            if entry and isinstance(entry.get("payload"), dict):
                cached_payload = self._copy(entry["payload"])
                cached_error = entry.get("error")
            else:
                cached_payload = None
                cached_error = None

        if should_refresh:
            site_snapshot = dict(site)

            def refresh() -> None:
                refreshed = False
                try:
                    ok, payload, error = fetch(site_snapshot)
                    if ok and isinstance(payload, dict) and payload.get("success"):
                        with self._lock:
                            self._entries[key] = {
                                "payload": self._copy(payload),
                                "updated_monotonic": time.monotonic(),
                                "error": None,
                            }
                        refreshed = True
                    elif error:
                        with self._lock:
                            previous = dict(self._entries.get(key) or {})
                            previous["error"] = error
                            self._entries[key] = previous
                finally:
                    with self._lock:
                        self._refreshing.discard(key)
                if refreshed:
                    try:
                        on_success(site_snapshot)
                    except Exception:
                        pass

            threading.Thread(
                target=refresh,
                name=f"newapi-uptime-{site.get('id') or 'site'}",
                daemon=True,
            ).start()

        if cached_payload is not None:
            return cached_payload, str(cached_error) if cached_error else None
        return {"success": True, "data": []}, "公开监控正在后台刷新"


class ModelCacheService:
    def __init__(
        self,
        store: ModelCacheStore | None = None,
        uptime: NewApiUptimeStore | None = None,
        newapi: NewApiClient | None = None,
        sub2api_auth: Sub2ApiSiteAuthService | None = None,
    ) -> None:
        self.store = store or default_store
        self.uptime = uptime or default_uptime_store
        self.newapi = newapi or NewApiClient()
        self.sub2api_auth = sub2api_auth or Sub2ApiSiteAuthService()

    def invalidate(self, site_id: int) -> None:
        self.store.invalidate(site_id)

    def get(self, site_id: int) -> tuple[dict[str, Any] | None, float]:
        return self.store.get(site_id)

    def attach_group_model_names(
        self, site_id: int, groups: dict[str, Any]
    ) -> None:
        """Enrich a check result from the existing cache without I/O."""
        if not groups:
            return
        cached, _age = self.get(site_id)
        if not isinstance(cached, dict):
            return
        models_by_group = cached.get("models_by_group")
        if not isinstance(models_by_group, dict):
            return
        for name, info in groups.items():
            if not isinstance(info, dict):
                continue
            entries = models_by_group.get(name)
            if not isinstance(entries, list):
                continue
            info["models"] = sorted(
                {
                    str(model.get("name") or "").strip()
                    for model in entries
                    if isinstance(model, dict) and str(model.get("name") or "").strip()
                }
            )

    def refresh(self, site_id: int) -> tuple[int, dict[str, Any]]:
        site = SiteRepository().get(int(site_id))
        if not site:
            return 404, {"success": False, "message": "site not found"}
        status, payload = self._build_payload(site)
        if status == 200 and isinstance(payload, dict) and payload.get("success"):
            self.store.put(site_id, payload)
        return status, payload

    def _build_payload(self, site: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        groups = site_groups_from_row(site)
        if not groups:
            return 409, {
                "success": False,
                "message": "请先检测站点，获取分组倍率后再查看模型",
            }
        platform = str(site.get("platform") or "newapi").strip().lower()
        if platform == "newapi":
            return self._build_newapi_payload(site, groups)
        return self._build_sub2api_payload(site, groups)

    def _build_newapi_payload(
        self, site: dict[str, Any], groups: dict[str, dict[str, Any]]
    ) -> tuple[int, dict[str, Any]]:
        pricing_ok, pricing, pricing_error = self.newapi.fetch_pricing_for_site(site)
        if not pricing_ok:
            return 502, {
                "success": False,
                "message": pricing_error or "读取 NewAPI 模型失败",
            }
        uptime, uptime_error = self.uptime.get(
            site,
            self.newapi.fetch_uptime_for_site,
            self._on_uptime_refresh,
        )
        models_by_group = parse_newapi_models_by_group(pricing, uptime, groups)
        pricing_data = pricing.get("data") if isinstance(pricing, dict) else []
        uptime_categories = uptime.get("data") if isinstance(uptime, dict) else []
        if not isinstance(pricing_data, list):
            pricing_data = []
        if not isinstance(uptime_categories, list):
            uptime_categories = []
        monitors_count = sum(
            len(category.get("monitors") or [])
            for category in uptime_categories
            if isinstance(category, dict)
        )
        return 200, {
            "success": True,
            "source": ["/api/pricing", "/api/uptime/status"],
            "fetched_at": utc_now_iso(),
            "models_by_group": models_by_group,
            "models_count": len(pricing_data),
            "monitors_count": monitors_count,
            "uptime_error": uptime_error,
        }

    def _build_sub2api_payload(
        self, site: dict[str, Any], groups: dict[str, dict[str, Any]]
    ) -> tuple[int, dict[str, Any]]:
        ok, raw_payload, error_message = self.sub2api_auth.fetch_models(site)
        payload = strip_sub2api_auth_context(raw_payload)
        if not ok:
            return 502, {
                "success": False,
                "message": error_message or "读取上游模型失败",
            }
        configured_models = parse_sub2api_channel_models(
            payload.get("channels") if isinstance(payload, dict) else [], groups
        )
        monitored_models, unmatched_models = parse_sub2api_monitor_models(
            payload.get("monitors") if isinstance(payload, dict) else {}, groups
        )
        models_by_group = merge_sub2api_group_models(
            configured_models, monitored_models
        )
        monitor_items = (
            payload.get("monitors", {}).get("items", [])
            if isinstance(payload, dict) and isinstance(payload.get("monitors"), dict)
            else []
        )
        return 200, {
            "success": True,
            "source": ["/api/v1/channels/available", "/api/v1/channel-monitors"],
            "fetched_at": utc_now_iso(),
            "models_by_group": models_by_group,
            "channels_count": len(payload.get("channels") or [])
            if isinstance(payload, dict)
            else 0,
            "monitors_count": len(monitor_items),
            "unmatched_models_count": len(unmatched_models),
            "channels_error": payload.get("channels_error")
            if isinstance(payload, dict)
            else None,
            "monitors_error": payload.get("monitors_error")
            if isinstance(payload, dict)
            else None,
        }

    def _on_uptime_refresh(self, site: dict[str, Any]) -> None:
        """Refresh model entries sharing a public upstream after uptime changes."""
        base_url = normalize_base_url(str(site.get("base_url") or ""))
        if not base_url:
            return
        for candidate in SiteRepository().list():
            if normalize_base_url(str(candidate.get("base_url") or "")) != base_url:
                continue
            try:
                candidate_id = int(candidate["id"])
            except (KeyError, TypeError, ValueError):
                continue
            self.invalidate(candidate_id)
            self.schedule(candidate_id)

    def schedule(self, site_id: int) -> bool:
        return self.store.schedule(site_id, self.refresh)

    def warm(self, site_ids: list[int]) -> None:
        for site_id in site_ids:
            self.schedule(site_id)


default_store = ModelCacheStore()
default_uptime_store = NewApiUptimeStore()
default_service = ModelCacheService(default_store)


__all__ = [
    "MODEL_CACHE_TTL_SECONDS",
    "ModelCacheService",
    "ModelCacheStore",
    "NewApiUptimeStore",
    "UPTIME_CACHE_TTL_SECONDS",
    "default_service",
    "default_store",
    "default_uptime_store",
]
