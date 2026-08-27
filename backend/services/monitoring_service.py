"""Application services for monitoring reads and checks.

The overview builder, site-group collector, detection orchestrator,
account/models payload builders, model-cache helpers and app-settings
accessors were moved here from ``backend.legacy_runtime``.  The legacy
runtime re-exports every public name below so existing ``legacy.<fn>``
callers keep working unchanged.
"""

from __future__ import annotations

import json
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

from backend.core.config import DEFAULT_INTERVAL_MINUTES
from backend.core.normalize import (
    format_change_value,
    normalize_base_url,
    platform_label,
    ratio_direction,
    split_channel_groups,
)
from backend.core.state import (
    BROWSER_AUTH_MODE,
    MODEL_CACHE_LOCK,
    MODEL_CACHE_REFRESHING,
    MODEL_CACHE_TTL_SECONDS,
    MODEL_DATA_CACHE,
    NEWAPI_PERF_SUMMARY_CACHE,
    NEWAPI_PERF_SUMMARY_FRESH_SECONDS,
    NEWAPI_PERF_SUMMARY_REFRESHING,
    NEWAPI_PRICING_CACHE,
    NEWAPI_PRICING_FRESH_SECONDS,
    NEWAPI_PRICING_REFRESHING,
    STOP_EVENT,
)
from backend.core.time import (
    app_now,
    next_check_iso,
    parse_iso_dt,
    stable_hash,
    utc_now_iso,
)
from backend.db.connection import (
    _q,
    db_connection,
    db_execute,
    db_query_all,
    db_query_one,
)
from backend.integrations.newapi import (
    fetch_newapi_account_for_site,
    fetch_newapi_groups,
    fetch_newapi_groups_for_site,
    fetch_newapi_perf_summary_for_site,
    fetch_newapi_pricing_for_site,
    get_cached_newapi_uptime_for_site,
    normalize_newapi_account,
    parse_groups_payload,
    parse_newapi_models_by_group,
    refresh_site_system_access_token,
)
from backend.integrations.sub2api import (
    _strip_sub2api_auth_context,
    fetch_sub2api_account,
    fetch_sub2api_account_by_token,
    fetch_sub2api_groups_by_token,
    fetch_sub2api_model_data,
    fetch_sub2api_user_groups,
    is_sub2api_auth_error,
    merge_sub2api_group_models,
    normalize_sub2api_account,
    parse_sub2api_channel_models,
    parse_sub2api_groups,
    parse_sub2api_monitor_models,
)
from backend.repositories.changes import (
    ChangeRepository,
    diff_groups,
    get_last_success_snapshot,
    persist_snapshot,
)
from backend.repositories.sites import (
    find_monitor_site_for_channel,
    get_site_or_404,
    list_sites_payload,
    site_groups_from_row,
    site_summary,
)
from backend.services.notification_service import notify_changes

# Reconcile-mode constants (mirrored from legacy_runtime to avoid a
# top-level ``from backend.legacy_runtime import`` that would create a
# circular import when legacy_runtime resolves this module lazily).
RECONCILE_MODE_DISABLE = "disable"
RECONCILE_MODE_DELETE = "delete"
RECONCILE_MODES = {RECONCILE_MODE_DISABLE, RECONCILE_MODE_DELETE}
SETTING_RECONCILE_MODE = "main_site_reconcile_mode"

from backend.services.session_sync_service import mark_site_browser_session_expired


# ---------------------------------------------------------------------------
# App settings
# ---------------------------------------------------------------------------

def get_app_setting(name: str, default: str = "") -> str:
    try:
        row = db_query_one("SELECT value FROM app_settings WHERE name = ?", (name,))
    except Exception:
        return default
    if not isinstance(row, dict):
        return default
    value = row.get("value")
    return str(value) if value is not None else default


def set_app_setting(name: str, value: str) -> None:
    db_execute(
        "INSERT INTO app_settings (name, value, updated_at) VALUES (?, ?, ?) "
        "ON DUPLICATE KEY UPDATE value = VALUES(value), updated_at = VALUES(updated_at)",
        (name, value, utc_now_iso()),
    )


def get_main_site_reconcile_mode() -> str:
    """User-selected handling for monitoring sites whose upstream channel vanished.

    ``disable`` (default) flips them to enabled=0 and keeps their history;
    ``delete`` physically removes the site (cascading snapshots + changes).
    """
    mode = (
        get_app_setting(SETTING_RECONCILE_MODE, RECONCILE_MODE_DISABLE)
        .strip()
        .lower()
    )
    return mode if mode in RECONCILE_MODES else RECONCILE_MODE_DISABLE


# ---------------------------------------------------------------------------
# Overview
# ---------------------------------------------------------------------------

def overview_payload() -> Dict[str, Any]:
    with db_connection() as connection:
        sites = db_query_all(
            "SELECT * FROM sites ORDER BY id DESC", connection=connection
        )
        changes = db_query_all(
            "SELECT * FROM changes ORDER BY id DESC LIMIT 8",
            connection=connection,
        )
        totals = {
            "sites_total": len(sites),
            "sites_enabled": sum(1 for s in sites if s["enabled"]),
            "sites_ok": sum(1 for s in sites if s["status"] == "ok"),
            "sites_failed": sum(
                1 for s in sites if s["status"] in {"failed", "warning"}
            ),
            "changes_today": db_query_one(
                "SELECT COUNT(*) AS count FROM changes WHERE created_at >= ?",
                (
                    app_now()
                    .replace(hour=0, minute=0, second=0, microsecond=0)
                    .isoformat(timespec="seconds"),
                ),
                connection=connection,
            )
            or {"count": 0},
        }
        return {
            "stats": {
                "sites_total": totals["sites_total"],
                "sites_enabled": totals["sites_enabled"],
                "sites_ok": totals["sites_ok"],
                "sites_failed": totals["sites_failed"],
                "changes_today": totals["changes_today"]["count"],
            },
            "sites": [site_summary(site, connection=connection) for site in sites],
            "changes": changes,
        }


# ---------------------------------------------------------------------------
# Account payload
# ---------------------------------------------------------------------------

def build_site_account_payload(site: Dict[str, Any]) -> Tuple[int, Dict[str, Any]]:
    """统一账户额度出口：按平台分发到 NewAPI /api/user/self 或 sub2api /api/v1/auth/me。"""
    platform = site.get("platform") or "newapi"
    if platform == "newapi":
        if not site.get("login_enabled"):
            return 409, {
                "success": False,
                "message": "该 NewAPI 站点未开启认证增强监控，无法读取账户额度",
            }
        # 凭据是否齐全交给下层判定：browser / password 模式走统一执行器
        # （支持 cookie-only 会话），token 模式缺令牌时由
        # fetch_newapi_account 返回「缺少系统访问令牌」的准确提示。
        ok, data, error_message = fetch_newapi_account_for_site(site)
        if not ok:
            return 502, {"success": False, "message": error_message or "读取 NewAPI 账户失败"}
        return 200, {
            "success": True,
            "source": "/api/user/self",
            "fetched_at": utc_now_iso(),
            "account": normalize_newapi_account(data),
        }

    if not (site.get("login_enabled") or site.get("access_token") or site.get("login_username")):
        return 409, {"success": False, "message": "该 sub2api 站点未配置登录信息，无法读取账户额度"}
    ok, data, error_message = fetch_sub2api_account(
        site["base_url"],
        username=site.get("login_username") or "",
        password=site.get("login_password") or "",
        auth_mode=site.get("auth_mode") or "password",
        access_token=site.get("access_token") or "",
        refresh_token=site.get("refresh_token") or "",
        site_id=int(site["id"]),
    )
    data = _strip_sub2api_auth_context(data)
    if not ok:
        return 502, {"success": False, "message": error_message or "读取 sub2api 账户失败"}
    return 200, {
        "success": True,
        "source": "/api/v1/auth/me",
        "fetched_at": utc_now_iso(),
        "account": normalize_sub2api_account(data),
    }


# ---------------------------------------------------------------------------
# Group collection & model attachment
# ---------------------------------------------------------------------------

def collect_site_groups(site: Dict[str, Any]) -> Tuple[bool, Dict[str, Dict[str, Any]], Dict[str, Any], str, Optional[str]]:
    platform = site.get("platform") or "newapi"
    if platform == "sub2api":
        ok, payload, error_message = fetch_sub2api_user_groups(
            site["base_url"],
            username=site.get("login_username") or "",
            password=site.get("login_password") or "",
            auth_mode=site.get("auth_mode") or "password",
            access_token=site.get("access_token") or "",
            refresh_token=site.get("refresh_token") or "",
            site_id=int(site.get("id") or 0),
        )
        if (
            not ok
            and str(site.get("auth_mode") or "").strip().lower()
            == BROWSER_AUTH_MODE
            and (
                bool(
                    isinstance(payload, dict)
                    and payload.get("browser_sync_required")
                )
                or is_sub2api_auth_error(payload, error_message)
            )
        ):
            mark_site_browser_session_expired(
                int(site.get("id") or 0),
                error_message or "登录态已过期，请重新登录",
            )
        groups = parse_sub2api_groups(payload.get("data"), payload.get("user_rates")) if ok else {}
        return ok, groups, payload, "/api/v1/groups/available", error_message

    ok, payload, error_message = fetch_newapi_groups(site["base_url"])
    groups = parse_groups_payload(payload) if ok else {}
    return ok, groups, payload, "/api/user/groups", error_message


def attach_group_model_names(site_id: int, groups: Dict[str, Dict[str, Any]]) -> None:
    """把「每个分组当前有哪些模型」写进 groups，用于模型上/下架 diff。

    只使用已有的模型缓存（用户查看模型详情或启动预热时填充），不在检测热路径里
    新增网络请求，避免 sub2api 密码模式频繁登录被上游限流。缓存缺失的分组不写
    models 字段，从而在 diff 时被安全跳过。
    """
    if not groups:
        return
    try:
        cached, _age = get_site_model_cache(site_id)
    except Exception:
        return
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
        info["models"] = sorted({
            str(model.get("name")).strip()
            for model in entries
            if isinstance(model, dict) and str(model.get("name") or "").strip()
        })


# ---------------------------------------------------------------------------
# Site detection
# ---------------------------------------------------------------------------

def detect_site(site_id: int) -> Dict[str, Any]:
    site = db_query_one("SELECT * FROM sites WHERE id = ?", (site_id,))
    if not site:
        return {"success": False, "message": "site not found"}

    checked_at = utc_now_iso()
    ok, new_groups, payload, source, error_message = collect_site_groups(site)
    payload = _strip_sub2api_auth_context(payload)
    latest_success = get_last_success_snapshot(site_id)

    if not ok:
        persist_snapshot(
            site_id,
            status="failed",
            source=source,
            raw_json=json.dumps(payload, ensure_ascii=False),
            error_message=error_message,
            checked_at=checked_at,
        )

        consecutive_failures = int(site["consecutive_failures"] or 0) + 1
        status = "failed" if consecutive_failures >= 3 else "warning"
        next_check_at = next_check_iso(int(site["interval_minutes"] or DEFAULT_INTERVAL_MINUTES))
        db_execute(
            """
            UPDATE sites
            SET status = ?, last_error = ?, last_check_at = ?, next_check_at = ?, consecutive_failures = ?, updated_at = ?
            WHERE id = ?
            """,
            (status, error_message, checked_at, next_check_at, consecutive_failures, checked_at, site_id),
        )
        result: Dict[str, Any] = {
            "success": False,
            "message": error_message,
            "status": status,
        }
        if isinstance(payload, dict):
            if payload.get("code"):
                result["code"] = str(payload["code"])
            if payload.get("browser_sync_required"):
                result["browser_sync_required"] = True
        return result

    attach_group_model_names(site_id, new_groups)
    groups_json = json.dumps(new_groups, ensure_ascii=False, sort_keys=True)
    hash_value = stable_hash(new_groups)
    login_groups: Dict[str, Dict[str, Any]] = {}
    login_groups_json: Optional[str] = None
    login_error: Optional[str] = None
    persist_snapshot(
        site_id,
        status="success",
        source=source,
        groups_json=groups_json,
        raw_json=json.dumps(payload, ensure_ascii=False),
        hash_value=hash_value,
        checked_at=checked_at,
    )

    changes: List[Dict[str, Any]] = []
    if latest_success and latest_success.get("groups_json"):
        try:
            old_groups = json.loads(latest_success["groups_json"])
            if isinstance(old_groups, dict):
                changes = diff_groups(old_groups, new_groups)
        except Exception:
            changes = []

    if (site.get("platform") or "newapi") == "newapi" and site.get("login_enabled") and site.get("access_token") and site.get("access_user_id"):
        login_ok, login_payload, login_error_message = fetch_newapi_groups_for_site(
            site
        )
        if login_ok:
            login_groups = parse_groups_payload(login_payload)
            login_groups_json = json.dumps(login_groups, ensure_ascii=False, sort_keys=True)
            old_login_groups = {}
            if site.get("current_login_groups_json"):
                try:
                    parsed_old_login = json.loads(site["current_login_groups_json"])
                    if isinstance(parsed_old_login, dict):
                        old_login_groups = parsed_old_login
                except Exception:
                    old_login_groups = {}
            login_changes = diff_groups(old_login_groups, login_groups) if old_login_groups else []
            for change in login_changes:
                change["message"] = f"认证增强 {change['message']}"
            changes.extend(login_changes)
        else:
            login_error = login_error_message or "认证增强采集失败"

    for change in changes:
        severity = "info"
        if change["change_type"] in {"group_removed"}:
            severity = "critical"
        elif change["change_type"] == "model_removed_from_group":
            severity = "warning"
        elif change["change_type"] == "ratio_changed":
            percent = change.get("change_percent")
            if isinstance(percent, (int, float)) and percent > 0:
                severity = "warning"

        db_execute(
            """
            INSERT INTO changes
            (site_id, change_type, group_name, old_value, new_value, change_percent, message, created_at, acknowledged)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0)
            """,
            (
                site_id,
                change["change_type"],
                change.get("group_name"),
                json.dumps(change.get("old_value"), ensure_ascii=False) if change.get("old_value") is not None else None,
                json.dumps(change.get("new_value"), ensure_ascii=False) if change.get("new_value") is not None else None,
                change.get("change_percent"),
                change["message"],
                checked_at,
            ),
        )
        change["severity"] = severity

    notify_changes(site, changes, checked_at)

    next_check_at = next_check_iso(int(site["interval_minutes"] or DEFAULT_INTERVAL_MINUTES))
    effective_status = "warning" if login_error else "ok"
    db_execute(
        """
        UPDATE sites
        SET status = ?,
            last_error = NULL,
            last_check_at = ?,
            next_check_at = ?,
            consecutive_failures = 0,
            current_groups_json = ?,
            current_login_groups_json = COALESCE(?, current_login_groups_json),
            login_last_error = ?,
            login_last_check_at = ?,
            updated_at = ?
        WHERE id = ?
        """,
        (
            effective_status,
            checked_at,
            next_check_at,
            groups_json,
            login_groups_json,
            login_error,
            checked_at if site.get("login_enabled") else None,
            checked_at,
            site_id,
        ),
    )
    schedule_model_cache_refresh(site_id)

    return {
        "success": not bool(login_error),
        "message": login_error or "ok",
        "checked_at": checked_at,
        "groups": new_groups,
        "login_groups": login_groups,
        "changes": changes,
    }


# ---------------------------------------------------------------------------
# Models payload
# ---------------------------------------------------------------------------

def build_site_models_payload(site: Dict[str, Any]) -> Tuple[int, Dict[str, Any]]:
    groups = site_groups_from_row(site)
    if not groups:
        return 409, {"success": False, "message": "请先检测站点，获取分组倍率后再查看模型"}

    platform = site.get("platform") or "newapi"
    if platform == "newapi":
        pricing_ok, pricing_payload, pricing_error = fetch_newapi_pricing_for_site(site)
        if not pricing_ok:
            return 502, {"success": False, "message": pricing_error or "读取 NewAPI 模型失败"}
        # 顺带回填 pricing 缓存：站点检测 / models 缓存刷新都走这里，
        # 相当于按检测周期定期更新，悬浮浮层读缓存即可秒开。
        cache_newapi_pricing_payload(int(site["id"]), pricing_payload)
        uptime_payload, uptime_error = get_cached_newapi_uptime_for_site(site)
        payload = {
            "success": True,
            "pricing": pricing_payload,
            "uptime": uptime_payload,
            "uptime_error": uptime_error,
        }
        if not pricing_ok:
            return 502, {"success": False, "message": pricing_error or "读取 NewAPI 模型失败"}
        models_by_group = parse_newapi_models_by_group(payload.get("pricing"), payload.get("uptime"), groups)
        pricing_data = payload.get("pricing", {}).get("data", []) if isinstance(payload.get("pricing"), dict) else []
        uptime_categories = payload.get("uptime", {}).get("data", []) if isinstance(payload.get("uptime"), dict) else []
        uptime_monitors_count = sum(
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
            "monitors_count": uptime_monitors_count,
            "uptime_error": payload.get("uptime_error"),
        }

    ok, payload, error_message = fetch_sub2api_model_data(
        site["base_url"],
        username=site.get("login_username") or "",
        password=site.get("login_password") or "",
        auth_mode=site.get("auth_mode") or "password",
        access_token=site.get("access_token") or "",
        refresh_token=site.get("refresh_token") or "",
        site_id=int(site["id"]),
    )
    payload = _strip_sub2api_auth_context(payload)
    if not ok:
        return 502, {"success": False, "message": error_message or "读取上游模型失败"}

    configured_models = parse_sub2api_channel_models(payload.get("channels"), groups)
    monitored_models, unmatched_models = parse_sub2api_monitor_models(payload.get("monitors"), groups)
    models_by_group = merge_sub2api_group_models(configured_models, monitored_models)
    monitor_items = payload.get("monitors", {}).get("items", []) if isinstance(payload.get("monitors"), dict) else []
    return 200, {
        "success": True,
        "source": ["/api/v1/channels/available", "/api/v1/channel-monitors"],
        "fetched_at": utc_now_iso(),
        "models_by_group": models_by_group,
        "channels_count": len(payload.get("channels") or []),
        "monitors_count": len(monitor_items),
        "unmatched_models_count": len(unmatched_models),
        "channels_error": payload.get("channels_error"),
        "monitors_error": payload.get("monitors_error"),
    }


# ---------------------------------------------------------------------------
# Model cache
# ---------------------------------------------------------------------------

def invalidate_site_model_cache(site_id: int) -> None:
    with MODEL_CACHE_LOCK:
        MODEL_DATA_CACHE.pop(site_id, None)


def cache_site_model_payload(site_id: int, payload: Dict[str, Any]) -> None:
    with MODEL_CACHE_LOCK:
        MODEL_DATA_CACHE[site_id] = {
            "payload": json.loads(json.dumps(payload, ensure_ascii=False)),
            "updated_monotonic": time.monotonic(),
        }


def get_site_model_cache(site_id: int) -> Tuple[Optional[Dict[str, Any]], float]:
    with MODEL_CACHE_LOCK:
        entry = MODEL_DATA_CACHE.get(site_id)
        if not entry or not isinstance(entry.get("payload"), dict):
            return None, float("inf")
        age = time.monotonic() - float(entry.get("updated_monotonic") or 0)
        return json.loads(json.dumps(entry["payload"], ensure_ascii=False)), age


def refresh_site_model_cache(site_id: int) -> Tuple[int, Dict[str, Any]]:
    site = db_query_one("SELECT * FROM sites WHERE id = ?", (site_id,))
    if not site:
        return 404, {"success": False, "message": "site not found"}
    status, payload = build_site_models_payload(site)
    if status == 200 and payload.get("success"):
        cache_site_model_payload(site_id, payload)
    return status, payload


def model_cache_refresh_worker(site_id: int) -> None:
    try:
        refresh_site_model_cache(site_id)
    finally:
        with MODEL_CACHE_LOCK:
            MODEL_CACHE_REFRESHING.discard(site_id)


def schedule_model_cache_refresh(site_id: int) -> None:
    with MODEL_CACHE_LOCK:
        if site_id in MODEL_CACHE_REFRESHING:
            return
        MODEL_CACHE_REFRESHING.add(site_id)
    threading.Thread(target=model_cache_refresh_worker, args=(site_id,), daemon=True).start()


# ---------------------------------------------------------------------------
# NewAPI pricing / perf-summary 缓存（渠道悬浮浮层秒开；弹窗 refresh=1 穿透）
# ---------------------------------------------------------------------------

def cache_newapi_pricing_payload(site_id: int, payload: Dict[str, Any]) -> None:
    with MODEL_CACHE_LOCK:
        NEWAPI_PRICING_CACHE[site_id] = {
            "payload": json.loads(json.dumps(payload, ensure_ascii=False)),
            "updated_monotonic": time.monotonic(),
        }


def get_newapi_pricing_cache(site_id: int) -> Tuple[Optional[Dict[str, Any]], float]:
    with MODEL_CACHE_LOCK:
        entry = NEWAPI_PRICING_CACHE.get(site_id)
        if not entry or not isinstance(entry.get("payload"), dict):
            return None, float("inf")
        age = time.monotonic() - float(entry.get("updated_monotonic") or 0)
        return json.loads(json.dumps(entry["payload"], ensure_ascii=False)), age


def newapi_pricing_cache_refresh_worker(site_id: int) -> None:
    try:
        site = db_query_one("SELECT * FROM sites WHERE id = ?", (site_id,))
        if not site:
            return
        ok, payload, _error = fetch_newapi_pricing_for_site(site)
        if ok and isinstance(payload, dict):
            cache_newapi_pricing_payload(site_id, payload)
    finally:
        with MODEL_CACHE_LOCK:
            NEWAPI_PRICING_REFRESHING.discard(site_id)


def schedule_newapi_pricing_refresh(site_id: int) -> None:
    with MODEL_CACHE_LOCK:
        if site_id in NEWAPI_PRICING_REFRESHING:
            return
        NEWAPI_PRICING_REFRESHING.add(site_id)
    threading.Thread(
        target=newapi_pricing_cache_refresh_worker, args=(site_id,), daemon=True
    ).start()


def _summary_cache_key(site_id: int, hours: int) -> str:
    return f"{site_id}:{hours:g}"


def cache_newapi_perf_summary_payload(
    site_id: int, hours: int, payload: Dict[str, Any]
) -> None:
    with MODEL_CACHE_LOCK:
        NEWAPI_PERF_SUMMARY_CACHE[_summary_cache_key(site_id, hours)] = {
            "payload": json.loads(json.dumps(payload, ensure_ascii=False)),
            "updated_monotonic": time.monotonic(),
        }


def get_newapi_perf_summary_cache(
    site_id: int, hours: int
) -> Tuple[Optional[Dict[str, Any]], float]:
    with MODEL_CACHE_LOCK:
        entry = NEWAPI_PERF_SUMMARY_CACHE.get(_summary_cache_key(site_id, hours))
        if not entry or not isinstance(entry.get("payload"), dict):
            return None, float("inf")
        age = time.monotonic() - float(entry.get("updated_monotonic") or 0)
        return json.loads(json.dumps(entry["payload"], ensure_ascii=False)), age


def newapi_perf_summary_cache_refresh_worker(site_id: int, hours: int) -> None:
    try:
        site = db_query_one("SELECT * FROM sites WHERE id = ?", (site_id,))
        if not site:
            return
        ok, payload, _error = fetch_newapi_perf_summary_for_site(site, hours=hours)
        if ok and isinstance(payload, dict):
            cache_newapi_perf_summary_payload(site_id, hours, payload)
    finally:
        with MODEL_CACHE_LOCK:
            NEWAPI_PERF_SUMMARY_REFRESHING.discard(_summary_cache_key(site_id, hours))


def schedule_newapi_perf_summary_refresh(site_id: int, hours: int) -> None:
    key = _summary_cache_key(site_id, hours)
    with MODEL_CACHE_LOCK:
        if key in NEWAPI_PERF_SUMMARY_REFRESHING:
            return
        NEWAPI_PERF_SUMMARY_REFRESHING.add(key)
    threading.Thread(
        target=newapi_perf_summary_cache_refresh_worker, args=(site_id, hours), daemon=True
    ).start()


def warm_model_cache() -> None:
    for site in db_query_all("SELECT id, platform FROM sites WHERE enabled = 1 ORDER BY id"):
        # models 刷新会顺带回填 pricing 缓存（build_site_models_payload 内）。
        schedule_model_cache_refresh(int(site["id"]))
        if (site.get("platform") or "newapi") == "newapi":
            # 悬浮浮层固定用 hours=1 的 summary；弹窗打开时实时穿透，无需预热。
            schedule_newapi_perf_summary_refresh(int(site["id"]), 1)


# ---------------------------------------------------------------------------
# Service facade
# ---------------------------------------------------------------------------

class MonitoringService:
    def __init__(self, changes: ChangeRepository | None = None) -> None:
        self.changes = changes or ChangeRepository()

    def overview(self) -> dict[str, Any]:
        return overview_payload()

    def list_sites(self):
        return list_sites_payload()

    def list_changes(self, limit: int = 100):
        return self.changes.list(limit)

    def list_site_changes(self, site_id: int, limit: int = 100):
        return self.changes.list_for_site(site_id, limit)

    def snapshots(self, site_id: int):
        return self.changes.snapshots_for_site(site_id)

    def account(self, site: dict[str, Any]):
        return build_site_account_payload(site)

    def refresh_system_token(self, site_id: int) -> Tuple[bool, Optional[str]]:
        """手动重新生成兜底系统访问令牌（会重置上游该账号的系统访问令牌）。"""
        return refresh_site_system_access_token(int(site_id), force=True)

    def check(self, site_id: int):
        return detect_site(site_id)
