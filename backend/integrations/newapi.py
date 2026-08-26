"""NewAPI upstream integration.

All NewAPI-specific protocol, parsing, channel, account, browser-session and
uptime functions live here.  HTTP transport / upstream response helpers come
from :mod:`backend.integrations.http`; the legacy runtime re-exports every
name below for backward compatibility.
"""
from __future__ import annotations

import json
import re
import threading
import time
from http.cookies import SimpleCookie
from typing import Any, Dict, Iterable, List, Optional, Tuple
from urllib.parse import quote

from backend.core.config import HTTP_TIMEOUT_SECONDS
from backend.repositories.admin_sites import (
    get_cached_admin_channel_key,
    is_admin_site_row,
    persist_admin_channel_key,
)
from backend.core.normalize import (
    _channel_key_is_masked,
    _normalize_discovery_base_url,
    _positive_channel_id,
    clamp_perf_hours,
    mask_newapi_user_token_key,
    normalize_base_url,
    normalize_newapi_user_token_key,
    site_origin,
)
from backend.core.state import (
    BROWSER_AUTH_MODE,
    MAIN_CHANNEL_KEY_CACHE,
    MAIN_CHANNEL_KEY_CACHE_TTL_SECONDS,
    MAIN_CHANNEL_KEY_LAST_REQUEST_AT,
    MAIN_CHANNEL_KEY_MIN_INTERVAL_SECONDS,
    MAIN_CHANNEL_KEY_RATE_LIMIT_COOLDOWN_SECONDS,
    MAIN_CHANNEL_KEY_RATE_LIMIT_UNTIL,
    MAIN_CHANNEL_KEY_REQUEST_LOCK,
    MODEL_CACHE_LOCK,
    MODEL_CACHE_REFRESHING,
    MODEL_CACHE_TTL_SECONDS,
    MODEL_DATA_CACHE,
    NEWAPI_SITE_BROWSER_SESSION_LOCKS,
    NEWAPI_UPTIME_CACHE,
    NEWAPI_UPTIME_REFRESHING,
    NEWAPI_USER_TOKEN_LIST_CACHE,
    NEWAPI_USER_TOKEN_LIST_CACHE_TTL_SECONDS,
    NEWAPI_USER_TOKEN_LIST_LOCK,
    SESSION_SYNC_MAX_TOKEN_LENGTH,
    UPTIME_CACHE_TTL_SECONDS,
)
from backend.core.time import app_now, next_check_iso, parse_iso_dt, utc_now_iso
from backend.db.connection import (
    _q,
    db_connection,
    db_execute,
    db_execute_rowcount,
    db_query_all,
    db_query_one,
)
from backend.integrations.http import (
    _admin_browser_refresh_error,
    _cookie_header_from_response,
    _upstream_response_details,
    _upstream_response_message,
    UpstreamHttpStatusError,
    admin_request_json,
    newapi_auth_failure_message,
    request_json,
    request_json_with_headers,
    send_upstream_request,
)


def aggregate_newapi_channel_candidates(
    channels: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Group NewAPI channels by normalized, safe upstream Base URL.

    Dict insertion order preserves the order in which the upstream pagination
    returned the first channel for each URL.  Source IDs and names are kept in
    stable order for display and for the discovery-link write.
    """
    grouped: Dict[str, Dict[str, Any]] = {}
    for channel in channels or []:
        if not isinstance(channel, dict):
            continue
        channel_id = _positive_channel_id(channel.get("id"))
        if channel_id is None:
            continue
        base_url, _error = _normalize_discovery_base_url(channel.get("base_url"))
        if not base_url:
            continue
        item = grouped.setdefault(
            base_url,
            {
                "base_url": base_url,
                "name": "",
                "channel_ids": [],
                "channel_names": [],
            },
        )
        channel_name = str(channel.get("name") or "").strip()
        if channel_name and not item["name"]:
            item["name"] = channel_name
        if channel_id not in item["channel_ids"]:
            item["channel_ids"].append(channel_id)
            # Keep this list index-aligned with channel_ids.  The old code
            # omitted empty names, which could associate the next channel's
            # name with the wrong channel during import.
            item["channel_names"].append(channel_name)
        elif channel_name:
            index = item["channel_ids"].index(channel_id)
            if not item["channel_names"][index]:
                item["channel_names"][index] = channel_name

    result: List[Dict[str, Any]] = []
    for item in grouped.values():
        candidate = dict(item)
        candidate["name"] = candidate["name"] or candidate["base_url"]
        candidate["channel_count"] = len(candidate["channel_ids"])
        result.append(candidate)
    return result

def enrich_channel_candidates_with_sites(
    candidates: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Attach a redacted local-site status to each discovery candidate.

    Only the columns needed for status mapping are selected and only public
    fields are copied to the returned object.  Credentials from either the
    database row or a malformed caller-supplied candidate never reach the UI.
    """
    rows = db_query_all(
        "SELECT id, base_url, platform, status, auth_mode, enabled, "
        "session_sync_status "
        "FROM sites WHERE platform = 'newapi'"
    )
    by_url: Dict[str, Dict[str, Any]] = {}
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        if str(row.get("platform") or "newapi").strip().lower() != "newapi":
            continue
        normalized, _error = _normalize_discovery_base_url(row.get("base_url"))
        if normalized:
            by_url.setdefault(normalized, row)

    safe_keys = (
        "base_url",
        "name",
        "channel_ids",
        "channel_names",
        "channel_count",
    )
    enriched: List[Dict[str, Any]] = []
    for candidate in candidates or []:
        if not isinstance(candidate, dict):
            continue
        safe_candidate = {
            key: candidate[key] for key in safe_keys if key in candidate
        }
        base_url, _error = _normalize_discovery_base_url(
            safe_candidate.get("base_url")
        )
        if base_url:
            safe_candidate["base_url"] = base_url
        row = by_url.get(base_url) if base_url else None
        if row:
            safe_candidate["existing_site_id"] = row.get("id")
            # NewAPI local monitoring never inherits a browser session.  Keep
            # discovery results token-oriented even for legacy rows that were
            # created by the old browser-sync flow.
            safe_candidate["existing_site_auth_mode"] = "token"
            safe_candidate["existing_site_enabled"] = bool(row.get("enabled", True))
            safe_candidate["existing_site_session_sync_status"] = "not_requested"
            safe_candidate["existing_site_status"] = row.get("status") or "unknown"
        else:
            safe_candidate["existing_site_id"] = None
            safe_candidate["existing_site_status"] = None
            safe_candidate["existing_site_auth_mode"] = None
            safe_candidate["existing_site_enabled"] = None
            safe_candidate["existing_site_session_sync_status"] = None
        safe_candidate["importable"] = True
        enriched.append(safe_candidate)
    return enriched

def _newapi_session_sync_request_error(
    target_kind: str,
    target_id: int,
    request_id: str,
    expected_origin: str,
) -> Optional[str]:
    """Return a safe error when a claimed NewAPI sync is no longer current.

    The sync request must still be in ``validating`` state so an older
    completion cannot race a newer replacement request.
    """
    normalized_origin = site_origin(expected_origin)
    if (
        not request_id
        or not normalized_origin
        or target_kind != "site"
    ):
        return "同步请求无效，请重新发起同步"

    target = db_query_one(
        "SELECT id, base_url, platform, auth_mode FROM sites WHERE id = ?",
        (int(target_id),),
    )
    if (
        not target
        or str(target.get("platform") or "newapi").strip().lower() != "newapi"
        or str(target.get("auth_mode") or "").strip().lower() != BROWSER_AUTH_MODE
        or site_origin(str(target.get("base_url") or "")) != normalized_origin
    ):
        return "同步目标已变更，请重新发起同步"
    request = db_query_one(
        """
        SELECT id, site_id, admin_site_id, platform, target_origin, status
        FROM browser_session_sync_requests
        WHERE id = ? AND site_id = ? AND admin_site_id IS NULL
        """,
        (str(request_id), int(target_id)),
    )

    if (
        not request
        or str(request.get("status") or "") != "validating"
        or str(request.get("platform") or "").strip().lower() != "newapi"
        or str(request.get("target_origin") or "") != normalized_origin
    ):
        return "同步请求已失效，请重新发起同步"
    return None

def persist_newapi_site_browser_session_cas(
    site_id: int,
    session: Dict[str, Any],
    request_id: str,
    expected_origin: str,
) -> bool:
    """CAS write of a NewAPI regular-site browser session.

    Only updates ``sites`` rows that still match the validating sync request,
    so a newer replacement request or a manual auth-mode change cannot be
    clobbered by an older completion.  Returns True only when the row was
    actually updated.
    """
    origin = site_origin(expected_origin)
    if not origin or not request_id:
        return False
    try:
        expires_at = int(session.get("browser_access_expires_at") or 0)
    except (TypeError, ValueError):
        expires_at = 0
    access_token = str(session.get("access_token") or "").strip()
    access_user_id = str(session.get("access_user_id") or "").strip()
    browser_cookie = str(session.get("browser_cookie") or "").strip() or None
    refresh_cookie = str(session.get("browser_refresh_cookie") or "").strip() or None
    session_id = str(session.get("browser_session_id") or "").strip() or None
    now = utc_now_iso()
    return db_execute_rowcount(
        """
        UPDATE sites AS s
        SET auth_mode = 'browser', login_enabled = 1,
            login_username = NULL, login_password = NULL,
            access_token = ?, access_user_id = ?,
            browser_cookie = ?, browser_refresh_cookie = ?, browser_session_id = ?,
            browser_access_expires_at = ?,
            session_sync_status = 'ready', session_sync_error = NULL,
            session_synced_at = ?, updated_at = ?
        WHERE s.id = ?
          AND s.platform = 'newapi'
          AND s.auth_mode = 'browser'
          AND EXISTS (
              SELECT 1
              FROM browser_session_sync_requests AS r
              WHERE r.id = ?
                AND r.site_id = s.id
                AND r.admin_site_id IS NULL
                AND r.platform = 'newapi'
                AND r.target_origin = ?
                AND r.status = 'validating'
          )
        """,
        (
            access_token,
            access_user_id,
            browser_cookie,
            refresh_cookie,
            session_id,
            expires_at,
            now,
            now,
            int(site_id),
            str(request_id),
            origin,
        ),
    ) > 0

def mark_newapi_site_browser_session_expired_cas(
    site_id: int,
    message: str,
    request_id: str,
    expected_origin: str,
) -> bool:
    """CAS write that marks a NewAPI regular-site sync as expired."""
    origin = site_origin(expected_origin)
    if not origin or not request_id:
        return False
    now = utc_now_iso()
    return db_execute_rowcount(
        """
        UPDATE sites AS s
        SET session_sync_status = 'expired', session_sync_error = ?, updated_at = ?
        WHERE s.id = ?
          AND s.platform = 'newapi'
          AND s.auth_mode = 'browser'
          AND EXISTS (
              SELECT 1
              FROM browser_session_sync_requests AS r
              WHERE r.id = ?
                AND r.site_id = s.id
                AND r.admin_site_id IS NULL
                AND r.platform = 'newapi'
                AND r.target_origin = ?
                AND r.status = 'validating'
          )
        """,
        (
            str(message or "登录态已过期，请重新登录"),
            now,
            int(site_id),
            str(request_id),
            origin,
        ),
    ) > 0

NEWAPI_SESSION_SYNC_FIELDS = frozenset(
    {
        "access_token",
        "access_user_id",
        "browser_cookie",
        "browser_refresh_cookie",
        "browser_session_id",
        "browser_access_expires_at",
    }
)

def _newapi_session_payload_error(session: Dict[str, Any]) -> Optional[str]:
    browser_cookie = str(session.get("browser_cookie") or "").strip()
    refresh_cookie = str(session.get("browser_refresh_cookie") or "").strip()
    session_id = str(session.get("browser_session_id") or "").strip()
    raw_expires_at = session.get("browser_access_expires_at")
    has_expires_at = raw_expires_at not in (None, "", 0, "0")
    if browser_cookie:
        if len(browser_cookie) > SESSION_SYNC_MAX_TOKEN_LENGTH:
            return "SESSION_COOKIE_INVALID"
        cookie_parts = [part.strip() for part in browser_cookie.split(";")]
        if not cookie_parts or any(
            not re.fullmatch(r"[A-Za-z0-9_-]+=[^;\s]+", part)
            for part in cookie_parts
        ):
            return "SESSION_COOKIE_INVALID"
        if refresh_cookie or session_id or has_expires_at:
            return "SESSION_FIELDS_INVALID"
        return None
    if refresh_cookie and not re.fullmatch(r"new_api_refresh=[^\s;,]+", refresh_cookie):
        return "SESSION_COOKIE_INVALID"
    if bool(refresh_cookie) != bool(session_id):
        return "SESSION_FIELDS_INVALID"
    if has_expires_at and (not refresh_cookie or not session_id):
        return "SESSION_FIELDS_INVALID"
    if has_expires_at:
        try:
            if int(raw_expires_at) <= 0:
                return "SESSION_FIELDS_INVALID"
        except (TypeError, ValueError):
            return "SESSION_FIELDS_INVALID"
    return None

def parse_groups_payload(payload: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    data = payload.get("data") or {}
    if not isinstance(data, dict):
        return {}

    normalized: Dict[str, Dict[str, Any]] = {}
    for name in sorted(data.keys()):
        info = data.get(name) or {}
        if not isinstance(info, dict):
            info = {}

        ratio = info.get("ratio")
        if isinstance(ratio, (int, float)):
            ratio_value: Any = float(ratio)
            ratio_type = "number"
        elif isinstance(ratio, str):
            stripped = ratio.strip()
            try:
                ratio_value = float(stripped)
                ratio_type = "number"
            except ValueError:
                ratio_value = stripped
                ratio_type = "text"
        else:
            ratio_value = ratio
            ratio_type = "text"

        normalized[name] = {
            "ratio": ratio_value,
            "ratio_type": ratio_type,
            "desc": info.get("desc", ""),
        }
    return normalized

def parse_newapi_models_by_group(
    pricing_payload: Any,
    uptime_payload: Any,
    groups: Dict[str, Dict[str, Any]],
) -> Dict[str, List[Dict[str, Any]]]:
    if isinstance(pricing_payload, dict) and "data" in pricing_payload:
        pricing_payload = pricing_payload.get("data")
    if isinstance(uptime_payload, dict) and "data" in uptime_payload:
        uptime_payload = uptime_payload.get("data")
    if not isinstance(pricing_payload, list) or not isinstance(groups, dict):
        return {}

    monitors: List[Dict[str, Any]] = []
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

    def matching_monitor(model_name: str) -> Optional[Dict[str, Any]]:
        normalized = model_name.casefold()
        exact = [item for item in monitors if str(item.get("name") or "").strip().casefold() == normalized]
        if exact:
            return exact[0]
        fuzzy = [
            item for item in monitors
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
        return {0: "error", 1: "operational", 2: "degraded", 3: "maintenance"}.get(status, "configured")

    models_by_group: Dict[str, List[Dict[str, Any]]] = {}
    seen: set[Tuple[str, str]] = set()
    for pricing in pricing_payload:
        if not isinstance(pricing, dict):
            continue
        model_name = str(pricing.get("model_name") or pricing.get("name") or "").strip()
        if not model_name:
            continue
        enabled_groups = pricing.get("enable_groups") or pricing.get("groups") or []
        if isinstance(enabled_groups, str):
            enabled_groups = [enabled_groups]
        if not isinstance(enabled_groups, list):
            enabled_groups = []
        enabled_names = {str(name).strip() for name in enabled_groups if str(name).strip()}
        target_groups = list(groups.keys()) if "all" in enabled_names else [name for name in groups if name in enabled_names]
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
            models_by_group.setdefault(group_name, []).append({
                "name": model_name,
                "ratio": ratio_value,
                "ratio_type": ratio_type,
                "group_ratio": group_info.get("ratio"),
                "channel": str(monitor.get("category") or "") if monitor else "",
                "platform": pricing.get("owner_by") or "NewAPI",
                "status": monitor_status(monitor.get("status")) if monitor else "configured",
                "latency_ms": None,
                "ping_latency_ms": None,
                "availability_7d": availability,
                "availability_label": "24 小时" if availability is not None else "",
                "timeline": [],
                "monitor": str(monitor.get("name") or "") if monitor else model_name,
                "source": "NewAPI 公开监控" if monitor else "NewAPI 模型配置",
                "completion_ratio": pricing.get("completion_ratio"),
            })

    for model_list in models_by_group.values():
        model_list.sort(key=lambda item: str(item.get("name") or "").casefold())
    return models_by_group

def fetch_newapi_groups(base_url: str) -> Tuple[bool, Dict[str, Any], Optional[str]]:
    url = f"{normalize_base_url(base_url)}/api/user/groups"
    try:
        resp = send_upstream_request(url)
        body = resp.text()
        payload = json.loads(body)
        if not isinstance(payload, dict) or not payload.get("success"):
            return False, payload if isinstance(payload, dict) else {"raw": body}, "success=false"
        return True, payload, None
    except UpstreamHttpStatusError as exc:
        try:
            raw = exc.read().decode("utf-8", errors="replace")
        except Exception:
            raw = ""
        return False, {"status": exc.code, "raw": raw}, f"HTTP {exc.code}"
    except Exception as exc:
        return False, {"error": str(exc)}, str(exc)

def refresh_newapi_uptime_cache(base_url: str, headers: Dict[str, str]) -> None:
    normalized_base = normalize_base_url(base_url)
    try:
        uptime_ok, uptime_payload, uptime_error = request_json(
            f"{normalized_base}/api/uptime/status",
            headers=headers,
        )
        if uptime_ok and isinstance(uptime_payload, dict) and uptime_payload.get("success"):
            with MODEL_CACHE_LOCK:
                NEWAPI_UPTIME_CACHE[normalized_base] = {
                    "payload": uptime_payload,
                    "updated_monotonic": time.monotonic(),
                    "error": None,
                }
            matching_sites = db_query_all("SELECT id FROM sites WHERE base_url = ?", (normalized_base,))
            from backend.services.monitoring_service import invalidate_site_model_cache, schedule_model_cache_refresh
            for site in matching_sites:
                invalidate_site_model_cache(int(site["id"]))
                schedule_model_cache_refresh(int(site["id"]))
        elif uptime_error:
            with MODEL_CACHE_LOCK:
                previous = NEWAPI_UPTIME_CACHE.get(normalized_base, {})
                previous["error"] = uptime_error
                NEWAPI_UPTIME_CACHE[normalized_base] = previous
    finally:
        with MODEL_CACHE_LOCK:
            NEWAPI_UPTIME_REFRESHING.discard(normalized_base)

def get_cached_newapi_uptime(base_url: str, headers: Dict[str, str]) -> Tuple[Dict[str, Any], Optional[str]]:
    normalized_base = normalize_base_url(base_url)
    with MODEL_CACHE_LOCK:
        entry = NEWAPI_UPTIME_CACHE.get(normalized_base)
        age = time.monotonic() - float(entry.get("updated_monotonic") or 0) if entry else float("inf")
        if age >= UPTIME_CACHE_TTL_SECONDS and normalized_base not in NEWAPI_UPTIME_REFRESHING:
            NEWAPI_UPTIME_REFRESHING.add(normalized_base)
            threading.Thread(
                target=refresh_newapi_uptime_cache,
                args=(normalized_base, dict(headers)),
                daemon=True,
            ).start()
        if entry and isinstance(entry.get("payload"), dict):
            return entry["payload"], entry.get("error")
        return {"success": True, "data": []}, "公开监控正在后台刷新"

def _newapi_uptime_cache_key(site: Dict[str, Any]) -> str:
    """Secret-free cache key for the per-site NewAPI uptime payload.

    Uses only stable, non-sensitive site identity (base URL, site id, auth
    mode, and a coarse bucket of the browser session expiry) so no
    plaintext token, cookie, or session id ever enters a cache key.
    """
    try:
        expires = int(site.get("browser_access_expires_at") or 0)
    except (TypeError, ValueError):
        expires = 0
    auth_mode = str(site.get("auth_mode") or "token").strip().lower()
    return "|".join(
        (
            normalize_base_url(str(site.get("base_url") or "")),
            str(int(site.get("id") or 0)),
            auth_mode,
            str(expires // 60),
        )
    )

def refresh_newapi_uptime_cache_for_site(site: Dict[str, Any]) -> None:
    """Refresh the uptime cache using the unified browser executor."""
    cache_key = _newapi_uptime_cache_key(site)
    normalized_base = normalize_base_url(str(site.get("base_url") or ""))
    try:
        ok, payload, error = newapi_browser_request(site, "GET", "/api/uptime/status")
        if ok and isinstance(payload, dict) and payload.get("success"):
            with MODEL_CACHE_LOCK:
                NEWAPI_UPTIME_CACHE[cache_key] = {
                    "payload": payload,
                    "updated_monotonic": time.monotonic(),
                    "error": None,
                }
            matching_sites = db_query_all(
                "SELECT id FROM sites WHERE base_url = ?", (normalized_base,)
            )
            from backend.services.monitoring_service import invalidate_site_model_cache, schedule_model_cache_refresh
            for row in matching_sites:
                invalidate_site_model_cache(int(row["id"]))
                schedule_model_cache_refresh(int(row["id"]))
        elif error:
            with MODEL_CACHE_LOCK:
                previous = NEWAPI_UPTIME_CACHE.get(cache_key, {})
                previous["error"] = error
                NEWAPI_UPTIME_CACHE[cache_key] = previous
    finally:
        with MODEL_CACHE_LOCK:
            NEWAPI_UPTIME_REFRESHING.discard(cache_key)

def get_cached_newapi_uptime_for_site(
    site: Dict[str, Any]
) -> Tuple[Dict[str, Any], Optional[str]]:
    """Cache-aware NewAPI uptime fetcher keyed by stable site identity.

    Preserves the previous successful payload when a refresh fails so the UI
    can keep showing the last known uptime while the new request is in
    flight.
    """
    cache_key = _newapi_uptime_cache_key(site)
    with MODEL_CACHE_LOCK:
        entry = NEWAPI_UPTIME_CACHE.get(cache_key)
        age = (
            time.monotonic() - float(entry.get("updated_monotonic") or 0)
            if entry
            else float("inf")
        )
        if age >= UPTIME_CACHE_TTL_SECONDS and cache_key not in NEWAPI_UPTIME_REFRESHING:
            NEWAPI_UPTIME_REFRESHING.add(cache_key)
            threading.Thread(
                target=refresh_newapi_uptime_cache_for_site,
                args=(dict(site),),
                daemon=True,
            ).start()
        if entry and isinstance(entry.get("payload"), dict):
            return entry["payload"], entry.get("error")
    return {"success": True, "data": []}, "公开监控正在后台刷新"

def newapi_auth_headers(access_token: str = "", user_id: str = "") -> Dict[str, str]:
    """Build NewAPI console-style headers (system access token + New-Api-User)."""
    headers: Dict[str, str] = {}
    token = (access_token or "").strip()
    if token:
        headers["Authorization"] = token.removeprefix("Bearer ").removeprefix("bearer ").strip()
    if str(user_id or "").strip():
        headers["New-Api-User"] = str(user_id).strip()
    return headers

def site_newapi_headers(site: Dict[str, Any]) -> Dict[str, str]:
    headers = newapi_auth_headers(
        access_token=str(site.get("access_token") or ""),
        user_id=str(site.get("access_user_id") or ""),
    )
    proof = str(site.get("security_proof") or "").strip()
    if proof:
        headers["X-Security-Proof"] = proof
    return headers

def fetch_newapi_pricing(
    base_url: str,
    access_token: str = "",
    user_id: str = "",
) -> Tuple[bool, Dict[str, Any], Optional[str]]:
    headers = newapi_auth_headers(access_token, user_id)
    ok, payload, error = request_json(
        f"{normalize_base_url(base_url)}/api/pricing",
        headers=headers,
    )
    if ok and (not isinstance(payload, dict) or not payload.get("success")):
        return False, payload if isinstance(payload, dict) else {"raw": payload}, (
            str(payload.get("message") or "pricing success=false")
            if isinstance(payload, dict)
            else "pricing 响应异常"
        )
    if not ok:
        return False, payload if isinstance(payload, dict) else {"raw": payload}, error or "读取 pricing 失败"
    return True, payload if isinstance(payload, dict) else {"success": True, "data": payload}, None

def fetch_newapi_perf_summary(
    base_url: str,
    hours: int = 24,
    access_token: str = "",
    user_id: str = "",
) -> Tuple[bool, Dict[str, Any], Optional[str]]:
    hours = clamp_perf_hours(hours, 24)
    headers = newapi_auth_headers(access_token, user_id)
    ok, payload, error = request_json(
        f"{normalize_base_url(base_url)}/api/perf-metrics/summary?hours={hours:g}",
        headers=headers,
    )
    if ok and (not isinstance(payload, dict) or not payload.get("success")):
        return False, payload if isinstance(payload, dict) else {"raw": payload}, (
            str(payload.get("message") or "perf-metrics/summary success=false")
            if isinstance(payload, dict)
            else "perf-metrics/summary 响应异常"
        )
    if not ok:
        return False, payload if isinstance(payload, dict) else {"raw": payload}, error or "读取 perf-metrics/summary 失败"
    return True, payload if isinstance(payload, dict) else {"success": True, "data": payload}, None

def fetch_newapi_perf_detail(
    base_url: str,
    model_name: str,
    hours: int = 24,
    group: str = "",
    access_token: str = "",
    user_id: str = "",
) -> Tuple[bool, Dict[str, Any], Optional[str]]:
    hours = clamp_perf_hours(hours, 24)
    model_name = (model_name or "").strip()
    if not model_name:
        return False, {}, "model is required"
    qs = f"model={quote(model_name)}&hours={hours:g}"
    group = (group or "").strip()
    if group:
        qs += f"&group={quote(group)}"
    headers = newapi_auth_headers(access_token, user_id)
    ok, payload, error = request_json(
        f"{normalize_base_url(base_url)}/api/perf-metrics?{qs}",
        headers=headers,
    )
    if ok and (not isinstance(payload, dict) or not payload.get("success")):
        return False, payload if isinstance(payload, dict) else {"raw": payload}, (
            str(payload.get("message") or "perf-metrics success=false")
            if isinstance(payload, dict)
            else "perf-metrics 响应异常"
        )
    if not ok:
        return False, payload if isinstance(payload, dict) else {"raw": payload}, error or "读取 perf-metrics 失败"
    return True, payload if isinstance(payload, dict) else {"success": True, "data": payload}, None

def fetch_newapi_pricing_for_site(site: Dict[str, Any]) -> Tuple[bool, Dict[str, Any], Optional[str]]:
    """NewAPI ``/api/pricing`` for a full site record, browser-aware."""
    ok, payload, error = newapi_browser_request(site, "GET", "/api/pricing")
    if ok and (not isinstance(payload, dict) or not payload.get("success")):
        return False, payload if isinstance(payload, dict) else {"raw": payload}, (
            str(payload.get("message") or "pricing success=false")
            if isinstance(payload, dict)
            else "pricing 响应异常"
        )
    if not ok:
        return False, payload if isinstance(payload, dict) else {"raw": payload}, error or "读取 pricing 失败"
    return True, payload if isinstance(payload, dict) else {"success": True, "data": payload}, None

def fetch_newapi_perf_summary_for_site(
    site: Dict[str, Any], hours: int = 24
) -> Tuple[bool, Dict[str, Any], Optional[str]]:
    hours = clamp_perf_hours(hours, 24)
    ok, payload, error = newapi_browser_request(
        site, "GET", "/api/perf-metrics/summary", query=f"hours={hours:g}"
    )
    if ok and (not isinstance(payload, dict) or not payload.get("success")):
        return False, payload if isinstance(payload, dict) else {"raw": payload}, (
            str(payload.get("message") or "perf-metrics/summary success=false")
            if isinstance(payload, dict)
            else "perf-metrics/summary 响应异常"
        )
    if not ok:
        return False, payload if isinstance(payload, dict) else {"raw": payload}, error or "读取 perf-metrics/summary 失败"
    return True, payload if isinstance(payload, dict) else {"success": True, "data": payload}, None

def fetch_newapi_perf_detail_for_site(
    site: Dict[str, Any], model_name: str, hours: int = 24, group: str = ""
) -> Tuple[bool, Dict[str, Any], Optional[str]]:
    hours = clamp_perf_hours(hours, 24)
    model_name = (model_name or "").strip()
    if not model_name:
        return False, {}, "model is required"
    qs = f"model={quote(model_name)}&hours={hours:g}"
    group = (group or "").strip()
    if group:
        qs += f"&group={quote(group)}"
    ok, payload, error = newapi_browser_request(
        site, "GET", "/api/perf-metrics", query=qs
    )
    if ok and (not isinstance(payload, dict) or not payload.get("success")):
        return False, payload if isinstance(payload, dict) else {"raw": payload}, (
            str(payload.get("message") or "perf-metrics success=false")
            if isinstance(payload, dict)
            else "perf-metrics 响应异常"
        )
    if not ok:
        return False, payload if isinstance(payload, dict) else {"raw": payload}, error or "读取 perf-metrics 失败"
    return True, payload if isinstance(payload, dict) else {"success": True, "data": payload}, None

def newapi_admin_target(site: Dict[str, Any]) -> Tuple[str, Dict[str, str]]:
    return normalize_base_url(site["base_url"]), site_newapi_headers(site)

def _newapi_channel_list_items(payload: Any) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """Normalize both list shapes: data=[...] (one-api) and data={items:[...], total,...}."""
    data = payload.get("data") if isinstance(payload, dict) else None
    meta: Dict[str, Any] = {}
    items: List[Dict[str, Any]] = []
    if isinstance(data, list):
        items = [x for x in data if isinstance(x, dict)]
    elif isinstance(data, dict):
        raw_items = data.get("items")
        if isinstance(raw_items, list):
            items = [x for x in raw_items if isinstance(x, dict)]
        for k in ("total", "page", "page_size"):
            if k in data:
                meta[k] = data[k]
    return items, meta

def fetch_newapi_channels(
    site: Dict[str, Any],
    page: int = 0,
    page_size: int = 20,
    keyword: str = "",
) -> Tuple[bool, Dict[str, Any], Optional[str]]:
    base, headers = newapi_admin_target(site)
    keyword = (keyword or "").strip()
    if keyword:
        url = f"{base}/api/channel/search?keyword={quote(keyword)}"
    else:
        url = f"{base}/api/channel/?p={int(page)}&page_size={int(page_size)}"
    ok, payload, error = request_json(url, headers=headers)
    if not ok:
        return False, payload if isinstance(payload, dict) else {"raw": payload}, error or "读取渠道列表失败"
    if not isinstance(payload, dict) or not payload.get("success"):
        msg = str(payload.get("message")) if isinstance(payload, dict) else "渠道列表响应异常"
        return False, payload if isinstance(payload, dict) else {"raw": payload}, msg or "渠道列表 success=false"
    return True, payload, None

def fetch_newapi_channel_detail(
    site: Dict[str, Any], channel_id: int
) -> Tuple[bool, Dict[str, Any], Optional[str]]:
    base, headers = newapi_admin_target(site)
    ok, payload, error = request_json(f"{base}/api/channel/{int(channel_id)}", headers=headers)
    if not ok:
        return False, payload if isinstance(payload, dict) else {"raw": payload}, error or "读取渠道详情失败"
    if not isinstance(payload, dict) or not payload.get("success"):
        msg = str(payload.get("message")) if isinstance(payload, dict) else "渠道详情响应异常"
        return False, payload if isinstance(payload, dict) else {"raw": payload}, msg or "渠道详情 success=false"
    return True, payload, None

def site_newapi_channel_key_headers(site: Dict[str, Any]) -> Dict[str, str]:
    """Use the browser session for protected key reads when available."""
    # 延迟导入：admin_site_service 顶层已导入本模块，顶层互导会循环
    from backend.services.admin_site_service import _admin_browser_auth_headers

    browser_headers = _admin_browser_auth_headers(site)
    headers = browser_headers or site_newapi_headers(site)
    proof = str(site.get("security_proof") or "").strip()
    if proof:
        headers["X-Security-Proof"] = proof
    return headers

def fetch_newapi_channel_key(
    site: Dict[str, Any], channel_id: int, clear_admin_proof: bool = True,
    force_refresh: bool = False,
) -> Tuple[bool, str, Optional[str]]:
    """Read a NewAPI channel key through the dedicated protected endpoint.

    GET /api/channel/:id intentionally clears ``key``. NewAPI exposes the real
    value only through POST /api/channel/:id/key after the main-site account
    has passed its security verification.
    """
    cache_key = "|".join(
        (
            normalize_base_url(str(site.get("base_url") or "")),
            str(site.get("id") or "0"),
            str(int(channel_id)),
        )
    )
    admin_site = is_admin_site_row(site)
    if admin_site and not force_refresh:
        persisted_key = get_cached_admin_channel_key(int(site["id"]), int(channel_id))
        if persisted_key:
            with MAIN_CHANNEL_KEY_REQUEST_LOCK:
                MAIN_CHANNEL_KEY_CACHE[cache_key] = {
                    "key": persisted_key,
                    "updated_monotonic": time.monotonic(),
                }
            return True, persisted_key, None
    if not force_refresh:
        with MAIN_CHANNEL_KEY_REQUEST_LOCK:
            cached = MAIN_CHANNEL_KEY_CACHE.get(cache_key)
            if cached and time.monotonic() - float(cached.get("updated_monotonic") or 0) < MAIN_CHANNEL_KEY_CACHE_TTL_SECONDS:
                return True, str(cached.get("key") or ""), None

    # Admin-site rows carry browser session fields; refresh the short-lived
    # dashboard access token before every protected key read. Monitoring-site
    # rows do not have these fields and continue using their normal PAT.
    # 延迟导入：admin_site_service 顶层已导入本模块，顶层互导会循环
    from backend.services.admin_site_service import (
        ensure_admin_site_browser_session,
        refresh_admin_site_browser_session,
    )

    if "browser_access_token" in site or "browser_session_id" in site:
        browser_ok, browser_error = ensure_admin_site_browser_session(site)
        if not browser_ok:
            return False, "", browser_error or "主站网页登录态不可用"
    base = normalize_base_url(site["base_url"])
    headers = site_newapi_channel_key_headers(site)
    request_gate_key = f"{int(site.get('id') or 0)}|{base}"
    with MAIN_CHANNEL_KEY_REQUEST_LOCK:
        cooldown_until = MAIN_CHANNEL_KEY_RATE_LIMIT_UNTIL.get(request_gate_key, 0.0)
        if cooldown_until > time.monotonic():
            wait_seconds = max(1, int(cooldown_until - time.monotonic()))
            return False, "", f"主站 key 接口触发限流（HTTP 429），已暂停请求，请等待约 {wait_seconds} 秒后再刷新"
        elapsed = time.monotonic() - MAIN_CHANNEL_KEY_LAST_REQUEST_AT.get(request_gate_key, 0.0)
        wait_seconds = MAIN_CHANNEL_KEY_MIN_INTERVAL_SECONDS - elapsed
        if wait_seconds > 0:
            time.sleep(wait_seconds)
        MAIN_CHANNEL_KEY_LAST_REQUEST_AT[request_gate_key] = time.monotonic()
        ok, payload, error = request_json(
            f"{base}/api/channel/{int(channel_id)}/key",
            headers=headers,
            method="POST",
        )
        status, response_code, _response_message = _upstream_response_details(
            payload, error
        )
        if (
            not ok
            and admin_site
            and status == 401
            and response_code == "AUTH_TOKEN_EXPIRED"
        ):
            refreshed, refresh_error = refresh_admin_site_browser_session(
                site, force=True
            )
            if not refreshed:
                return False, "", refresh_error or "主站网页登录态刷新失败"
            headers = site_newapi_channel_key_headers(site)
            ok, payload, error = request_json(
                f"{base}/api/channel/{int(channel_id)}/key",
                headers=headers,
                method="POST",
            )
    if not ok:
        raw_message = ""
        if isinstance(payload, dict):
            if int(payload.get("status") or 0) == 429:
                with MAIN_CHANNEL_KEY_REQUEST_LOCK:
                    MAIN_CHANNEL_KEY_RATE_LIMIT_UNTIL[request_gate_key] = (
                        time.monotonic() + MAIN_CHANNEL_KEY_RATE_LIMIT_COOLDOWN_SECONDS
                    )
                return False, "", "主站 key 接口触发限流（HTTP 429），已暂停请求，请等待约 30 秒后再刷新"
            raw = payload.get("raw")
            try:
                parsed_raw = json.loads(raw) if isinstance(raw, str) and raw else {}
                raw_message = str(parsed_raw.get("message") or "") if isinstance(parsed_raw, dict) else ""
                raw_code = str(parsed_raw.get("code") or "") if isinstance(parsed_raw, dict) else ""
                if raw_code in {"SECURITY_PROOF_REQUIRED", "SECURITY_PROOF_INVALID", "SECURITY_PROOF_EXPIRED"}:
                    if clear_admin_proof:
                        db_execute(
                            """
                            UPDATE admin_sites SET security_proof = NULL,
                                security_proof_verified_at = NULL, updated_at = ?
                            WHERE id = ?
                            """,
                            (utc_now_iso(), int(site["id"])),
                        )
                    with MAIN_CHANNEL_KEY_REQUEST_LOCK:
                        MAIN_CHANNEL_KEY_CACHE.pop(cache_key, None)
                    proof_messages = {
                        "SECURITY_PROOF_REQUIRED": "主站尚未完成 key 读取安全验证",
                        "SECURITY_PROOF_INVALID": "主站网页登录 Session 或安全验证 proof 已失效",
                        "SECURITY_PROOF_EXPIRED": "主站 key 读取安全验证已过期",
                    }
                    return False, "", proof_messages.get(raw_code, "主站安全验证失败")
            except (TypeError, ValueError):
                pass
        return False, "", raw_message or error or "读取主站渠道 key 失败"
    if not isinstance(payload, dict) or not payload.get("success"):
        message = str(payload.get("message")) if isinstance(payload, dict) else "主站 key 接口响应异常"
        return False, "", message or "读取主站渠道 key 失败"
    data = payload.get("data")
    key = data.get("key") if isinstance(data, dict) else ""
    if _channel_key_is_masked(key):
        return False, "", "主站 key 接口没有返回明文 key"
    key = str(key).strip()
    with MAIN_CHANNEL_KEY_REQUEST_LOCK:
        MAIN_CHANNEL_KEY_CACHE[cache_key] = {
            "key": key,
            "updated_monotonic": time.monotonic(),
        }
    if admin_site:
        persist_admin_channel_key(int(site["id"]), int(channel_id), key)
    return True, key, None

def create_newapi_channel(
    site: Dict[str, Any], body: Dict[str, Any]
) -> Tuple[bool, Dict[str, Any], Optional[str]]:
    """新增渠道。

    上游要的是 `{"mode": "single", "channel": {...}}` 这个信封，直接把渠道对象平铺发过去
    会被拒：`{"success": false, "message": "channel cannot be empty"}`。信封形状取自上游
    后台自己的前端（`POST /api/channel` 带 {mode, channel}），"mode" 只认 single/batch。
    """
    base, headers = newapi_admin_target(site)
    envelope = body if "channel" in body and "mode" in body else {"mode": "single", "channel": body}
    ok, payload, error = request_json(f"{base}/api/channel/", headers=headers, payload=envelope, method="POST")
    if not ok:
        return False, payload if isinstance(payload, dict) else {"raw": payload}, error or "创建渠道失败"
    if not isinstance(payload, dict) or not payload.get("success"):
        msg = str(payload.get("message")) if isinstance(payload, dict) else "创建渠道响应异常"
        return False, payload if isinstance(payload, dict) else {"raw": payload}, msg or "创建渠道 success=false"
    return True, payload, None

def resolve_created_newapi_channel_id(
    site: Dict[str, Any], body: Dict[str, Any], existing_ids: Iterable[int]
) -> Tuple[Optional[int], Optional[str]]:
    """Resolve a newly created channel when NewAPI omits its ID in the response.

    The candidate must be new relative to the pre-create list and match the
    submitted name/base URL. Never guess between multiple candidates.
    """
    ok, items, error = fetch_all_newapi_channels(site)
    if not ok:
        return None, error or "创建成功，但刷新渠道列表失败，无法确认新渠道 ID"
    known_ids = {int(value) for value in existing_ids}
    wanted_name = str(body.get("name") or "").strip()
    wanted_base = normalize_base_url(str(body.get("base_url") or ""))
    candidates: List[int] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        try:
            channel_id = int(item.get("id"))
        except (TypeError, ValueError):
            continue
        if channel_id in known_ids:
            continue
        if str(item.get("name") or "").strip() != wanted_name:
            continue
        if normalize_base_url(str(item.get("base_url") or "")) != wanted_base:
            continue
        candidates.append(channel_id)
    if len(candidates) == 1:
        return candidates[0], None
    if not candidates:
        return None, "创建成功，但未能从渠道列表确认新渠道 ID"
    return None, "创建成功，但匹配到多个同名同地址渠道，未安全写入 key 缓存"

def update_newapi_channel(
    site: Dict[str, Any], channel_id: int, patch: Dict[str, Any]
) -> Tuple[bool, Dict[str, Any], Optional[str]]:
    """Read-merge-write: NewAPI PUT replaces the whole channel, so we fetch the
    current object, apply only the provided fields, and send the merged result.

    两个上游事实，踩过才知道（在 aiinfinite.online / NewAPI 上逐字段二分确认）：

    1. `PUT /api/channel/` 只要 body 里带 `status`，就无条件返回
       `{"success": false, "message": "Invalid parameters"}`——渠道状态不允许走通用更新
       接口改。因为我们是「读回整个渠道再回传」，而读回来的对象必然含 status，于是
       **所有**更新（权重 / 优先级 / 编辑保存 / 切换状态）全都失败。所以这里把 status
       从 PUT body 里剔除，改走专用的 `POST /api/channel/:id/status`（见
       set_newapi_channel_status）。
    2. `GET /api/channel/:id` 不返回明文密钥（key 为空串）。空 key 原样回传虽然不是
       上面那个报错的原因，但会有把密钥清空的风险，所以空 key 也一并剔除。

    合并的基底刻意是「上游回来的整个对象」而不是一份白名单：NewAPI 的 PUT 是整体替换，
    白名单漏掉任何一个可写字段（如 settings / header_override / remark）都会把它清空。
    只剔除确定不能回传的：status、空 key，以及 balance / used_quota 这类派生只读字段。
    """
    ok, detail, error = fetch_newapi_channel_detail(site, channel_id)
    if not ok:
        return False, detail, error
    current = detail.get("data") if isinstance(detail, dict) else None
    if not isinstance(current, dict):
        return False, detail, "渠道详情缺少 data，无法合并更新"

    # only touch caller-provided, whitelisted fields; keep everything else intact
    allowed = {
        "name", "status", "weight", "priority", "group", "groups", "models",
        "base_url", "key", "type", "model_mapping", "tag", "test_model",
        "auto_ban", "other", "setting", "settings", "param_override",
        "status_code_mapping", "header_override", "remark", "openai_organization",
    }
    merged = dict(current)
    for field, value in patch.items():
        if field in allowed:
            merged[field] = value
    merged["id"] = int(channel_id)

    # 上游派生的只读字段，回传没有意义，也避免上游对它们做校验
    for derived in (
        "balance", "balance_updated_time", "used_quota", "created_time",
        "test_time", "response_time", "other_info", "channel_info",
    ):
        merged.pop(derived, None)
    # status 不能经此接口更新（见 docstring 第 1 条）
    status_requested = merged.pop("status", None) if "status" in patch else None
    merged.pop("status", None)
    # 上游不回明文密钥；空 key 别回传，避免把密钥清空
    if not str(merged.get("key") or "").strip():
        merged.pop("key", None)

    other_fields = {k: v for k, v in patch.items() if k in allowed and k != "status"}

    status_payload: Optional[Dict[str, Any]] = None
    if status_requested is not None:
        ok, status_payload, error = set_newapi_channel_status(site, channel_id, status_requested)
        if not ok:
            return False, status_payload, error
        if not other_fields:
            return True, status_payload, None

    base, headers = newapi_admin_target(site)
    ok, payload, error = request_json(f"{base}/api/channel/", headers=headers, payload=merged, method="PUT")
    if not ok:
        return False, payload if isinstance(payload, dict) else {"raw": payload}, error or "更新渠道失败"
    if not isinstance(payload, dict) or not payload.get("success"):
        msg = str(payload.get("message")) if isinstance(payload, dict) else "更新渠道响应异常"
        return False, payload if isinstance(payload, dict) else {"raw": payload}, msg or "更新渠道 success=false"
    return True, payload, None

def set_newapi_channel_status(
    site: Dict[str, Any], channel_id: int, status: Any
) -> Tuple[bool, Dict[str, Any], Optional[str]]:
    """启用 / 停用单个渠道：`POST /api/channel/:id/status`，body `{"status": 1|2}`。

    这是上游后台自己前端用的端点（通用 PUT 拒收 status，见 update_newapi_channel）。
    只接受 int 1（启用）/ 2（手动停用）：传字符串 "2"、布尔、0、3 都会被判 Invalid
    parameters（3 = 自动停用是上游自己置的，不允许外部设置）。

    注意：同族的 `POST /api/channel/status/batch` 虽然回 success=true，实测并不落库，
    所以批量启停仍然逐个调用本函数，别改成那个批量端点。
    """
    try:
        wanted = int(status)
    except (TypeError, ValueError):
        return False, {}, f"状态值无效：{status!r}"
    if wanted not in (1, 2):
        return False, {}, f"只支持启用(1)/停用(2)，收到 {wanted}（3=自动停用由上游自行置位）"
    base, headers = newapi_admin_target(site)
    ok, payload, error = request_json(
        f"{base}/api/channel/{int(channel_id)}/status",
        headers=headers,
        payload={"status": wanted},
        method="POST",
    )
    if not ok:
        return False, payload if isinstance(payload, dict) else {"raw": payload}, error or "切换渠道状态失败"
    if not isinstance(payload, dict) or not payload.get("success"):
        msg = str(payload.get("message")) if isinstance(payload, dict) else "切换状态响应异常"
        return False, payload if isinstance(payload, dict) else {"raw": payload}, msg or "切换状态 success=false"
    return True, payload, None

def delete_newapi_channel(
    site: Dict[str, Any], channel_id: int
) -> Tuple[bool, Dict[str, Any], Optional[str]]:
    base, headers = newapi_admin_target(site)
    ok, payload, error = request_json(f"{base}/api/channel/{int(channel_id)}", headers=headers, method="DELETE")
    if not ok:
        return False, payload if isinstance(payload, dict) else {"raw": payload}, error or "删除渠道失败"
    if not isinstance(payload, dict) or not payload.get("success"):
        msg = str(payload.get("message")) if isinstance(payload, dict) else "删除渠道响应异常"
        return False, payload if isinstance(payload, dict) else {"raw": payload}, msg or "删除渠道 success=false"
    return True, payload, None

BATCH_CHANNEL_ACTIONS = {"enable", "disable", "delete", "set_group", "set_tag", "add_tag"}

def batch_channel_operation(
    site: Dict[str, Any],
    action: str,
    ids: Any,
    params: Dict[str, Any],
) -> Tuple[bool, Dict[str, Any], Optional[str]]:
    """对多个渠道执行同一操作。复用单渠道的 read-merge-write / delete 助手，
    逐个执行并汇总每个渠道的成功/失败，任一失败不影响其余。"""
    action = (action or "").strip()
    if action not in BATCH_CHANNEL_ACTIONS:
        return False, {}, f"不支持的批量操作：{action or '(空)'}"
    if not isinstance(ids, list) or not ids:
        return False, {}, "未选择任何渠道"
    group_value = str(params.get("group") or "").strip()
    tag_value = str(params.get("tag") or "").strip()
    if action == "set_group" and not group_value:
        return False, {}, "请提供要设置的分组名"
    if action in ("set_tag", "add_tag") and not tag_value:
        return False, {}, "请提供要设置的标签"

    results: List[Dict[str, Any]] = []
    ok_count = 0
    for raw_id in ids:
        try:
            channel_id = int(raw_id)
        except (TypeError, ValueError):
            results.append({"id": raw_id, "ok": False, "message": "无效渠道 ID"})
            continue
        if action == "delete":
            ok, _payload, error = delete_newapi_channel(site, channel_id)
        else:
            patch: Dict[str, Any] = {}
            if action == "enable":
                patch["status"] = 1
            elif action == "disable":
                patch["status"] = 2
            elif action == "set_group":
                patch["group"] = group_value
            elif action in ("set_tag", "add_tag"):
                patch["tag"] = tag_value
            ok, _payload, error = update_newapi_channel(site, channel_id, patch)
        if ok:
            ok_count += 1
        results.append({"id": channel_id, "ok": ok, "message": None if ok else error})

    return True, {
        "action": action,
        "ok_count": ok_count,
        "fail_count": len(results) - ok_count,
        "total": len(results),
        "results": results,
    }, None

def test_newapi_channel(
    site: Dict[str, Any], channel_id: int
) -> Tuple[bool, Dict[str, Any], Optional[str]]:
    base, headers = newapi_admin_target(site)
    ok, payload, error = request_json(f"{base}/api/channel/test/{int(channel_id)}", headers=headers)
    if not ok:
        return False, payload if isinstance(payload, dict) else {"raw": payload}, error or "测试渠道失败"
    return True, payload if isinstance(payload, dict) else {"success": True, "data": payload}, None

def _newapi_user_token_items(payload: Any) -> List[Dict[str, Any]]:
    data = payload.get("data") if isinstance(payload, dict) else None
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    if isinstance(data, dict):
        items = data.get("items")
        if isinstance(items, list):
            return [item for item in items if isinstance(item, dict)]
    return []

def fetch_all_newapi_user_tokens(
    site: Dict[str, Any], page_size: int = 100, max_pages: int = 50
) -> Tuple[bool, List[Dict[str, Any]], Optional[str]]:
    """读取当前上游用户自己的 API token；这是普通 UserAuth，不需要渠道管理员权限。

    Goes through the unified NewAPI browser executor so browser-mode sites
    also use the full session bundle, and 401/403 failures trigger exactly
    one forced refresh + retry.
    """
    if not site.get("access_token") or not site.get("access_user_id"):
        return False, [], "NewAPI 上游缺少用户认证令牌或用户 ID"
    all_items: List[Dict[str, Any]] = []
    # NewAPI 的 token 页码从 1 开始。p=0 会被服务端兼容成第 1 页，若随后
    # 再请求 p=1 就会把第一页重复计算并提前达到 total，漏掉后续密钥。
    for page in range(1, max_pages + 1):
        query = f"p={page}&page_size={int(page_size)}&size={int(page_size)}"
        ok, payload, error = newapi_browser_request(
            site, "GET", "/api/token/", query=query
        )
        if not ok:
            return False, [], error or "读取 NewAPI 用户 API 密钥列表失败"
        if not isinstance(payload, dict) or not payload.get("success"):
            message = str(payload.get("message") or "") if isinstance(payload, dict) else ""
            return False, [], message or "NewAPI 用户 API 密钥列表响应异常"
        items = _newapi_user_token_items(payload)
        all_items.extend(items)
        data = payload.get("data") if isinstance(payload, dict) else None
        total = data.get("total") if isinstance(data, dict) else None
        if len(items) < page_size or (isinstance(total, int) and len(all_items) >= total):
            break
    return True, all_items, None

def fetch_newapi_user_token_key(
    site: Dict[str, Any], token_id: int
) -> Tuple[bool, str, Optional[str]]:
    ok, payload, error = newapi_browser_request(
        site, "POST", f"/api/token/{int(token_id)}/key"
    )
    if not ok:
        return False, "", error or "读取 NewAPI 用户 API 密钥失败"
    data = payload.get("data") if isinstance(payload, dict) else None
    key = data.get("key") if isinstance(data, dict) else ""
    if not isinstance(payload, dict) or not payload.get("success") or not str(key or "").strip():
        message = str(payload.get("message") or "") if isinstance(payload, dict) else ""
        return False, "", message or "NewAPI 用户 API 密钥响应异常"
    return True, str(key).strip(), None

def _newapi_token_cache_key(site: Dict[str, Any]) -> str:
    """Stable, secret-free cache key for the per-site NewAPI token list.

    Never includes plaintext tokens, cookies or session ids.  The bucket of
    ``browser_access_expires_at`` keeps the cache consistent with the current
    browser session, while the ``auth_mode`` flag invalidates it when the
    user switches back to a system token.
    """
    try:
        expires = int(site.get("browser_access_expires_at") or 0)
    except (TypeError, ValueError):
        expires = 0
    auth_mode = str(site.get("auth_mode") or "token").strip().lower()
    return "|".join(
        (
            normalize_base_url(str(site.get("base_url") or "")),
            str(int(site.get("id") or 0)),
            auth_mode,
            str(expires // 60),
        )
    )

def find_newapi_user_token_by_key(
    site: Dict[str, Any], channel_key: str
) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """用主站渠道 key 在上游当前用户的 /api/token/ 列表中精确匹配分组。"""
    target = normalize_newapi_user_token_key(channel_key)
    if not target:
        return None, "当前渠道没有真实 key，无法查询上游分组"
    cache_key = _newapi_token_cache_key(site)
    with NEWAPI_USER_TOKEN_LIST_LOCK:
        cached = NEWAPI_USER_TOKEN_LIST_CACHE.get(cache_key)
        if cached and time.monotonic() - float(cached.get("updated_monotonic") or 0) < NEWAPI_USER_TOKEN_LIST_CACHE_TTL_SECONDS:
            tokens = cached.get("tokens") or []
        else:
            ok, tokens, error = fetch_all_newapi_user_tokens(site)
            if not ok:
                return None, error or "读取 NewAPI 用户 API 密钥列表失败"
            tokens = [dict(item) for item in tokens if isinstance(item, dict)]
            NEWAPI_USER_TOKEN_LIST_CACHE[cache_key] = {
                "tokens": tokens,
                "updated_monotonic": time.monotonic(),
            }

        target_mask = mask_newapi_user_token_key(target)
        candidates = [
            item
            for item in tokens
            if normalize_newapi_user_token_key(item.get("key")) == target
            or normalize_newapi_user_token_key(item.get("key")) == target_mask
        ]
        if not candidates:
            return None, "当前 key 未在上游 NewAPI 用户 API 密钥列表中找到"

        key_errors: List[str] = []
        for item in candidates:
            full_key = str(item.get("_full_key") or "").strip()
            if not full_key:
                try:
                    token_id = int(item.get("id"))
                except (TypeError, ValueError):
                    continue
                key_ok, full_key, key_error = fetch_newapi_user_token_key(site, token_id)
                if not key_ok:
                    if key_error and key_error not in key_errors:
                        key_errors.append(key_error)
                    continue
                item["_full_key"] = full_key
            if normalize_newapi_user_token_key(full_key) == target:
                return item, None
        if key_errors:
            return None, "读取上游 NewAPI 用户 API 密钥失败：" + "；".join(key_errors)
        return None, "当前 key 未在上游 NewAPI 用户 API 密钥列表中找到"

def fetch_all_newapi_channels(
    site: Dict[str, Any], page_size: int = 100, max_pages: int = 50
) -> Tuple[bool, List[Dict[str, Any]], Optional[str]]:
    """翻页拉取全部渠道（渠道表与左侧分组计数都基于全量）。"""
    all_items: List[Dict[str, Any]] = []
    expected_total: Optional[int] = None
    for page in range(max_pages):
        ok, payload, error = fetch_newapi_channels(site, page, page_size)
        if not ok:
            # A partial list is unsafe for discovery: importing it could make
            # a user believe every upstream URL was considered.
            return False, [], error or "读取 NewAPI 渠道分页失败"
        items, _meta = _newapi_channel_list_items(payload)
        if not isinstance(payload, dict) or "data" not in payload:
            return False, [], "NewAPI 渠道响应缺少 data，拒绝返回截断数据"
        raw_data = payload.get("data")
        if not isinstance(raw_data, list) and not (
            isinstance(raw_data, dict) and isinstance(raw_data.get("items"), list)
        ):
            return False, [], "NewAPI 渠道响应格式无效，拒绝返回截断数据"
        raw_items = (
            raw_data
            if isinstance(raw_data, list)
            else raw_data.get("items") or []
        )
        if any(not isinstance(item, dict) for item in raw_items):
            return False, [], "NewAPI 渠道响应包含无效项，拒绝返回截断数据"
        if isinstance(_meta, dict) and _meta.get("total") is not None:
            try:
                expected_total = max(0, int(_meta.get("total") or 0))
            except (TypeError, ValueError):
                return False, [], "NewAPI 渠道总数无效，拒绝返回截断数据"
        all_items.extend(items)
        if expected_total is not None:
            if len(all_items) >= expected_total:
                return True, all_items, None
            if not items:
                return False, [], "NewAPI 渠道分页提前结束，拒绝返回截断数据"
            continue
        if len(items) < page_size:
            return True, all_items, None
    return False, [], f"NewAPI 渠道超过最大分页页数 {max_pages}，拒绝返回截断数据"

def fetch_newapi_admin_groups(
    site: Dict[str, Any]
) -> Tuple[bool, Dict[str, Any], Optional[str]]:
    """Group name -> {ratio, ratio_type, desc}, for correlating a channel's key
    with the group multiplier it serves under."""
    ok, payload, error = fetch_newapi_groups_with_access_token(
        site["base_url"],
        site.get("access_token") or "",
        site.get("access_user_id") or "",
    )
    if not ok:
        return False, payload if isinstance(payload, dict) else {"raw": payload}, error or "读取分组失败"
    if not isinstance(payload, dict) or not isinstance(payload.get("data"), dict):
        return False, payload if isinstance(payload, dict) else {}, "NewAPI 分组响应格式无效"
    return True, {"success": True, "data": parse_groups_payload(payload)}, None

def fetch_newapi_model_data(
    base_url: str,
    access_token: str = "",
    user_id: str = "",
) -> Tuple[bool, Dict[str, Any], Optional[str]]:
    """Legacy join path: pricing + uptime (kept for old /models UI)."""
    headers = newapi_auth_headers(access_token, user_id)
    normalized_base = normalize_base_url(base_url)
    pricing_ok, pricing_payload, pricing_error = fetch_newapi_pricing(
        normalized_base, access_token=access_token, user_id=user_id
    )
    if not pricing_ok:
        return False, {"pricing": pricing_payload}, pricing_error or "读取 NewAPI 模型配置失败"

    uptime_payload, uptime_error = get_cached_newapi_uptime(normalized_base, headers)

    return True, {
        "success": True,
        "pricing": pricing_payload,
        "uptime": uptime_payload,
        "uptime_error": uptime_error,
    }, None

NEWAPI_QUOTA_PER_UNIT = 500000.0  # NewAPI 默认 QuotaPerUnit：500000 额度 = $1

def fetch_newapi_account(base_url: str, access_token: str, user_id: str = "") -> Tuple[bool, Dict[str, Any], Optional[str]]:
    token = (access_token or "").strip()
    if not token:
        return False, {}, "缺少系统访问令牌，无法读取账户额度"
    ok, payload, error = request_json(
        f"{normalize_base_url(base_url)}/api/user/self",
        headers=newapi_auth_headers(access_token, user_id),
    )
    if not ok:
        return False, payload if isinstance(payload, dict) else {"raw": payload}, error or "读取 /api/user/self 失败"
    if not isinstance(payload, dict) or not payload.get("success"):
        message = str(payload.get("message")) if isinstance(payload, dict) and payload.get("message") else None
        return False, payload if isinstance(payload, dict) else {"raw": payload}, message or "/api/user/self success=false"
    data = payload.get("data")
    return True, data if isinstance(data, dict) else {}, None

def fetch_newapi_account_with_headers(
    base_url: str, headers: Dict[str, str]
) -> Tuple[bool, Dict[str, Any], Optional[str]]:
    ok, payload, error = request_json(
        f"{normalize_base_url(base_url)}/api/user/self",
        headers=dict(headers),
    )
    if not ok:
        return (
            False,
            payload if isinstance(payload, dict) else {"raw": payload},
            error or "读取 /api/user/self 失败",
        )
    if not isinstance(payload, dict) or not payload.get("success"):
        message = (
            str(payload.get("message"))
            if isinstance(payload, dict) and payload.get("message")
            else None
        )
        return (
            False,
            payload if isinstance(payload, dict) else {"raw": payload},
            message or "/api/user/self success=false",
        )
    data = payload.get("data")
    return True, data if isinstance(data, dict) else {}, None

def fetch_newapi_groups_with_headers(
    base_url: str, headers: Dict[str, str]
) -> Tuple[bool, Dict[str, Any], Optional[str]]:
    errors: List[str] = []
    for path in ("/api/user/self/groups", "/api/user/groups"):
        ok, payload, error = request_json(
            f"{normalize_base_url(base_url)}{path}", headers=dict(headers)
        )
        if ok and isinstance(payload, dict) and payload.get("success"):
            return True, payload, None
        errors.append(f"{path}: {newapi_auth_failure_message(payload, error)}")
    return False, {"errors": errors}, "访问令牌分组采集失败：" + "；".join(errors)

def newapi_site_browser_auth_headers(session: Dict[str, Any]) -> Dict[str, str]:
    token = str(session.get("access_token") or "").strip()
    user_id = str(session.get("access_user_id") or "").strip()
    session_id = str(session.get("browser_session_id") or "").strip()
    refresh_cookie = str(
        session.get("browser_cookie")
        or session.get("browser_refresh_cookie")
        or ""
    ).strip()
    headers: Dict[str, str] = {}
    if token:
        normalized_token = token.removeprefix("Bearer ").removeprefix("bearer ").strip()
        headers["Authorization"] = (
            f"Bearer {normalized_token}" if session_id else normalized_token
        )
    if user_id:
        headers["New-Api-User"] = user_id
    if session_id:
        headers["X-Auth-Session"] = session_id
    if refresh_cookie:
        headers["Cookie"] = refresh_cookie
    return headers

def _newapi_status_from_payload(payload: Any) -> Optional[int]:
    """Return the HTTP-like status code from a request_json failure payload."""
    if isinstance(payload, dict):
        raw = payload.get("status")
        if isinstance(raw, int):
            return raw
        if isinstance(raw, str) and raw.isdigit():
            return int(raw)
    return None

def _newapi_site_fallback_request(
    site: Dict[str, Any],
    url: str,
    payload: Optional[Dict[str, Any]],
    method: str,
) -> Tuple[bool, Any, Optional[str]]:
    """浏览器会话不可用时的凭证回退：用户名密码登录 → 系统访问令牌。

    仅在站点保留了回退凭证（login_username/login_password 或
    access_token/access_user_id）时生效；密码登录成功会把新会话落库
    （保留回退凭证），令牌兜底不改变任何持久化状态。
    """
    base = normalize_base_url(str(site.get("base_url") or ""))
    username = str(site.get("login_username") or "").strip()
    password = str(site.get("login_password") or "")
    if base and username and password:
        ok, auth_data, _error = _newapi_password_login_bundle(
            base,
            username,
            password,
            access_user_id=str(site.get("access_user_id") or ""),
            previous_refresh_cookie=str(site.get("browser_refresh_cookie") or ""),
        )
        if ok and auth_data and not auth_data.get("requires_2fa"):
            site_id = int(site.get("id") or 0)
            if site_id > 0:
                try:
                    persist_newapi_site_browser_session(
                        site_id,
                        auth_data,
                        auth_mode=str(site.get("auth_mode") or BROWSER_AUTH_MODE),
                        preserve_login_credentials=True,
                    )
                except Exception:  # noqa: BLE001
                    pass
            site.update(auth_data)
            headers = newapi_site_browser_auth_headers(site)
            ok_req, raw, error = request_json(
                url, headers=headers, payload=payload, method=method
            )
            if ok_req:
                return True, raw, None
            # 密码登录成功但请求失败：继续尝试令牌兜底
    access_token = str(site.get("access_token") or "").strip()
    access_user_id = str(site.get("access_user_id") or "").strip()
    if access_token and access_user_id:
        headers = newapi_auth_headers(access_token, access_user_id)
        proof = str(site.get("security_proof") or "").strip()
        if proof:
            headers["X-Security-Proof"] = proof
        ok_req, raw, error = request_json(
            url, headers=headers, payload=payload, method=method
        )
        if ok_req:
            return True, raw, None
    return False, {}, None

def newapi_browser_request(
    site: Dict[str, Any],
    method: str,
    path: str,
    payload: Optional[Dict[str, Any]] = None,
    query: Optional[str] = None,
    force_refresh_on_401: bool = True,
) -> Tuple[bool, Any, Optional[str]]:
    """Unified NewAPI request executor with auth-mode awareness.

    Behaviour:
    * token mode: one request using the system access token + New-Api-User
      (and X-Security-Proof when present).
    * browser mode: one request using the full browser session bundle.  If
      the upstream returns an explicit 401/403 we ``force=True`` refresh the
      browser session and retry **at most once**.  Network errors, timeouts,
      429 and 5xx never trigger a refresh.
    """
    base = normalize_base_url(str(site.get("base_url") or ""))
    url = f"{base}{path}"
    if query:
        url = f"{url}?{query}" if "?" not in url else f"{url}&{query}"
    auth_mode = str(site.get("auth_mode") or "token").strip().lower()

    if auth_mode in {BROWSER_AUTH_MODE, "password"}:
        # Re-read latest state so a concurrent refresh by another caller is
        # honoured before we pick headers.
        latest = db_query_one(
            """
            SELECT access_token, access_user_id, browser_cookie, browser_refresh_cookie,
                   browser_session_id, browser_access_expires_at, auth_mode
            FROM sites WHERE id = ?
            """,
            (int(site.get("id") or 0),),
        )
        if latest:
            site.update(latest)
        ready, ready_error = ensure_newapi_site_browser_session(site)
        if not ready:
            # 浏览器会话不可用：回退到站点保留的凭证（密码登录 / 令牌）
            fallback_ok, fallback_raw, _fallback_error = _newapi_site_fallback_request(
                site, url, payload, method
            )
            if fallback_ok:
                return True, fallback_raw, None
            return False, {}, ready_error or "登录态已过期，请重新登录"
        headers = newapi_site_browser_auth_headers(site)
        ok, raw, error = request_json(url, headers=headers, payload=payload, method=method)
        if ok:
            return True, raw, None
        status = _newapi_status_from_payload(raw)
        if force_refresh_on_401 and status in (401, 403):
            refreshed, refresh_error = refresh_newapi_site_browser_session(
                site, force=True
            )
            if not refreshed:
                return False, raw, "登录态已失效，请重新验证登录"
            ready, ready_error = ensure_newapi_site_browser_session(site)
            if not ready:
                return False, raw, "请重新网页登录/同步后再试"
            headers = newapi_site_browser_auth_headers(site)
            ok, raw, error = request_json(
                url, headers=headers, payload=payload, method=method
            )
            if ok:
                return True, raw, None
            status = _newapi_status_from_payload(raw)
            if status in (401, 403):
                return False, raw, "登录态已失效，请重新验证登录"
            return False, raw, newapi_auth_failure_message(raw, error)
        return False, raw, newapi_auth_failure_message(raw, error)

    # token / system-token path
    headers = newapi_auth_headers(
        str(site.get("access_token") or ""),
        str(site.get("access_user_id") or ""),
    )
    proof = str(site.get("security_proof") or "").strip()
    if proof:
        headers["X-Security-Proof"] = proof
    ok, raw, error = request_json(url, headers=headers, payload=payload, method=method)
    if ok:
        return True, raw, None
    status = _newapi_status_from_payload(raw)
    if force_refresh_on_401 and status in (401, 403):
        return False, raw, "上游令牌已失效，请刷新或重新录入"
    return False, raw, error or "NewAPI 上游调用失败"

def validate_newapi_site_browser_session(
    base_url: str, session: Dict[str, Any]
) -> Tuple[bool, Dict[str, Any], Optional[str]]:
    access_token = str(session.get("access_token") or "").strip()
    access_user_id = str(session.get("access_user_id") or "").strip()
    browser_cookie = str(session.get("browser_cookie") or "").strip()
    if not access_token and not browser_cookie:
        return False, {}, "没有登录态，请提前登录"
    if not access_user_id:
        return False, {}, "浏览器登录态缺少 NewAPI 用户 ID"
    headers = newapi_site_browser_auth_headers(session)
    account_ok, account, account_error = fetch_newapi_account_with_headers(
        base_url, headers
    )
    if not account_ok:
        return False, {}, account_error or "登录态已过期，请重新登录"
    account_id = str(account.get("id") or "").strip()
    if account_id and account_id != access_user_id:
        return False, {}, "浏览器登录用户与 NewAPI 用户 ID 不匹配"
    groups_ok, groups, groups_error = fetch_newapi_groups_with_headers(
        base_url, headers
    )
    if not groups_ok:
        return False, {}, groups_error or "当前登录态无法读取分组"
    return True, {"account": account, "groups": groups}, None

def persist_newapi_site_browser_session(
    site_id: int,
    session: Dict[str, Any],
    auth_mode: str = BROWSER_AUTH_MODE,
    preserve_login_credentials: bool = False,
) -> None:
    try:
        expires_at = int(session.get("browser_access_expires_at") or 0)
    except (TypeError, ValueError):
        expires_at = 0
    access_token = str(session.get("access_token") or "").strip()
    access_user_id = str(session.get("access_user_id") or "").strip()
    browser_cookie = str(session.get("browser_cookie") or "").strip() or None
    refresh_cookie = str(session.get("browser_refresh_cookie") or "").strip() or None
    session_id = str(session.get("browser_session_id") or "").strip() or None
    now = utc_now_iso()
    db_execute(
        """
        UPDATE sites
        SET auth_mode = ?, login_enabled = 1,
            login_username = CASE WHEN ? THEN login_username ELSE NULL END,
            login_password = CASE WHEN ? THEN login_password ELSE NULL END,
            access_token = ?, access_user_id = ?,
            browser_cookie = ?, browser_refresh_cookie = ?, browser_session_id = ?,
            browser_access_expires_at = ?,
            session_sync_status = 'ready', session_sync_error = NULL,
            session_synced_at = ?, updated_at = ?
        WHERE id = ?
        """,
        (
            auth_mode,
            1 if preserve_login_credentials else 0,
            1 if preserve_login_credentials else 0,
            access_token,
            access_user_id,
            browser_cookie,
            refresh_cookie,
            session_id,
            expires_at,
            now,
            now,
            int(site_id),
        ),
    )

def _newapi_site_browser_session_lock(site_id: int) -> threading.RLock:
    return NEWAPI_SITE_BROWSER_SESSION_LOCKS.lock(site_id)

def _newapi_refresh_cookie_from_response(
    headers: Dict[str, Any], previous: str = ""
) -> str:
    raw_values = headers.get("set-cookie") if isinstance(headers, dict) else []
    if isinstance(raw_values, str):
        raw_values = [raw_values]
    for raw in raw_values or []:
        cookie = SimpleCookie()
        try:
            cookie.load(str(raw))
        except Exception:
            continue
        morsel = cookie.get("new_api_refresh")
        if morsel is not None:
            return f"new_api_refresh={morsel.value}"
    previous_value = str(previous or "").strip()
    return previous_value if previous_value.startswith("new_api_refresh=") else ""

def _newapi_site_browser_auth_data(
    site: Dict[str, Any], payload: Any, response_headers: Dict[str, Any]
) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, dict):
        return None, "NewAPI 刷新没有返回认证数据"
    access_token = str(data.get("access_token") or "").strip()
    user = data.get("user") if isinstance(data.get("user"), dict) else {}
    access_user_id = str(user.get("id") or site.get("access_user_id") or "").strip()
    browser_session = (
        data.get("session") if isinstance(data.get("session"), dict) else {}
    )
    session_id = str(browser_session.get("sid") or "").strip()
    if not access_token or not access_user_id or not session_id:
        return None, "NewAPI 刷新没有返回有效的网页登录态"
    return {
        "access_token": access_token,
        "access_user_id": access_user_id,
        "browser_refresh_cookie": _newapi_refresh_cookie_from_response(
            response_headers, str(site.get("browser_refresh_cookie") or "")
        ),
        "browser_session_id": session_id,
        "browser_access_expires_at": data.get("access_expires_at") or 0,
    }, None

def _newapi_password_login_bundle(
    base_url: str,
    username: str,
    password: str,
    verification_code: str = "",
    access_user_id: str = "",
    previous_refresh_cookie: str = "",
) -> Tuple[bool, Dict[str, Any], Optional[str]]:
    """Log a NewAPI ordinary user in and return a refreshable session bundle."""
    normalized_base = normalize_base_url(base_url)
    if not normalized_base or not username or not password:
        return False, {}, "请填写 NewAPI 用户名和密码"
    ok, payload, error, response_headers = request_json_with_headers(
        f"{normalized_base}/api/user/login",
        payload={"username": username, "password": password},
        method="POST",
    )
    data = payload.get("data") if isinstance(payload, dict) else None
    if isinstance(data, dict) and data.get("require_2fa"):
        flow_token = str(data.get("flow_token") or "").strip()
        if not verification_code:
            return False, {"requires_2fa": True}, "需要 2FA 验证码"
        if not flow_token:
            return False, {}, "2FA 登录流程已失效，请重新验证用户名和密码"
        ok, payload, error, response_headers = request_json_with_headers(
            f"{normalized_base}/api/user/login/2fa",
            payload={"code": verification_code, "flow_token": flow_token},
            method="POST",
        )
    if not ok or not isinstance(payload, dict) or not payload.get("success"):
        return False, {}, newapi_auth_failure_message(payload, error)
    source = {
        "access_user_id": access_user_id,
        "browser_refresh_cookie": previous_refresh_cookie,
    }
    auth_data, auth_error = _newapi_site_browser_auth_data(
        source, payload, response_headers
    )
    if not auth_data:
        return False, {}, auth_error or "NewAPI 登录没有返回有效登录态"
    return True, auth_data, None

def login_newapi_site_with_password(
    site: Dict[str, Any], verification_code: str = ""
) -> Tuple[bool, Dict[str, Any], Optional[str]]:
    if str(site.get("platform") or "newapi").strip().lower() != "newapi":
        return False, {}, "只有 NewAPI 渠道支持用户名密码登录"
    if str(site.get("auth_mode") or "").strip().lower() != "password":
        return False, {}, "请先将认证方式切换为用户名密码"
    site_id = int(site.get("id") or 0)
    if site_id <= 0:
        return False, {}, "渠道记录无效"
    with _newapi_site_browser_session_lock(site_id):
        latest = db_query_one("SELECT * FROM sites WHERE id = ?", (site_id,))
        if latest:
            site.update(latest)
        ok, auth_data, error = _newapi_password_login_bundle(
            str(site.get("base_url") or ""),
            str(site.get("login_username") or "").strip(),
            str(site.get("login_password") or ""),
            verification_code=verification_code,
            access_user_id=str(site.get("access_user_id") or ""),
            previous_refresh_cookie=str(site.get("browser_refresh_cookie") or ""),
        )
        if not ok:
            return False, auth_data, error
        persist_newapi_site_browser_session(
            site_id,
            auth_data,
            auth_mode="password",
            preserve_login_credentials=True,
        )
        site.update(auth_data)
        site["auth_mode"] = "password"
        groups_ok, groups_payload, groups_error = fetch_newapi_groups_for_site(site)
        groups = parse_groups_payload(groups_payload) if groups_ok else {}
        return True, {
            "groups_count": len(groups),
            "warning": None
            if groups_ok
            else newapi_auth_failure_message(groups_payload, groups_error),
        }, None

def probe_newapi_password_login(
    base_url: str, username: str, password: str, verification_code: str = ""
) -> Tuple[bool, Dict[str, Any], Optional[str]]:
    """Validate credentials without persisting them to a monitoring site."""
    ok, auth_data, error = _newapi_password_login_bundle(
        base_url, username, password, verification_code=verification_code
    )
    if not ok:
        return False, auth_data, error
    groups_ok, groups_payload, groups_error = fetch_newapi_groups_with_headers(
        base_url, newapi_site_browser_auth_headers(auth_data)
    )
    groups = parse_groups_payload(groups_payload) if groups_ok else {}
    return groups_ok, {
        "groups_count": len(groups),
        "warning": None
        if groups_ok
        else newapi_auth_failure_message(groups_payload, groups_error),
    }, None if groups_ok else newapi_auth_failure_message(groups_payload, groups_error)

def refresh_newapi_site_browser_session(
    site: Dict[str, Any], force: bool = False
) -> Tuple[bool, Optional[str]]:
    site_id = int(site.get("id") or 0)
    if site_id <= 0:
        return False, "渠道记录无效，无法刷新网页登录态"
    with _newapi_site_browser_session_lock(site_id):
        latest = db_query_one(
            """
            SELECT access_token, access_user_id, browser_refresh_cookie,
                   browser_session_id, browser_access_expires_at
            FROM sites WHERE id = ?
            """,
            (site_id,),
        )
        if latest:
            site.update(latest)
        try:
            expires_at = int(site.get("browser_access_expires_at") or 0)
        except (TypeError, ValueError):
            expires_at = 0
        if not force and expires_at > int(time.time()) + 60:
            return True, None
        refresh_cookie = str(site.get("browser_refresh_cookie") or "").strip()
        session_id = str(site.get("browser_session_id") or "").strip()
        if not refresh_cookie or not session_id:
            return False, "NewAPI 网页登录态缺少 Refresh Cookie 或 Session ID"
        origin = site_origin(str(site.get("base_url") or ""))
        if not origin:
            return False, "渠道 URL 无法生成有效 Origin，请检查渠道地址"
        ok, payload, error, response_headers = request_json_with_headers(
            f"{normalize_base_url(str(site.get('base_url') or ''))}/api/user/auth/refresh",
            headers={
                "Cookie": refresh_cookie,
                "X-Auth-Session": session_id,
                "Origin": origin,
            },
            method="POST",
        )
        if not ok or not isinstance(payload, dict) or not payload.get("success"):
            return False, _admin_browser_refresh_error(payload, error).replace(
                "主站", "NewAPI 站点"
            )
        auth_data, auth_error = _newapi_site_browser_auth_data(
            site, payload, response_headers
        )
        if not auth_data:
            return False, auth_error or "NewAPI 刷新没有返回有效的网页登录态"
        auth_mode = str(site.get("auth_mode") or BROWSER_AUTH_MODE).strip().lower()
        persist_newapi_site_browser_session(
            site_id,
            auth_data,
            auth_mode=auth_mode if auth_mode in {BROWSER_AUTH_MODE, "password"} else BROWSER_AUTH_MODE,
            preserve_login_credentials=auth_mode == "password",
        )
        site.update(auth_data)
        return True, None

def ensure_newapi_site_browser_session(
    site: Dict[str, Any]
) -> Tuple[bool, Optional[str]]:
    access_token = str(site.get("access_token") or "").strip()
    access_user_id = str(site.get("access_user_id") or "").strip()
    browser_cookie = str(site.get("browser_cookie") or "").strip()
    if (not access_token and not browser_cookie) or not access_user_id:
        return False, "没有登录态，请提前登录"
    if browser_cookie:
        return True, None
    session_id = str(site.get("browser_session_id") or "").strip()
    refresh_cookie = str(site.get("browser_refresh_cookie") or "").strip()
    if not session_id and not refresh_cookie:
        return True, None
    if not session_id or not refresh_cookie:
        return False, "NewAPI 网页登录态不完整，请重新登录"
    try:
        expires_at = int(site.get("browser_access_expires_at") or 0)
    except (TypeError, ValueError):
        expires_at = 0
    if expires_at <= 0 or expires_at > int(time.time()) + 60:
        return True, None
    return refresh_newapi_site_browser_session(site)

def fetch_newapi_account_for_site(
    site: Dict[str, Any]
) -> Tuple[bool, Dict[str, Any], Optional[str]]:
    if str(site.get("auth_mode") or "").strip().lower() not in {
        BROWSER_AUTH_MODE,
        "password",
    }:
        return fetch_newapi_account(
            str(site.get("base_url") or ""),
            str(site.get("access_token") or ""),
            str(site.get("access_user_id") or ""),
        )
    ok, payload, error = newapi_browser_request(site, "GET", "/api/user/self")
    if not ok:
        return False, payload if isinstance(payload, dict) else {}, error or "读取 /api/user/self 失败"
    if not isinstance(payload, dict) or not payload.get("success"):
        message = (
            str(payload.get("message"))
            if isinstance(payload, dict) and payload.get("message")
            else None
        )
        return False, payload if isinstance(payload, dict) else {}, message or "/api/user/self success=false"
    data = payload.get("data")
    return True, data if isinstance(data, dict) else {}, None

def fetch_newapi_groups_for_site(
    site: Dict[str, Any]
) -> Tuple[bool, Dict[str, Any], Optional[str]]:
    if str(site.get("auth_mode") or "").strip().lower() not in {
        BROWSER_AUTH_MODE,
        "password",
    }:
        return fetch_newapi_groups_with_access_token(
            str(site.get("base_url") or ""),
            str(site.get("access_token") or ""),
            str(site.get("access_user_id") or ""),
        )
    errors: List[str] = []
    for path in ("/api/user/self/groups", "/api/user/groups"):
        ok, payload, error = newapi_browser_request(site, "GET", path)
        if ok and isinstance(payload, dict) and payload.get("success"):
            return True, payload, None
        message = (
            str(payload.get("message") or "")
            if isinstance(payload, dict)
            else ""
        )
        errors.append(f"{path}: {message or error or 'success=false'}")
    return False, {"errors": errors}, "访问令牌分组采集失败：" + "；".join(errors)

def normalize_newapi_account(data: Dict[str, Any]) -> Dict[str, Any]:
    def to_usd(value: Any) -> Optional[float]:
        try:
            return round(float(value) / NEWAPI_QUOTA_PER_UNIT, 4)
        except (TypeError, ValueError):
            return None

    quota = data.get("quota")
    used_quota = data.get("used_quota")
    return {
        "platform": "newapi",
        "username": str(data.get("username") or ""),
        "group": str(data.get("group") or ""),
        "balance_usd": to_usd(quota),          # NewAPI quota = 剩余额度
        "used_usd": to_usd(used_quota),
        "request_count": data.get("request_count"),
        "raw_quota": quota,
        "raw_used_quota": used_quota,
        "quota_per_unit": NEWAPI_QUOTA_PER_UNIT,
        "subscriptions": [],
    }

def fetch_newapi_groups_with_access_token(base_url: str, access_token: str, user_id: str = "") -> Tuple[bool, Dict[str, Any], Optional[str]]:
    token = (access_token or "").strip()
    if not token:
        return False, {}, "访问令牌为空"

    headers = {
        "Accept": "application/json",
        "User-Agent": "Upstream-Ratio-Watch/1.0",
        "Authorization": token.removeprefix("Bearer ").removeprefix("bearer ").strip(),
    }
    if str(user_id or "").strip():
        headers["New-Api-User"] = str(user_id).strip()
    errors: List[str] = []
    for path in ("/api/user/self/groups", "/api/user/groups"):
        ok, payload, error = request_json(
            f"{normalize_base_url(base_url)}{path}", headers=headers
        )
        if ok and isinstance(payload, dict) and payload.get("success"):
            return True, payload, None
        errors.append(f"{path}: {newapi_auth_failure_message(payload, error)}")

    return False, {"errors": errors}, "访问令牌分组采集失败：" + "；".join(errors)

def probe_newapi_groups(base_url: str) -> Dict[str, Any]:
    ok, payload, error_message = fetch_newapi_groups(base_url)
    if not ok:
        return {
            "success": False,
            "message": error_message or "request failed",
            "groups_count": 0,
            "groups": {},
            "raw": payload,
        }

    groups = parse_groups_payload(payload)
    return {
        "success": True,
        "message": "ok",
        "groups_count": len(groups),
        "groups": groups,
    }


