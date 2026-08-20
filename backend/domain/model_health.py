"""Pure model-health mapping and payload sanitisation rules.

The functions in this module only transform upstream responses and the
already persisted group snapshot.  HTTP requests and cache lifecycle belong
to integration/service modules; keeping these rules pure makes the model
health response independent of the old request handler.
"""

from __future__ import annotations

import json
import re
from typing import Any


SUB2API_AUTH_CONTEXT_KEYS = frozenset(
    {
        "refreshed_auth",
        "_auth_context",
        "access_token",
        "refresh_token",
        "authorization",
        "password",
        "browser_refresh_cookie",
        "browser_session_id",
    }
)


def site_groups_from_row(site: dict[str, Any]) -> dict[str, dict[str, Any]]:
    value = site.get("current_groups_json")
    if not value:
        return {}
    try:
        groups = json.loads(value) if isinstance(value, str) else value
    except (TypeError, ValueError):
        return {}
    return groups if isinstance(groups, dict) else {}


def strip_sub2api_auth_context(value: Any) -> Any:
    """Remove credential-bearing fields before a payload reaches a router."""
    if isinstance(value, dict):
        return {
            key: strip_sub2api_auth_context(item)
            for key, item in value.items()
            if str(key or "").strip().lower() not in SUB2API_AUTH_CONTEXT_KEYS
        }
    if isinstance(value, list):
        return [strip_sub2api_auth_context(item) for item in value]
    if isinstance(value, tuple):
        return tuple(strip_sub2api_auth_context(item) for item in value)
    return value


def parse_newapi_models_by_group(
    pricing_payload: Any,
    uptime_payload: Any,
    groups: dict[str, dict[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    """Join NewAPI pricing entries and public uptime monitors by group."""
    if isinstance(pricing_payload, dict) and "data" in pricing_payload:
        pricing_payload = pricing_payload.get("data")
    if isinstance(uptime_payload, dict) and "data" in uptime_payload:
        uptime_payload = uptime_payload.get("data")
    if not isinstance(pricing_payload, list) or not isinstance(groups, dict):
        return {}

    monitors: list[dict[str, Any]] = []
    if isinstance(uptime_payload, list):
        for category in uptime_payload:
            if not isinstance(category, dict):
                continue
            category_name = str(category.get("categoryName") or "").strip()
            for monitor in category.get("monitors") or []:
                if not isinstance(monitor, dict):
                    continue
                entry = dict(monitor)
                entry["category"] = category_name
                monitors.append(entry)

    def matching_monitor(model_name: str) -> dict[str, Any] | None:
        normalized = model_name.casefold()
        exact = [
            item
            for item in monitors
            if str(item.get("name") or "").strip().casefold() == normalized
        ]
        if exact:
            return exact[0]
        fuzzy = [
            item
            for item in monitors
            if str(item.get("name") or "").strip()
            and (
                normalized in str(item.get("name") or "").strip().casefold()
                or str(item.get("name") or "").strip().casefold() in normalized
            )
        ]
        return fuzzy[0] if len(fuzzy) == 1 else None

    def monitor_status(value: Any) -> str:
        try:
            status = int(value)
        except (TypeError, ValueError):
            return "configured"
        return {
            0: "error",
            1: "operational",
            2: "degraded",
            3: "maintenance",
        }.get(status, "configured")

    models_by_group: dict[str, list[dict[str, Any]]] = {}
    seen: set[tuple[str, str]] = set()
    for pricing in pricing_payload:
        if not isinstance(pricing, dict):
            continue
        model_name = str(
            pricing.get("model_name") or pricing.get("name") or ""
        ).strip()
        if not model_name:
            continue
        enabled_groups = pricing.get("enable_groups") or pricing.get("groups") or []
        if isinstance(enabled_groups, str):
            enabled_groups = [enabled_groups]
        if not isinstance(enabled_groups, list):
            enabled_groups = []
        enabled_names = {
            str(name).strip() for name in enabled_groups if str(name).strip()
        }
        target_groups = (
            list(groups.keys())
            if "all" in enabled_names
            else [name for name in groups if name in enabled_names]
        )
        if not target_groups:
            continue

        monitor = matching_monitor(model_name)
        uptime_value = monitor.get("uptime") if monitor else None
        try:
            availability = float(uptime_value)
            if 0 <= availability <= 1:
                availability *= 100
        except (TypeError, ValueError):
            availability = None

        ratio_value = pricing.get("model_ratio")
        try:
            ratio_value = float(ratio_value)
            ratio_type = "number"
        except (TypeError, ValueError):
            ratio_type = "text"

        for group_name in target_groups:
            key = (group_name, model_name.casefold())
            if key in seen:
                continue
            seen.add(key)
            group_info = groups.get(group_name) or {}
            models_by_group.setdefault(group_name, []).append(
                {
                    "name": model_name,
                    "ratio": ratio_value,
                    "ratio_type": ratio_type,
                    "group_ratio": group_info.get("ratio"),
                    "channel": str(monitor.get("category") or "") if monitor else "",
                    "platform": pricing.get("owner_by") or "NewAPI",
                    "status": monitor_status(monitor.get("status"))
                    if monitor
                    else "configured",
                    "latency_ms": None,
                    "ping_latency_ms": None,
                    "availability_7d": availability,
                    "availability_label": "24 小时" if availability is not None else "",
                    "timeline": [],
                    "monitor": str(monitor.get("name") or "")
                    if monitor
                    else model_name,
                    "source": "NewAPI 公开监控"
                    if monitor
                    else "NewAPI 模型配置",
                    "completion_ratio": pricing.get("completion_ratio"),
                }
            )

    for model_list in models_by_group.values():
        model_list.sort(key=lambda item: str(item.get("name") or "").casefold())
    return models_by_group


def parse_sub2api_channel_models(
    channels_payload: Any,
    groups: dict[str, dict[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    """Map sub2api channel/platform model sections back to monitored groups."""
    if isinstance(channels_payload, dict) and "data" in channels_payload:
        channels_payload = channels_payload.get("data")
    if not isinstance(channels_payload, list) or not isinstance(groups, dict):
        return {}

    groups_by_id = {
        str(item.get("id")): (name, item)
        for name, item in groups.items()
        if isinstance(item, dict) and item.get("id") is not None
    }
    models_by_group: dict[str, list[dict[str, Any]]] = {}
    seen: set[tuple[str, str, str]] = set()

    for channel in channels_payload:
        if not isinstance(channel, dict):
            continue
        channel_name = str(channel.get("name") or "").strip()
        sections = channel.get("platforms") or []
        if not isinstance(sections, list):
            continue
        for section in sections:
            if not isinstance(section, dict):
                continue
            models = section.get("supported_models") or section.get("models") or []
            group_refs = section.get("groups") or []
            if not isinstance(models, list) or not isinstance(group_refs, list):
                continue

            matched_groups: list[tuple[str, dict[str, Any]]] = []
            for group_ref in group_refs:
                if not isinstance(group_ref, dict):
                    continue
                group_id = group_ref.get("id")
                matched = groups_by_id.get(str(group_id)) if group_id is not None else None
                if not matched:
                    group_name = str(group_ref.get("name") or "").strip()
                    group_info = groups.get(group_name)
                    matched = (
                        (group_name, group_info)
                        if isinstance(group_info, dict)
                        else None
                    )
                if matched:
                    matched_groups.append(matched)

            for group_name, group_info in matched_groups:
                destination = models_by_group.setdefault(group_name, [])
                for model in models:
                    if not isinstance(model, dict):
                        continue
                    model_name = str(
                        model.get("name")
                        or model.get("id")
                        or model.get("model")
                        or ""
                    ).strip()
                    if not model_name:
                        continue
                    model_key = (group_name, channel_name, model_name)
                    if model_key in seen:
                        continue
                    seen.add(model_key)

                    raw_status = (
                        model.get("status")
                        or model.get("health")
                        or model.get("state")
                        or ""
                    )
                    if not raw_status and isinstance(model.get("available"), bool):
                        raw_status = "可用" if model["available"] else "不可用"
                    if not raw_status and isinstance(model.get("enabled"), bool):
                        raw_status = "启用" if model["enabled"] else "停用"

                    destination.append(
                        {
                            "name": model_name,
                            "ratio": group_info.get("ratio"),
                            "ratio_type": group_info.get("ratio_type") or "text",
                            "channel": channel_name,
                            "platform": model.get("platform")
                            or section.get("platform")
                            or group_info.get("platform")
                            or "",
                            "status": str(raw_status),
                        }
                    )

    for model_list in models_by_group.values():
        model_list.sort(
            key=lambda item: (
                str(item.get("name") or "").lower(),
                str(item.get("channel") or "").lower(),
            )
        )
    return models_by_group


def parse_sub2api_monitor_models(
    monitors_payload: Any,
    groups: dict[str, dict[str, Any]],
) -> tuple[dict[str, list[dict[str, Any]]], list[dict[str, Any]]]:
    """Attach sub2api monitor status to groups only when the match is safe."""
    if isinstance(monitors_payload, dict) and "data" in monitors_payload:
        monitors_payload = monitors_payload.get("data")
    items = monitors_payload.get("items") if isinstance(monitors_payload, dict) else None
    if not isinstance(items, list) or not isinstance(groups, dict):
        return {}, []

    models_by_group: dict[str, list[dict[str, Any]]] = {}
    unmatched: list[dict[str, Any]] = []

    def normalized_label(value: Any) -> str:
        return "".join(char.casefold() for char in str(value or "") if char.isalnum())

    def numeric_values(value: Any) -> list[float]:
        values: list[float] = []
        for raw in re.findall(r"(?<![A-Za-z])\d+(?:\.\d+)?", str(value or "")):
            try:
                values.append(float(raw))
            except ValueError:
                continue
        return values

    def same_ratio(left: Any, right: Any) -> bool:
        try:
            return abs(float(left) - float(right)) < 1e-9
        except (TypeError, ValueError):
            return False

    def resolve_group(
        monitor: dict[str, Any],
    ) -> tuple[str, dict[str, Any]] | None:
        group_name = str(monitor.get("group_name") or "").strip()
        group_info = groups.get(group_name)
        if isinstance(group_info, dict):
            return group_name, group_info
        provider = str(monitor.get("provider") or "").strip().lower()
        platform_matches = [
            (name, item)
            for name, item in groups.items()
            if isinstance(item, dict)
            and str(item.get("platform") or "").strip().lower() == provider
        ]
        monitor_label = normalized_label(monitor.get("name"))
        label_matches = [
            (len(normalized_label(name)), name, item)
            for name, item in platform_matches
            if normalized_label(name) and normalized_label(name) in monitor_label
        ]
        if label_matches:
            longest = max(item[0] for item in label_matches)
            best = [
                (name, item) for length, name, item in label_matches if length == longest
            ]
            if len(best) == 1:
                return best[0]
            monitor_numbers = numeric_values(monitor.get("name"))
            ratio_matches = [
                (name, item)
                for name, item in best
                if any(same_ratio(number, item.get("ratio")) for number in monitor_numbers)
            ]
            if len(ratio_matches) == 1:
                return ratio_matches[0]

        if len(platform_matches) == 1:
            return platform_matches[0]
        if len(groups) == 1:
            name, item = next(iter(groups.items()))
            return (name, item) if isinstance(item, dict) else None
        return None

    for monitor in items:
        if not isinstance(monitor, dict):
            continue
        monitor_name = str(monitor.get("name") or "").strip()
        target_group = resolve_group(monitor)
        monitored_models: list[dict[str, Any]] = []
        primary_model = str(monitor.get("primary_model") or "").strip()
        timeline: list[dict[str, Any]] = []
        for point in monitor.get("timeline") or []:
            if not isinstance(point, dict):
                continue
            timeline.append(
                {
                    "status": str(point.get("status") or ""),
                    "latency_ms": point.get("latency_ms"),
                    "ping_latency_ms": point.get("ping_latency_ms"),
                    "checked_at": str(point.get("checked_at") or ""),
                }
            )
            if len(timeline) >= 60:
                break
        if primary_model:
            monitored_models.append(
                {
                    "name": primary_model,
                    "status": str(monitor.get("primary_status") or ""),
                    "latency_ms": monitor.get("primary_latency_ms"),
                    "ping_latency_ms": monitor.get("primary_ping_latency_ms"),
                    "availability_7d": monitor.get("availability_7d"),
                    "timeline": timeline,
                }
            )
        for extra in monitor.get("extra_models") or []:
            if not isinstance(extra, dict):
                continue
            model_name = str(extra.get("model") or "").strip()
            if model_name:
                monitored_models.append(
                    {
                        "name": model_name,
                        "status": str(extra.get("status") or ""),
                        "latency_ms": extra.get("latency_ms"),
                        "ping_latency_ms": None,
                        "availability_7d": None,
                        "timeline": [],
                    }
                )

        if not target_group:
            unmatched.extend(
                {
                    "name": item["name"],
                    "status": item.get("status") or "",
                    "monitor": monitor_name,
                    "provider": monitor.get("provider") or "",
                }
                for item in monitored_models
            )
            continue

        group_name, group_info = target_group
        destination = models_by_group.setdefault(group_name, [])
        for item in monitored_models:
            destination.append(
                {
                    "name": item["name"],
                    "ratio": group_info.get("ratio"),
                    "ratio_type": group_info.get("ratio_type") or "text",
                    "channel": "",
                    "platform": monitor.get("provider")
                    or group_info.get("platform")
                    or "",
                    "status": item["status"],
                    "latency_ms": item["latency_ms"],
                    "ping_latency_ms": item["ping_latency_ms"],
                    "availability_7d": item["availability_7d"],
                    "timeline": item["timeline"],
                    "monitor": monitor_name,
                    "source": "上游监控",
                }
            )

    return models_by_group, unmatched


def merge_sub2api_group_models(
    configured_models: dict[str, list[dict[str, Any]]],
    monitored_models: dict[str, list[dict[str, Any]]],
) -> dict[str, list[dict[str, Any]]]:
    """Prefer monitor fields while retaining configured-only models."""
    merged = {
        name: [dict(item) for item in values]
        for name, values in configured_models.items()
    }
    for group_name, items in monitored_models.items():
        destination = merged.setdefault(group_name, [])
        indexes = {
            str(item.get("name") or "").casefold(): index
            for index, item in enumerate(destination)
        }
        for item in items:
            key = str(item.get("name") or "").casefold()
            if key in indexes:
                destination[indexes[key]].update(
                    {
                        field: value
                        for field, value in item.items()
                        if value not in (None, "")
                    }
                )
            else:
                indexes[key] = len(destination)
                destination.append(dict(item))
    for model_list in merged.values():
        model_list.sort(key=lambda item: str(item.get("name") or "").casefold())
    return merged


__all__ = [
    "merge_sub2api_group_models",
    "parse_newapi_models_by_group",
    "parse_sub2api_channel_models",
    "parse_sub2api_monitor_models",
    "site_groups_from_row",
    "strip_sub2api_auth_context",
]
