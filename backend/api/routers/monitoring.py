"""Monitoring and site-facing API routes."""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from backend.core.normalize import clamp_perf_hours, normalize_base_url
from backend.core.state import (
    MODEL_CACHE_TTL_SECONDS,
    NEWAPI_PERF_SUMMARY_FRESH_SECONDS,
    NEWAPI_PRICING_FRESH_SECONDS,
)
from backend.db.connection import db_query_one
from backend.integrations.http import newapi_auth_failure_message
from backend.integrations.newapi import (
    fetch_newapi_groups_with_access_token,
    fetch_newapi_perf_detail_for_site,
    fetch_newapi_perf_summary_for_site,
    fetch_newapi_pricing_for_site,
    login_newapi_site_with_password,
    parse_groups_payload,
    probe_newapi_groups,
    probe_newapi_password_login,
)
from backend.integrations.sub2api import probe_sub2api_groups
from backend.repositories.admin_sites import get_admin_site_or_404
from backend.repositories.sites import create_site, delete_site, get_site_or_404, update_site
from backend.services.admin_site_service import fetch_admin_site_channels
from backend.services.discovery_service import (
    import_discovered_sites,
    list_site_discovery_links,
)
from backend.services.monitoring_service import (
    MonitoringService,
    cache_newapi_perf_summary_payload,
    cache_newapi_pricing_payload,
    detect_site,
    get_newapi_perf_summary_cache,
    get_newapi_pricing_cache,
    get_site_model_cache,
    invalidate_site_model_cache,
    refresh_site_model_cache,
    schedule_model_cache_refresh,
    schedule_newapi_perf_summary_refresh,
    schedule_newapi_pricing_refresh,
)
from backend.services.sync_service import (
    SYNC_SCOPES,
    auto_sync_admin_site_channels_to_sites,
)


router = APIRouter()
service = MonitoringService()


@router.get("/overview")
def overview() -> dict[str, Any]:
    return service.overview()


@router.get("/sites")
def sites() -> dict[str, Any]:
    data, auto_sync = service.list_sites()
    return {"data": data, "auto_sync": auto_sync}


@router.get("/changes")
def changes(limit: int = 100) -> dict[str, Any]:
    return {"data": service.list_changes(limit)}


@router.get("/sites/{site_id}/snapshots")
def snapshots(site_id: int) -> dict[str, Any]:
    return {"data": service.snapshots(site_id)}


@router.get("/sites/{site_id}/changes")
def site_changes(site_id: int, limit: int = 100) -> dict[str, Any]:
    return {"data": service.list_site_changes(site_id, limit)}


@router.get("/sites/{site_id}/account")
def account(site_id: int):
    site, error, status = get_site_or_404(site_id)
    if error:
        return JSONResponse(error, status_code=status)
    response_status, payload = service.account(site)
    return JSONResponse(payload, status_code=response_status)


@router.post("/sites/{site_id}/system-token")
def refresh_system_token(site_id: int):
    """手动重新生成兜底系统访问令牌（会重置上游该账号的系统访问令牌）。"""
    site, error, status = get_site_or_404(site_id)
    if error:
        return JSONResponse(error, status_code=status)
    if (site.get("platform") or "newapi") != "newapi":
        return JSONResponse(
            {"success": False, "message": "只有 NewAPI 站点支持兜底系统访问令牌"},
            status_code=400,
        )
    if not int(site.get("system_token_fallback_enabled") or 0):
        return JSONResponse(
            {"success": False, "message": "请先在渠道编辑中开启「会话失效时用系统访问令牌兜底」"},
            status_code=409,
        )
    try:
        ok, error_message = service.refresh_system_token(site_id)
    except Exception as exc:  # noqa: BLE001
        return JSONResponse(
            {"success": False, "message": f"生成兜底令牌失败：{exc}"},
            status_code=500,
        )
    if not ok:
        return JSONResponse(
            {"success": False, "message": error_message or "生成兜底令牌失败"},
            status_code=502,
        )
    return {"success": True, "message": "兜底系统访问令牌已生成并保存"}


@router.get("/sites/{site_id}/discovery-links")
def discovery_links(site_id: int):
    site, error, status = get_site_or_404(site_id)
    if error:
        return JSONResponse(error, status_code=status)
    return {"success": True, "data": list_site_discovery_links(site_id)}


@router.get("/sites/{site_id}/models")
def models(site_id: int, refresh: bool = False):
    site, error, status = get_site_or_404(site_id)
    if error:
        return JSONResponse(error, status_code=status)
    if refresh:
        # 倍率弹窗「点击查看」实时穿透：同步刷新并回填缓存。
        try:
            status_code, payload = refresh_site_model_cache(site_id)
        except Exception as exc:  # noqa: BLE001
            return JSONResponse(
                {"success": False, "message": f"刷新模型缓存失败：{exc}"},
                status_code=500,
            )
        return JSONResponse(payload, status_code=status_code)
    try:
        cached_payload, cache_age = get_site_model_cache(site_id)
    except Exception as exc:  # noqa: BLE001
        return JSONResponse(
            {"success": False, "message": f"读取模型缓存失败：{exc}"},
            status_code=500,
        )
    if cached_payload is not None:
        cached_payload["cache_hit"] = True
        cached_payload["cache_age_seconds"] = round(cache_age, 1)
        if cache_age >= MODEL_CACHE_TTL_SECONDS:
            schedule_model_cache_refresh(site_id)
            cached_payload["refreshing"] = True
        return cached_payload
    try:
        status_code, payload = refresh_site_model_cache(site_id)
    except Exception as exc:  # noqa: BLE001
        return JSONResponse(
            {"success": False, "message": f"刷新模型缓存失败：{exc}"},
            status_code=500,
        )
    payload["cache_hit"] = False
    return JSONResponse(payload, status_code=status_code)


@router.get("/sites/{site_id}/pricing")
def pricing(site_id: int, refresh: bool = False):
    site, error, status = get_site_or_404(site_id)
    if error:
        return JSONResponse(error, status_code=status)
    if (site.get("platform") or "newapi") != "newapi":
        return JSONResponse(
            {"success": False, "message": "pricing 仅支持 NewAPI 站点"},
            status_code=400,
        )

    def _finalize(payload: Any) -> JSONResponse:
        # 统一补站点上下文字段（缓存与实时两条路径共用同一形状）。
        if isinstance(payload, dict):
            payload = dict(payload)
            payload["site_id"] = site_id
            payload["base_url"] = site["base_url"]
            auth_mode = str(site.get("auth_mode") or "token").strip().lower()
            if auth_mode == "browser":
                payload["auth_used"] = bool(
                    site.get("browser_access_token") and site.get("browser_session_id")
                )
            else:
                payload["auth_used"] = bool(
                    site.get("access_token") and site.get("access_user_id")
                )
        return JSONResponse(payload)

    # 悬浮浮层路径：有缓存直接回（秒开），条目过旧时调度后台刷新（SWR）。
    if not refresh:
        cached, cache_age = get_newapi_pricing_cache(site_id)
        if cached is not None:
            cached = dict(cached)
            cached["cache_hit"] = True
            cached["cache_age_seconds"] = round(cache_age, 1)
            if cache_age >= NEWAPI_PRICING_FRESH_SECONDS:
                schedule_newapi_pricing_refresh(site_id)
                cached["refreshing"] = True
            return _finalize(cached)
    # 点击查看（refresh=1）或缓存冷启动：实时拉取并回填缓存。
    try:
        ok, payload, error_message = fetch_newapi_pricing_for_site(site)
    except Exception as exc:  # noqa: BLE001
        return JSONResponse(
            {"success": False, "message": f"读取 pricing 失败：{exc}"},
            status_code=500,
        )
    if not ok:
        return JSONResponse(
            {"success": False, "message": error_message, "upstream": payload},
            status_code=502,
        )
    if isinstance(payload, dict):
        cache_newapi_pricing_payload(site_id, payload)
    return _finalize(payload)


@router.get("/sites/{site_id}/perf-metrics/summary")
def perf_summary(site_id: int, hours: str = "24", refresh: bool = False):
    site, error, status = get_site_or_404(site_id)
    if error:
        return JSONResponse(error, status_code=status)
    if (site.get("platform") or "newapi") != "newapi":
        return JSONResponse(
            {"success": False, "message": "perf-metrics 仅支持 NewAPI 站点"},
            status_code=400,
        )
    clamped_hours = clamp_perf_hours(hours, 24)

    def _finalize(payload: Any) -> JSONResponse:
        if isinstance(payload, dict):
            payload = dict(payload)
            payload["site_id"] = site_id
            payload["hours"] = clamped_hours
            payload["note"] = (
                "summary 为全站模型级汇总，不随 group 筛选变化；"
                "分组仅用于 pricing 过滤模型名单（与 NewAPI 前端列表一致）"
            )
        return JSONResponse(payload)

    # 悬浮浮层路径：缓存直回 + 过旧后台刷新（SWR）；summary 按站点 + hours 分键。
    if not refresh:
        cached, cache_age = get_newapi_perf_summary_cache(site_id, clamped_hours)
        if cached is not None:
            cached = dict(cached)
            cached["cache_hit"] = True
            cached["cache_age_seconds"] = round(cache_age, 1)
            if cache_age >= NEWAPI_PERF_SUMMARY_FRESH_SECONDS:
                schedule_newapi_perf_summary_refresh(site_id, clamped_hours)
                cached["refreshing"] = True
            return _finalize(cached)
    # 点击查看（refresh=1）或缓存冷启动：实时拉取并回填缓存。
    try:
        ok, payload, error_message = fetch_newapi_perf_summary_for_site(
            site, hours=clamped_hours
        )
    except Exception as exc:  # noqa: BLE001
        return JSONResponse(
            {"success": False, "message": f"读取 perf-metrics summary 失败：{exc}"},
            status_code=500,
        )
    if not ok:
        return JSONResponse(
            {"success": False, "message": error_message, "upstream": payload},
            status_code=502,
        )
    if isinstance(payload, dict):
        cache_newapi_perf_summary_payload(site_id, clamped_hours, payload)
    return _finalize(payload)


@router.get("/sites/{site_id}/perf-metrics")
def perf_metrics(site_id: int, model: str = "", group: str = "", hours: str = "24"):
    site, error, status = get_site_or_404(site_id)
    if error:
        return JSONResponse(error, status_code=status)
    if (site.get("platform") or "newapi") != "newapi":
        return JSONResponse(
            {"success": False, "message": "perf-metrics 仅支持 NewAPI 站点"},
            status_code=400,
        )
    clamped_hours = clamp_perf_hours(hours, 24)
    model_name = model.strip()
    if not model_name:
        return JSONResponse(
            {"success": False, "message": "model is required"},
            status_code=400,
        )
    try:
        ok, payload, error_message = fetch_newapi_perf_detail_for_site(
            site,
            model_name=model_name,
            hours=clamped_hours,
            group=group,
        )
    except Exception as exc:  # noqa: BLE001
        return JSONResponse(
            {"success": False, "message": f"读取 perf-metrics 失败：{exc}"},
            status_code=500,
        )
    if not ok:
        return JSONResponse(
            {"success": False, "message": error_message, "upstream": payload},
            status_code=502,
        )
    if isinstance(payload, dict):
        payload = dict(payload)
        payload["site_id"] = site_id
        payload["hours"] = clamped_hours
        payload["requested_model"] = model_name
        payload["requested_group"] = group or None
    return payload


@router.post("/sites/sync")
async def sync_sites(request: Request):
    """手动触发主站渠道同步（双向对账）。"""
    body_bytes = await request.body()
    try:
        body = json.loads(body_bytes) if body_bytes else {}
    except (json.JSONDecodeError, UnicodeDecodeError):
        body = {}
    if not isinstance(body, dict):
        body = {}

    raw_admin_site_id = body.get("admin_site_id")
    admin_site_id: int | None = None
    if raw_admin_site_id not in (None, ""):
        try:
            admin_site_id = int(raw_admin_site_id)
        except (TypeError, ValueError):
            return JSONResponse(
                {"success": False, "message": "管理站点 ID 无效"},
                status_code=400,
            )
        if admin_site_id <= 0:
            return JSONResponse(
                {"success": False, "message": "管理站点 ID 无效"},
                status_code=400,
            )

    raw_scope = body.get("scope")
    scope: str | None = None
    if raw_scope not in (None, ""):
        scope = str(raw_scope).strip().lower()
        if scope not in SYNC_SCOPES:
            return JSONResponse(
                {"success": False, "message": f"同步范围无效：{scope}"},
                status_code=400,
            )
    raw_channel_ids = body.get("channel_ids")
    channel_ids: list[int] = []
    if isinstance(raw_channel_ids, list):
        for raw in raw_channel_ids:
            try:
                channel_id = int(raw)
            except (TypeError, ValueError):
                continue
            if channel_id > 0:
                channel_ids.append(channel_id)
    if scope == "selected":
        if admin_site_id is None:
            return JSONResponse(
                {"success": False, "message": "勾选渠道同步只支持单个主站"},
                status_code=400,
            )
        if not channel_ids:
            return JSONResponse(
                {"success": False, "message": "请至少勾选一个渠道"},
                status_code=400,
            )

    # 1. 跑同步
    try:
        results = await run_in_threadpool(
            auto_sync_admin_site_channels_to_sites, admin_site_id, scope, channel_ids,
        )
    except ValueError as exc:
        return JSONResponse(
            {"success": False, "message": str(exc)},
            status_code=400,
        )

    # 2. 汇总同步结果（和原有逻辑一致）
    imported = sum(
        int(entry.get("imported") or 0)
        for entry in results
        if isinstance(entry, dict)
    )
    conflicts = sum(
        int(entry.get("conflict_count") or 0)
        for entry in results
        if isinstance(entry, dict)
    )
    channels_changed = any(
        bool(entry.get("channels_changed"))
        for entry in results
        if isinstance(entry, dict)
    )
    groups_changed = any(
        bool(entry.get("groups_changed"))
        for entry in results
        if isinstance(entry, dict)
    )
    keys_refreshed = sum(
        int(entry.get("keys_refreshed") or 0)
        for entry in results
        if isinstance(entry, dict)
    )
    keys_changed = sum(
        int(entry.get("keys_changed") or 0)
        for entry in results
        if isinstance(entry, dict)
    )
    keys_failed = sum(
        int(entry.get("keys_failed") or 0)
        for entry in results
        if isinstance(entry, dict)
    )
    key_errors: list[str] = []
    for entry in results:
        for message in (entry.get("key_errors") or []) if isinstance(entry, dict) else []:
            if message not in key_errors:
                key_errors.append(str(message))
    reconcile = next(
        (
            entry
            for entry in results
            if isinstance(entry, dict)
            and entry.get("status") == "reconcile"
        ),
        {},
    )
    failed = [
        entry
        for entry in results
        if isinstance(entry, dict)
        and entry.get("status") in {"fetch_failed", "sync_failed", "error"}
    ]

    excluded = sum(
        int(entry.get("excluded_channels") or 0)
        for entry in results
        if isinstance(entry, dict)
    )

    platform_deleted = sum(
        int(entry.get("platform_deleted") or 0)
        for entry in results
        if isinstance(entry, dict)
    )

    return JSONResponse(
        {
            "success": True,
            "data": results,
            "scope": scope or "auto",
            "platform_deleted": platform_deleted,
            "channels_changed": channels_changed,
            "groups_changed": groups_changed,
            "keys_refreshed": keys_refreshed,
            "keys_changed": keys_changed,
            "keys_failed": keys_failed,
            "key_errors": key_errors[:3],
            "imported": imported,
            "conflicts": conflicts,
            "excluded": excluded,
            "disabled": int(reconcile.get("disabled") or 0),
            "reenabled": int(reconcile.get("reenabled") or 0),
            "deleted": int(reconcile.get("deleted") or 0),
            "failed": len(failed),
        },
    )


@router.post("/sites")
async def create_site_route(request: Request):
    """创建监控站点。"""
    try:
        body = await request.json()
    except Exception:
        return JSONResponse(
            {"success": False, "message": "请求 JSON 无效"},
            status_code=400,
        )
    body = body if isinstance(body, dict) else {}
    try:
        ok, site_id, error, existed = await run_in_threadpool(create_site, body)
    except Exception as exc:  # noqa: BLE001
        return JSONResponse(
            {"success": False, "message": f"创建站点失败：{exc}"},
            status_code=500,
        )
    if not ok:
        if existed:
            # 手动创建撞 sites.base_url 唯一键:409 让前端明确提示「已存在」,
            # 不再静默复用已有站点丢弃新配置(P1-5)
            return JSONResponse(
                {
                    "success": False,
                    "code": "site_exists",
                    "site_id": site_id,
                    "message": error,
                },
                status_code=409,
            )
        return JSONResponse(
            {"success": False, "message": error},
            status_code=400,
        )
    return {"success": True, "id": site_id}


@router.put("/sites/{site_id}")
async def update_site_route(site_id: int, request: Request):
    """更新监控站点配置。"""
    try:
        body = await request.json()
    except Exception:
        return JSONResponse(
            {"success": False, "message": "请求 JSON 无效"},
            status_code=400,
        )
    body = body if isinstance(body, dict) else {}
    try:
        ok, error = await run_in_threadpool(update_site, site_id, body)
    except Exception as exc:  # noqa: BLE001
        return JSONResponse(
            {"success": False, "message": f"更新站点失败：{exc}"},
            status_code=500,
        )
    if not ok:
        return JSONResponse(
            {"success": False, "message": error},
            status_code=400,
        )
    invalidate_site_model_cache(site_id)
    schedule_model_cache_refresh(site_id)
    return {"success": True}


@router.delete("/sites/{site_id}")
def delete_site_route(site_id: int):
    """删除监控站点。"""
    try:
        delete_site(site_id)
    except Exception as exc:  # noqa: BLE001
        return JSONResponse(
            {"success": False, "message": f"删除站点失败：{exc}"},
            status_code=500,
        )
    invalidate_site_model_cache(site_id)
    return {"success": True}


@router.post("/sites/{site_id}/check")
def check_site(site_id: int):
    """手动检测站点（采集分组倍率快照 + diff）。"""
    try:
        result = detect_site(site_id)
    except Exception as exc:  # noqa: BLE001
        return JSONResponse(
            {"success": False, "message": f"检测失败：{exc}"},
            status_code=500,
        )
    return result


@router.post("/sites/discovery-import")
async def discovery_import(request: Request):
    """从主站渠道发现结果导入/复用监控站点。"""
    try:
        body = await request.json()
    except Exception:
        return JSONResponse(
            {"success": False, "message": "请求 JSON 无效"},
            status_code=400,
        )
    body = body if isinstance(body, dict) else {}
    try:
        admin_site_id = int(body.get("admin_site_id") or 0)
    except (TypeError, ValueError):
        return JSONResponse(
            {"success": False, "message": "管理站点 ID 无效"},
            status_code=400,
        )
    if admin_site_id <= 0:
        return JSONResponse(
            {"success": False, "message": "管理站点 ID 无效"},
            status_code=400,
        )
    site, error, status = get_admin_site_or_404(admin_site_id)
    if error:
        return JSONResponse(error, status_code=status)
    if str(site.get("platform") or "newapi").strip().lower() != "newapi":
        return JSONResponse(
            {"success": False, "message": "主站渠道发现导入仅支持 NewAPI"},
            status_code=405,
        )
    try:
        result = await run_in_threadpool(import_discovered_sites, site, body)
    except Exception as exc:  # noqa: BLE001
        return JSONResponse(
            {"success": False, "message": f"导入失败：{exc}"},
            status_code=500,
        )
    if isinstance(result, dict) and result.get("error"):
        error_status = 413 if result.get("error") == "too_many_items" else 400
        return JSONResponse(
            {
                "success": False,
                "message": result.get("message") or "导入请求无效",
                "error": result.get("error"),
            },
            status_code=error_status,
        )
    return {"success": True, "data": result or []}


@router.post("/check-connection")
async def check_connection(request: Request):
    """检测上游站点连通性（探测分组列表）。"""
    try:
        body = await request.json()
    except Exception:
        return JSONResponse(
            {"success": False, "message": "请求 JSON 无效"},
            status_code=400,
        )
    body = body if isinstance(body, dict) else {}
    base_url = normalize_base_url(str(body.get("base_url") or ""))
    platform = str(body.get("platform") or "newapi").strip().lower()
    if not base_url:
        return JSONResponse(
            {"success": False, "message": "base_url required"},
            status_code=400,
        )
    try:
        if platform == "sub2api":
            result = await run_in_threadpool(
                probe_sub2api_groups,
                base_url,
                username=str(body.get("login_username") or "").strip(),
                password=str(body.get("login_password") or ""),
                auth_mode=str(body.get("auth_mode") or "password").strip().lower(),
                access_token=str(body.get("access_token") or "").strip(),
                refresh_token=str(body.get("refresh_token") or "").strip(),
            )
        else:
            result = await run_in_threadpool(probe_newapi_groups, base_url)
    except Exception as exc:  # noqa: BLE001
        return JSONResponse(
            {"success": False, "message": f"连接检测失败：{exc}"},
            status_code=500,
        )
    return result


@router.post("/check-login")
async def check_login(request: Request):
    """检测上游站点登录凭证（密码或访问令牌）。"""
    try:
        body = await request.json()
    except Exception:
        return JSONResponse(
            {"success": False, "message": "请求 JSON 无效"},
            status_code=400,
        )
    body = body if isinstance(body, dict) else {}
    base_url = normalize_base_url(str(body.get("base_url") or ""))
    auth_mode = str(body.get("auth_mode") or "token").strip().lower()
    if auth_mode == "password":
        username = str(body.get("login_username") or "").strip()
        password = str(body.get("login_password") or "")
        verification_code = str(body.get("two_factor_code") or "").strip()
        if not base_url or not username or not password:
            return JSONResponse(
                {"success": False, "message": "Base URL、用户名和密码都需要填写"},
                status_code=400,
            )
        try:
            groups_ok, result, groups_error = await run_in_threadpool(
                probe_newapi_password_login,
                base_url,
                username,
                password,
                verification_code,
            )
        except Exception as exc:  # noqa: BLE001
            return JSONResponse(
                {"success": False, "message": f"登录检测失败：{exc}"},
                status_code=500,
            )
        return {
            "success": groups_ok,
            "requires_2fa": bool(result.get("requires_2fa")),
            "message": groups_error or result.get("warning") or "用户名密码验证成功",
            "groups_count": result.get("groups_count", 0),
        }
    access_token = str(body.get("access_token") or "").strip()
    access_user_id = str(body.get("access_user_id") or "").strip()
    if not base_url or not access_token or not access_user_id:
        return JSONResponse(
            {"success": False, "message": "Base URL、系统访问令牌、NewAPI 用户 ID 都需要填写"},
            status_code=400,
        )
    try:
        groups_ok, groups_payload, groups_error = await run_in_threadpool(
            fetch_newapi_groups_with_access_token,
            base_url,
            access_token,
            access_user_id,
        )
    except Exception as exc:  # noqa: BLE001
        return JSONResponse(
            {"success": False, "message": f"令牌检测失败：{exc}"},
            status_code=500,
        )
    groups = parse_groups_payload(groups_payload) if groups_ok else {}
    return {
        "success": groups_ok,
        "message": (
            newapi_auth_failure_message(groups_payload, groups_error)
            if not groups_ok
            else "访问令牌验证成功"
        ),
        "groups_count": len(groups),
        "groups": groups,
    }


@router.post("/sites/{site_id}/auth/login")
async def site_password_login(site_id: int, request: Request):
    """NewAPI 密码登录（获取站点管理令牌）。"""
    site = db_query_one("SELECT * FROM sites WHERE id = ?", (site_id,))
    if not site:
        return JSONResponse(
            {"success": False, "message": "site not found"}, status_code=404
        )
    try:
        body = await request.json()
    except Exception:
        body = {}
    body = body if isinstance(body, dict) else {}
    ok, result, error = await run_in_threadpool(
        login_newapi_site_with_password,
        site,
        str(body.get("two_factor_code") or "").strip(),
    )
    if not ok:
        return JSONResponse({
            "success": False,
            "requires_2fa": bool(result.get("requires_2fa")),
            "message": error or "NewAPI 登录失败",
        })
    return JSONResponse({
        "success": True,
        "message": "NewAPI 用户登录成功",
        "groups_count": result.get("groups_count", 0),
        "warning": result.get("warning"),
    })
