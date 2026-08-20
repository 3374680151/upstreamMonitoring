"""Channel discovery and idempotent monitoring-site imports."""
from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

from backend.db import normalize_base_url
from backend.repositories.discovery import DiscoveryRepository


DEFAULT_INTERVAL_MINUTES = 3
MIN_INTERVAL_MINUTES = 1
MAX_DISCOVERY_IMPORT_ITEMS = 100
MAX_DISCOVERY_CHANNEL_IDS_PER_ITEM = 1000
MAX_DISCOVERY_INTERVAL_MINUTES = 1440


def _safe_display_url(value: Any) -> str:
    text = normalize_base_url(str(value or ""))
    if not text:
        return ""
    try:
        parsed = urlparse(text)
        if parsed.username or parsed.password:
            scheme = parsed.scheme.lower()
            hostname = parsed.hostname or ""
            if not hostname:
                return ""
            try:
                port = f":{parsed.port}" if parsed.port else ""
            except (TypeError, ValueError):
                port = ""
            return f"{scheme}://{hostname}{port}{parsed.path or ''}".rstrip("/")
    except (TypeError, ValueError):
        return ""
    return text[:512]


def _normalize_base_url(value: Any) -> tuple[str, str | None]:
    normalized = normalize_base_url(str(value or ""))
    if not normalized:
        return "", "base_url required"
    try:
        parsed = urlparse(normalized)
        parsed.port
    except (TypeError, ValueError):
        return "", "base_url invalid"
    if (
        parsed.scheme.lower() not in {"http", "https"}
        or not parsed.netloc
        or not parsed.hostname
    ):
        return "", "base_url must use http or https"
    if parsed.username or parsed.password:
        return "", "base_url must not include credentials"
    return normalized, None


def _channel_ids(value: Any) -> tuple[list[int], str | None]:
    if not isinstance(value, list) or not value:
        return [], "channel_ids required"
    if len(value) > MAX_DISCOVERY_CHANNEL_IDS_PER_ITEM:
        return [], "too many channel_ids"
    result: list[int] = []
    for raw_id in value:
        if isinstance(raw_id, bool):
            return [], "channel_id invalid"
        try:
            channel_id = int(raw_id)
        except (TypeError, ValueError):
            return [], "channel_id invalid"
        if channel_id <= 0:
            return [], "channel_id invalid"
        if channel_id not in result:
            result.append(channel_id)
    return (result, None) if result else ([], "channel_ids required")


def _public_item(
    item: dict[str, Any],
    status: str,
    site_id: int | None = None,
    message: str = "",
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "base_url": str(item.get("base_url") or ""),
        "name": str(item.get("name") or "").strip()
        or str(item.get("base_url") or ""),
        "channel_ids": list(item.get("channel_ids") or []),
        "status": status,
    }
    if site_id is not None:
        result["site_id"] = site_id
    if message:
        result["message"] = message
    return result


class DiscoveryService:
    def __init__(self, repository: DiscoveryRepository | None = None) -> None:
        self.repository = repository or DiscoveryRepository()

    def import_sites(self, admin_site: dict[str, Any], payload: dict[str, Any]):
        if not isinstance(admin_site, dict) or (
            str(admin_site.get("platform") or "newapi").strip().lower() != "newapi"
        ):
            return {
                "error": "platform_invalid",
                "message": "主站渠道发现导入仅支持 NewAPI",
            }
        if not isinstance(payload, dict):
            return {"error": "invalid_body", "message": "请求体无效"}
        raw_items = payload.get("items")
        if not isinstance(raw_items, list):
            return {"error": "items_invalid", "message": "items 必须是数组"}
        if len(raw_items) > MAX_DISCOVERY_IMPORT_ITEMS:
            return {
                "error": "too_many_items",
                "message": f"单次最多导入 {MAX_DISCOVERY_IMPORT_ITEMS} 个渠道",
            }
        raw_interval = payload.get("interval_minutes", DEFAULT_INTERVAL_MINUTES)
        if isinstance(raw_interval, bool):
            return {"error": "interval_invalid", "message": "interval_minutes 无效"}
        try:
            interval = int(raw_interval or DEFAULT_INTERVAL_MINUTES)
        except (TypeError, ValueError):
            return {"error": "interval_invalid", "message": "interval_minutes 无效"}
        interval = max(
            MIN_INTERVAL_MINUTES,
            min(MAX_DISCOVERY_INTERVAL_MINUTES, interval),
        )
        try:
            admin_site_id = int(admin_site.get("id"))
        except (TypeError, ValueError):
            return {"error": "admin_site_invalid", "message": "管理站点 ID 无效"}

        results: list[dict[str, Any] | None] = [None] * len(raw_items)
        for index, raw_item in enumerate(raw_items):
            if not isinstance(raw_item, dict):
                results[index] = {
                    "base_url": "",
                    "name": "",
                    "channel_ids": [],
                    "status": "invalid",
                    "message": "候选项无效",
                }
                continue
            base_url, url_error = _normalize_base_url(raw_item.get("base_url"))
            item: dict[str, Any] = {
                "base_url": base_url or _safe_display_url(raw_item.get("base_url")),
                "name": str(raw_item.get("name") or "").strip(),
                "channel_ids": [],
                "channel_names": raw_item.get("channel_names"),
            }
            if url_error:
                item["channel_ids"] = (
                    list(raw_item.get("channel_ids") or [])
                    if isinstance(raw_item.get("channel_ids"), list)
                    else []
                )
                results[index] = _public_item(item, "invalid", message=url_error)
                continue
            channel_ids, ids_error = _channel_ids(raw_item.get("channel_ids"))
            if ids_error:
                item["base_url"] = base_url
                item["channel_ids"] = (
                    list(raw_item.get("channel_ids") or [])
                    if isinstance(raw_item.get("channel_ids"), list)
                    else []
                )
                results[index] = _public_item(item, "invalid", message=ids_error)
                continue
            item["base_url"] = base_url
            item["channel_ids"] = channel_ids
            item["name"] = item["name"] or base_url
            try:
                status, site_id, message = self.repository.import_item(
                    admin_site_id, item, interval
                )
                results[index] = _public_item(item, status, site_id, message)
            except Exception:
                results[index] = _public_item(
                    item, "conflict", message="创建或关联监控站点失败"
                )
        return [result for result in results if result is not None]

    def links(self, site_id: int):
        return self.repository.list_links(site_id)
