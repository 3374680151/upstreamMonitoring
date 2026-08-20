"""FastAPI-native main-site synchronisation service.

The service coordinates upstream reads and delegates every local write to
``SyncRepository``. A sync only reaches the repository transaction after both
the complete channel list and complete group map have been read and validated.
"""

from __future__ import annotations

from typing import Any, Optional
from urllib.parse import urlparse

from backend.integrations.clients import NewApiClient, Sub2ApiClient
from backend.integrations.newapi_admin import aggregate_channel_candidates
from backend.integrations.platform_probe import detect_upstream_platforms
from backend.integrations.transport import normalize_base_url
from backend.core.sanitize import sanitize_error_text
from backend.db import transaction
from backend.repositories.sync import SyncRepository
from backend.services.settings_service import RECONCILE_MODE_DISABLE


_SECRET_FIELDS = {
    "access_token", "browser_access_token", "browser_cookie",
    "browser_refresh_cookie", "channel_key", "key", "login_password",
    "password", "refresh_token", "secret", "client_secret", "security_proof",
    "token",
}


def _safe_value(value: Any, field_name: str = "", depth: int = 0) -> Any:
    name = str(field_name or "").strip().lower()
    if (
        name in _SECRET_FIELDS
        or name.endswith("_token")
        or name.endswith("_password")
        or name.endswith("_cookie")
        or name.endswith("_secret")
    ):
        return None
    if depth > 8:
        return str(value)
    if isinstance(value, dict):
        return {
            str(key): _safe_value(raw, str(key), depth + 1)
            for key, raw in value.items()
            if str(key).strip().lower() not in _SECRET_FIELDS
            and not str(key).strip().lower().endswith(("_token", "_password", "_cookie", "_secret"))
        }
    if isinstance(value, list):
        return [_safe_value(item, "", depth + 1) for item in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def _normalize_base_url(value: Any) -> tuple[str, Optional[str]]:
    normalized = normalize_base_url(str(value or ""))
    if not normalized:
        return "", "base_url required"
    try:
        parsed = urlparse(normalized)
        parsed.port
    except (TypeError, ValueError):
        return "", "base_url invalid"
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.netloc or not parsed.hostname:
        return "", "base_url must use http or https"
    if parsed.username or parsed.password:
        return "", "base_url must not include credentials"
    return normalized, None


def _positive_id(value: Any) -> Optional[int]:
    if isinstance(value, bool):
        return None
    try:
        result = int(value)
    except (TypeError, ValueError):
        return None
    return result if result > 0 else None


def _normalize_channels(
    channels: Any, *, require_base_url: bool
) -> tuple[list[dict[str, Any]], Optional[str]]:
    if not isinstance(channels, list):
        return [], "主站渠道响应不是完整列表"
    by_id: dict[int, dict[str, Any]] = {}
    for channel in channels:
        if not isinstance(channel, dict):
            return [], "主站渠道列表包含无效项"
        channel_id = _positive_id(channel.get("id"))
        if channel_id is None:
            return [], "主站渠道列表包含缺少 ID 的渠道"
        safe = _safe_value(channel)
        if not isinstance(safe, dict):
            safe = {}
        safe["id"] = channel_id
        if channel.get("base_url"):
            base_url, error = _normalize_base_url(channel.get("base_url"))
            if error:
                if require_base_url:
                    return [], f"渠道 {channel_id} 的 Base URL 无效"
            else:
                safe["base_url"] = base_url
        elif require_base_url:
            return [], f"渠道 {channel_id} 缺少有效 Base URL，拒绝执行同步清理"
        by_id[channel_id] = safe
    return [by_id[channel_id] for channel_id in sorted(by_id)], None


def _normalize_groups(payload: Any) -> tuple[dict[str, Any], Optional[str]]:
    if not isinstance(payload, dict):
        return {}, "主站分组响应无效"
    data = payload.get("data")
    if not isinstance(data, dict):
        return {}, "主站分组响应不是完整列表"
    return {str(name): _safe_value(group) for name, group in data.items()}, None


class SyncService:
    def __init__(self, repository: SyncRepository | None = None) -> None:
        self.repository = repository or SyncRepository()

    def run(self, admin_site_id: Optional[int] = None) -> dict[str, Any]:
        try:
            if admin_site_id is None:
                admins = self.repository.list_admin_sites()
            else:
                admin = self.repository.get_admin_site(admin_site_id)
                admins = [admin] if admin else []
        except Exception as exc:
            return {
                "success": False,
                "message": sanitize_error_text(str(exc)) or "读取主站列表失败",
            }
        if admin_site_id is not None and not admins:
            results = [{
                "admin_site_id": int(admin_site_id),
                "status": "sync_failed",
                "message": "管理站点不存在",
                "imported": 0, "disabled": 0, "reenabled": 0, "deleted": 0,
            }]
            return self._aggregate(results)
        mode = self.repository.get_reconcile_mode(RECONCILE_MODE_DISABLE)
        results = [self._sync_one(admin, mode) for admin in admins if isinstance(admin, dict)]
        results.append(self._summary(results, mode))
        return self._aggregate(results)

    def _client(self, admin: dict[str, Any]) -> Any:
        platform = str(admin.get("platform") or "newapi").strip().lower()
        return Sub2ApiClient(site=admin) if platform == "sub2api" else NewApiClient(site=admin)

    def _sync_one(self, admin: dict[str, Any], mode: str) -> dict[str, Any]:
        admin_id = int(admin.get("id") or 0)
        platform = str(admin.get("platform") or "newapi").strip().lower()
        try:
            client = self._client(admin)
            ok, channels, _meta, error = client.list_channels()
            if not ok:
                raise RuntimeError(error or "读取主站渠道失败")
            ok, groups_payload, error = client.list_groups()
            if not ok:
                raise RuntimeError(error or "读取主站分组失败")
            normalized_channels, channels_error = _normalize_channels(
                channels, require_base_url=platform == "newapi"
            )
            if channels_error:
                raise RuntimeError(channels_error)
            normalized_groups, groups_error = _normalize_groups(groups_payload)
            if groups_error:
                raise RuntimeError(groups_error)

            detected: dict[str, str] = {}
            probed_sub2api = probed_newapi = probed_unknown = 0
            if platform == "newapi":
                candidates = aggregate_channel_candidates(normalized_channels)
                detected = detect_upstream_platforms(
                    [candidate.get("base_url") for candidate in candidates]
                )
                for value in detected.values():
                    if value == "sub2api":
                        probed_sub2api += 1
                    elif value == "newapi":
                        probed_newapi += 1
                    else:
                        probed_unknown += 1
            result = self._write_snapshot(admin, normalized_channels, normalized_groups, mode, detected)
            result.update({
                "probed_sub2api": probed_sub2api,
                "probed_newapi": probed_newapi,
                "probed_unclassified": probed_unknown,
            })
            return result
        except Exception as exc:
            message = sanitize_error_text(str(exc)) or "主站同步失败"
            self.repository.record_error(admin_id, message)
            return {
                "admin_site_id": admin_id, "platform": platform,
                "status": "sync_failed", "message": message,
                "imported": 0, "disabled": 0, "reenabled": 0, "deleted": 0,
            }

    def _write_snapshot(
        self,
        admin: dict[str, Any],
        channels: list[dict[str, Any]],
        groups: dict[str, Any],
        mode: str,
        detected: dict[str, str],
    ) -> dict[str, Any]:
        admin_id = int(admin.get("id") or 0)
        platform = str(admin.get("platform") or "newapi").strip().lower()
        candidates = aggregate_channel_candidates(channels) if platform == "newapi" else []
        imported = imported_sub2api = imported_newapi = 0
        conflicts: list[dict[str, Any]] = []
        preferred: dict[int, int] = {}
        with transaction() as connection:
            previous = self.repository.previous_state(connection, admin_id)
            if platform == "newapi":
                for candidate in candidates:
                    upstream_platform = detected.get(candidate.get("base_url") or "") or "newapi"
                    item = self.repository.import_candidate(
                        connection, admin_id, candidate, 3, upstream_platform
                    )
                    if item.get("status") == "conflict":
                        conflicts.append({
                            "base_url": item.get("base_url") or candidate.get("base_url"),
                            "name": item.get("name") or candidate.get("name"),
                            "channel_ids": list(item.get("channel_ids") or candidate.get("channel_ids") or []),
                            "site_id": item.get("site_id"),
                            "message": item.get("message") or "监控站点平台冲突",
                        })
                        continue
                    site_id = _positive_id(item.get("site_id"))
                    if site_id is not None:
                        for raw_channel_id in candidate.get("channel_ids") or []:
                            channel_id = _positive_id(raw_channel_id)
                            if channel_id is not None:
                                preferred[channel_id] = site_id
                    if item.get("status") == "created":
                        imported += 1
                        if upstream_platform == "sub2api":
                            imported_sub2api += 1
                        else:
                            imported_newapi += 1
            removed_links, affected = (
                self.repository.reconcile_links(connection, admin_id, candidates, preferred)
                if platform == "newapi" else (0, set())
            )
            live_ids = {
                channel_id
                for channel in channels
                if (channel_id := _positive_id(channel.get("id"))) is not None
            }
            removed_bindings, removed_keys = self.repository.remove_stale_channel_data(
                connection, admin_id, live_ids
            )
            disabled = reenabled = deleted = 0
            if platform == "newapi":
                disabled, reenabled, deleted = self.repository.apply_reconcile(
                    connection, admin_id, affected, mode
                )
            changed = self.repository.write_snapshot(
                connection, admin_id, channels, groups, previous
            )
        return {
            "admin_site_id": admin_id, "platform": platform, "status": "synced",
            "imported": imported, "imported_sub2api": imported_sub2api,
            "imported_newapi": imported_newapi, "channels_count": len(channels),
            "groups_count": len(groups), "channels_changed": changed["channels_changed"],
            "groups_changed": changed["groups_changed"], "conflicts": conflicts,
            "conflict_count": len(conflicts), "removed_links": removed_links,
            "removed_bindings": removed_bindings, "removed_keys": removed_keys,
            "disabled": disabled, "reenabled": reenabled, "deleted": deleted,
        }

    @staticmethod
    def _summary(results: list[dict[str, Any]], mode: str) -> dict[str, Any]:
        def total(key: str) -> int:
            return sum(int(item.get(key) or 0) for item in results if isinstance(item, dict))

        return {
            "status": "reconcile", "mode": mode,
            "channels_changed": any(bool(item.get("channels_changed")) for item in results),
            "groups_changed": any(bool(item.get("groups_changed")) for item in results),
            "disabled": total("disabled"), "reenabled": total("reenabled"),
            "deleted": total("deleted"), "imported_sub2api": total("imported_sub2api"),
            "imported_newapi": total("imported_newapi"), "probed_sub2api": total("probed_sub2api"),
            "probed_newapi": total("probed_newapi"), "probed_unclassified": total("probed_unclassified"),
        }

    @staticmethod
    def _aggregate(results: list[dict[str, Any]]) -> dict[str, Any]:
        reconcile = next((item for item in results if item.get("status") == "reconcile"), {})
        failed = [item for item in results if item.get("status") in {"fetch_failed", "sync_failed", "error"}]
        key_errors: list[str] = []
        for item in results:
            for message in item.get("key_errors") or []:
                if message not in key_errors:
                    key_errors.append(str(message))
        return {
            "success": True, "data": results,
            "mode": reconcile.get("mode") or RECONCILE_MODE_DISABLE,
            "channels_changed": bool(reconcile.get("channels_changed")),
            "groups_changed": bool(reconcile.get("groups_changed")),
            "keys_refreshed": sum(int(item.get("keys_refreshed") or 0) for item in results),
            "keys_changed": sum(int(item.get("keys_changed") or 0) for item in results),
            "keys_failed": sum(int(item.get("keys_failed") or 0) for item in results),
            "key_errors": key_errors[:3],
            "imported": sum(int(item.get("imported") or 0) for item in results),
            "imported_sub2api": int(reconcile.get("imported_sub2api") or 0),
            "imported_newapi": int(reconcile.get("imported_newapi") or 0),
            "probed_sub2api": int(reconcile.get("probed_sub2api") or 0),
            "probed_newapi": int(reconcile.get("probed_newapi") or 0),
            "probed_unclassified": int(reconcile.get("probed_unclassified") or 0),
            "conflicts": sum(int(item.get("conflict_count") or 0) for item in results),
            "disabled": int(reconcile.get("disabled") or 0),
            "reenabled": int(reconcile.get("reenabled") or 0),
            "deleted": int(reconcile.get("deleted") or 0), "failed": len(failed),
        }


__all__ = ["SyncService"]
