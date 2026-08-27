"""Admin-site management routes (CRUD + channel management).

All admin-site sub-paths (channels, groups, channel-mappings, key refresh,
batch ops, ...) are now native FastAPI routes — no longer served by the
legacy dispatcher.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from backend.core.normalize import _channel_key_is_masked, mask_channel_in_place
from backend.core.time import utc_now_iso
from backend.db.connection import db_execute
from backend.integrations.newapi import (
    aggregate_newapi_channel_candidates,
    batch_channel_operation,
    create_newapi_channel,
    delete_newapi_channel,
    enrich_channel_candidates_with_sites,
    fetch_all_newapi_channels,
    fetch_newapi_channel_key,
    resolve_created_newapi_channel_id,
    test_newapi_channel,
)
from backend.integrations.sub2api import (
    Sub2ApiUpstreamError,
    sub2api_proxy_error_response,
    validate_sub2api_admin_channel_patch,
)
from backend.repositories.admin_sites import (
    admin_site_platform,
    clear_admin_channel_key,
    create_admin_site,
    get_admin_site_or_404,
    get_cached_admin_channel_key,
    list_admin_sites_payload,
    sync_admin_channel_key,
    update_admin_site,
)
from backend.services.admin_site_service import (
    fetch_admin_site_channel_detail,
    fetch_admin_site_channels,
    fetch_admin_site_groups,
    test_admin_site_connection,
    update_admin_site_channel,
    verify_admin_site_channel_key_access,
)
from backend.services.channel_match_service import (
    channel_upstream_binding_payload,
    get_channel_upstream_binding,
    list_channel_upstream_bindings,
    match_channel_upstream_binding,
)


router = APIRouter()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _read_json_body(request: Request) -> dict:
    try:
        body = await request.json()
    except Exception:
        body = {}
    return body if isinstance(body, dict) else {}


def _platform(site: dict) -> str:
    return str(site.get("platform") or "newapi").strip().lower()


def _delete_admin_site(admin_site_id: int) -> None:
    db_execute(
        "DELETE FROM channel_upstream_bindings WHERE admin_site_id = ?",
        (admin_site_id,),
    )
    db_execute(
        "DELETE FROM admin_channel_keys WHERE admin_site_id = ?",
        (admin_site_id,),
    )
    db_execute("DELETE FROM admin_sites WHERE id = ?", (admin_site_id,))


# ---------------------------------------------------------------------------
# Admin-site CRUD
# ---------------------------------------------------------------------------

@router.get("/admin/sites")
async def list_admin_sites():
    data = await run_in_threadpool(list_admin_sites_payload)
    return JSONResponse({"data": data})


@router.post("/admin/sites")
async def create_admin_site_route(request: Request):
    body = await _read_json_body(request)
    ok, result, error = await run_in_threadpool(create_admin_site, body)
    if not ok:
        if isinstance(error, Sub2ApiUpstreamError):
            status, response = sub2api_proxy_error_response(
                error.payload, str(error), "sub2api 主站登录验证失败"
            )
            return JSONResponse(response, status_code=status)
        return JSONResponse(
            {"success": False, "message": error}, status_code=400
        )
    return JSONResponse({"success": True, "id": result})


@router.put("/admin/sites/{admin_site_id}")
async def update_admin_site_route(admin_site_id: int, request: Request):
    body = await _read_json_body(request)
    ok, error = await run_in_threadpool(update_admin_site, admin_site_id, body)
    if not ok:
        if isinstance(error, Sub2ApiUpstreamError):
            status, response = sub2api_proxy_error_response(
                error.payload, str(error), "sub2api 主站登录验证失败"
            )
            return JSONResponse(response, status_code=status)
        status_code = (
            409
            if error and "平台" in error and "不可修改" in error
            else 400
        )
        return JSONResponse(
            {"success": False, "message": error}, status_code=status_code
        )
    return JSONResponse({"success": True})


@router.delete("/admin/sites/{admin_site_id}")
async def delete_admin_site_route(admin_site_id: int):
    await run_in_threadpool(_delete_admin_site, admin_site_id)
    return JSONResponse({"success": True})


# ---------------------------------------------------------------------------
# Admin-site connection test (POST /admin/sites/test — no save)
# ---------------------------------------------------------------------------

@router.post("/admin/sites/test")
async def test_admin_site_connection_route(request: Request):
    body = await _read_json_body(request)
    ok, result, error = await run_in_threadpool(test_admin_site_connection, body)
    if not ok:
        if result.get("error_source") == "upstream":
            status, response = sub2api_proxy_error_response(
                result.get("details"),
                error,
                "sub2api 主站连接测试失败",
            )
            return JSONResponse(response, status_code=status)
        return JSONResponse(
            {"success": False, "message": error}, status_code=400
        )
    return JSONResponse({"success": True, **result})


# ---------------------------------------------------------------------------
# Channel candidates (GET /admin/sites/{id}/channel-candidates)
# ---------------------------------------------------------------------------

@router.get("/admin/sites/{admin_site_id}/channel-candidates")
async def channel_candidates(admin_site_id: int, request: Request):
    site, error, status = get_admin_site_or_404(admin_site_id)
    if error:
        return JSONResponse(error, status_code=status)
    if _platform(site) != "newapi":
        return JSONResponse(
            {"success": False, "message": "主站渠道发现仅支持 NewAPI"},
            status_code=405,
        )
    ok, channels, source_meta, error = await run_in_threadpool(
        fetch_admin_site_channels, site, "",
    )
    if not ok:
        return JSONResponse(
            {"success": False, "message": error or "读取主站渠道失败"},
            status_code=502,
        )
    source_channels = channels if isinstance(channels, list) else []
    candidates = aggregate_newapi_channel_candidates(source_channels)
    candidates = enrich_channel_candidates_with_sites(candidates)
    keyword = str(
        (request.query_params.get("keyword") or "")
    ).strip().casefold()
    if keyword:
        candidates = [
            candidate
            for candidate in candidates
            if keyword
            in " ".join(
                [
                    str(candidate.get("base_url") or ""),
                    str(candidate.get("name") or ""),
                    " ".join(
                        str(name or "")
                        for name in candidate.get("channel_names") or []
                    ),
                ]
            ).casefold()
        ]
    try:
        source_channel_total = int(
            (source_meta if isinstance(source_meta, dict) else {}).get("total")
            or len(source_channels)
        )
    except (TypeError, ValueError):
        source_channel_total = len(source_channels)
    return JSONResponse(
        {
            "success": True,
            "data": candidates,
            "meta": {
                "total": len(candidates),
                "source_channel_total": source_channel_total,
            },
        },
    )


# ---------------------------------------------------------------------------
# Groups (GET /admin/sites/{id}/groups)
# ---------------------------------------------------------------------------

@router.get("/admin/sites/{admin_site_id}/groups")
async def admin_site_groups(admin_site_id: int):
    site, error, status = get_admin_site_or_404(admin_site_id)
    if error:
        return JSONResponse(error, status_code=status)
    ok, payload, error = await run_in_threadpool(fetch_admin_site_groups, site)
    if not ok:
        if _platform(site) == "sub2api":
            status, response = sub2api_proxy_error_response(
                payload, error, "读取 sub2api 分组失败"
            )
            return JSONResponse(response, status_code=status)
        return JSONResponse(
            {"success": False, "message": error, "upstream": payload},
            status_code=502,
        )
    return JSONResponse(payload)


# ---------------------------------------------------------------------------
# Channel mappings (GET /admin/sites/{id}/channel-mappings)
# ---------------------------------------------------------------------------

@router.get("/admin/sites/{admin_site_id}/channel-mappings")
async def channel_mappings(admin_site_id: int):
    site, error, status = get_admin_site_or_404(admin_site_id)
    if error:
        return JSONResponse(error, status_code=status)
    if _platform(site) == "sub2api":
        return JSONResponse(
            {"success": False, "message": "sub2api 主站不使用 NewAPI 渠道 key 匹配"},
            status_code=405,
        )
    data = await run_in_threadpool(list_channel_upstream_bindings, admin_site_id)
    return JSONResponse({"success": True, "data": data})


# ---------------------------------------------------------------------------
# Channel list (GET /admin/sites/{id}/channels)
# ---------------------------------------------------------------------------

@router.get("/admin/sites/{admin_site_id}/channels")
async def channel_list(admin_site_id: int, keyword: str = ""):
    site, error, status = get_admin_site_or_404(admin_site_id)
    if error:
        return JSONResponse(error, status_code=status)
    ok, items, meta, error = await run_in_threadpool(
        fetch_admin_site_channels, site, keyword,
    )
    if not ok:
        if _platform(site) == "sub2api":
            status, response = sub2api_proxy_error_response(
                meta, error, "读取 sub2api 渠道失败"
            )
            return JSONResponse(response, status_code=status)
        return JSONResponse(
            {"success": False, "message": error},
            status_code=502,
        )
    data = (
        [mask_channel_in_place(item) for item in items]
        if _platform(site) == "newapi"
        else items
    )
    return JSONResponse({"success": True, "data": data, "meta": meta})


# ---------------------------------------------------------------------------
# Channel detail (GET /admin/sites/{id}/channels/{cid})
# ---------------------------------------------------------------------------

@router.get("/admin/sites/{admin_site_id}/channels/{channel_id}")
async def channel_detail(admin_site_id: int, channel_id: int):
    site, error, status = get_admin_site_or_404(admin_site_id)
    if error:
        return JSONResponse(error, status_code=status)
    ok, payload, error = await run_in_threadpool(
        fetch_admin_site_channel_detail, site, channel_id,
    )
    if not ok:
        if _platform(site) == "sub2api":
            status, response = sub2api_proxy_error_response(
                payload, error, "读取 sub2api 渠道详情失败"
            )
            return JSONResponse(response, status_code=status)
        return JSONResponse(
            {"success": False, "message": error, "upstream": payload},
            status_code=502,
        )
    detail = payload.get("data") if isinstance(payload, dict) else None
    if (
        _platform(site) == "newapi"
        and isinstance(detail, dict)
        and _channel_key_is_masked(detail.get("key"))
    ):
        key_ok, channel_key, key_error = await run_in_threadpool(
            fetch_newapi_channel_key, site, channel_id,
        )
        if key_ok:
            detail = dict(detail)
            detail["key"] = channel_key
            payload = dict(payload)
            payload["data"] = detail
        elif key_error:
            payload = dict(payload)
            payload["key_error"] = key_error
    return JSONResponse(payload)


# ---------------------------------------------------------------------------
# Channel test (GET /admin/sites/{id}/channels/{cid}/test)
# ---------------------------------------------------------------------------

@router.get("/admin/sites/{admin_site_id}/channels/{channel_id}/test")
async def channel_test(admin_site_id: int, channel_id: int):
    site, error, status = get_admin_site_or_404(admin_site_id)
    if error:
        return JSONResponse(error, status_code=status)
    if _platform(site) == "sub2api":
        return JSONResponse(
            {"success": False, "message": "sub2api 主站不支持 NewAPI 渠道测试接口"},
            status_code=405,
        )
    ok, payload, error = await run_in_threadpool(
        test_newapi_channel, site, channel_id,
    )
    if not ok:
        return JSONResponse(
            {"success": False, "message": error, "upstream": payload},
            status_code=502,
        )
    return JSONResponse(payload)


# ---------------------------------------------------------------------------
# Key verification (POST /admin/sites/{id}/key-verification)
# ---------------------------------------------------------------------------

@router.post("/admin/sites/{admin_site_id}/key-verification")
async def key_verification(admin_site_id: int, request: Request):
    site, error, status = get_admin_site_or_404(admin_site_id)
    if error:
        return JSONResponse(error, status_code=status)
    if _platform(site) == "sub2api":
        return JSONResponse(
            {"success": False, "message": "sub2api 主站不使用 NewAPI key 安全验证"},
            status_code=405,
        )
    body = await _read_json_body(request)
    verified, verify_error = await run_in_threadpool(
        verify_admin_site_channel_key_access,
        admin_site_id,
        str(body.get("code") or ""),
    )
    if not verified:
        return JSONResponse(
            {"success": False, "message": verify_error}, status_code=400
        )
    return JSONResponse({"success": True, "message": "主站 key 读取权限已验证"})


# ---------------------------------------------------------------------------
# Batch channel operation (POST /admin/sites/{id}/channels/batch)
# ---------------------------------------------------------------------------

@router.post("/admin/sites/{admin_site_id}/channels/batch")
async def batch_channels(admin_site_id: int, request: Request):
    site, error, status = get_admin_site_or_404(admin_site_id)
    if error:
        return JSONResponse(error, status_code=status)
    if _platform(site) == "sub2api":
        return JSONResponse(
            {"success": False, "message": "sub2api 主站不支持 NewAPI 渠道批量操作"},
            status_code=405,
        )
    body = await _read_json_body(request)
    ok, payload, error = await run_in_threadpool(
        batch_channel_operation,
        site,
        str(body.get("action") or ""),
        body.get("ids"),
        body,
    )
    if not ok:
        return JSONResponse(
            {"success": False, "message": error}, status_code=400
        )
    if str(body.get("action") or "") == "delete":
        for result in payload.get("results") or []:
            if result.get("ok"):
                db_execute(
                    "DELETE FROM channel_upstream_bindings WHERE admin_site_id = ? AND channel_id = ?",
                    (admin_site_id, result.get("id")),
                )
    return JSONResponse({"success": True, "data": payload})


# ---------------------------------------------------------------------------
# Key refresh (POST /admin/sites/{id}/channels/{cid}/key/refresh)
# ---------------------------------------------------------------------------

@router.post("/admin/sites/{admin_site_id}/channels/{channel_id}/key/refresh")
async def key_refresh(admin_site_id: int, channel_id: int):
    site, error, status = get_admin_site_or_404(admin_site_id)
    if error:
        return JSONResponse(error, status_code=status)
    if _platform(site) != "newapi":
        return JSONResponse(
            {"success": False, "message": "仅 NewAPI 主站支持刷新渠道 key"},
            status_code=405,
        )
    previous_key = get_cached_admin_channel_key(admin_site_id, channel_id)
    key_ok, channel_key, key_error = await run_in_threadpool(
        fetch_newapi_channel_key, site, channel_id, True,  # force_refresh=True
    )
    if not key_ok:
        message = key_error or "读取渠道真实 key 失败"
        status_code = 429 if "429" in message or "限流" in message else 400
        code = (
            "rate_limited"
            if status_code == 429
            else "security_verification_required"
            if any(marker in message for marker in ("安全验证", "2FA", "proof"))
            else "key_refresh_failed"
        )
        return JSONResponse(
            {"success": False, "code": code, "message": message},
            status_code=status_code,
        )
    changed = channel_key != previous_key
    match_ok, match_payload, match_error = await run_in_threadpool(
        match_channel_upstream_binding,
        site, channel_id, False,  # force_refresh=False
    )
    binding_row = get_channel_upstream_binding(admin_site_id, channel_id)
    binding_payload = (
        match_payload
        if match_ok and isinstance(match_payload, dict)
        else channel_upstream_binding_payload(binding_row)
    )
    match_status = str(binding_payload.get("match_status") or "")
    match_success = match_ok and match_status in {"matched", "matched_partial"}
    match_message = (
        match_error
        or binding_payload.get("match_message")
        or (None if match_success else "未匹配到上游分组倍率")
    )
    return JSONResponse(
        {
            "success": True,
            "data": {
                "channel_id": channel_id,
                "changed": changed,
                "first_fetch": not bool(previous_key),
                "fetched_at": utc_now_iso(),
                "match_success": match_success,
                "match_message": match_message,
                "binding": binding_payload,
            },
            "message": (
                "渠道 key 已刷新，倍率已重新匹配"
                if match_success and changed
                else "渠道 key 已是最新，倍率已刷新"
                if match_success
                else "渠道 key 已保存，但倍率刷新失败"
            ),
        },
    )


# ---------------------------------------------------------------------------
# Channel match (POST /admin/sites/{id}/channels/{cid}/match)
# ---------------------------------------------------------------------------

@router.post("/admin/sites/{admin_site_id}/channels/{channel_id}/match")
async def channel_match(
    admin_site_id: int,
    channel_id: int,
    refresh: str = "",
):
    site, error, status = get_admin_site_or_404(admin_site_id)
    if error:
        return JSONResponse(error, status_code=status)
    if _platform(site) == "sub2api":
        return JSONResponse(
            {"success": False, "message": "sub2api 主站不使用渠道 key 匹配"},
            status_code=405,
        )
    force_refresh = refresh.lower() in {"1", "true", "yes"}
    ok, payload, error = await run_in_threadpool(
        match_channel_upstream_binding,
        site, channel_id, force_refresh,
    )
    if not ok:
        binding_row = get_channel_upstream_binding(admin_site_id, channel_id)
        binding_payload = channel_upstream_binding_payload(binding_row)
        binding_payload["configured"] = True
        binding_payload["inherited_from_monitor"] = not bool(
            binding_row and binding_row.get("upstream_base_url")
        )
        return JSONResponse(
            {
                "success": False,
                "data": binding_payload,
                "message": error,
            },
        )
    return JSONResponse({"success": True, "data": payload})


# ---------------------------------------------------------------------------
# Create channel (POST /admin/sites/{id}/channels)
# ---------------------------------------------------------------------------

@router.post("/admin/sites/{admin_site_id}/channels")
async def create_channel(admin_site_id: int, request: Request):
    site, error, status = get_admin_site_or_404(admin_site_id)
    if error:
        return JSONResponse(error, status_code=status)
    if _platform(site) == "sub2api":
        return JSONResponse(
            {"success": False, "message": "sub2api 主站不允许在本系统新建渠道"},
            status_code=405,
        )
    body = await _read_json_body(request)
    if not body:
        return JSONResponse(
            {"success": False, "message": "渠道内容为空"}, status_code=400
        )
    existing_ids: set[int] = set()
    existing_ok, existing_items, _existing_error = await run_in_threadpool(
        fetch_all_newapi_channels, site,
    )
    if existing_ok:
        for existing_item in existing_items:
            try:
                existing_ids.add(int(existing_item.get("id")))
            except (TypeError, ValueError):
                continue
    ok, payload, error = await run_in_threadpool(
        create_newapi_channel, site, body,
    )
    if not ok:
        return JSONResponse(
            {"success": False, "message": error, "upstream": payload},
            status_code=502,
        )
    response = dict(payload) if isinstance(payload, dict) else {"success": True}
    created_data = response.get("data")
    created_id = response.get("id")
    if created_id is None and isinstance(created_data, dict):
        created_id = created_data.get("id")
    if created_id is None and isinstance(created_data, list) and created_data:
        first_created = created_data[0]
        if isinstance(first_created, dict):
            created_id = first_created.get("id")
    if created_id is None:
        created_id, resolve_error = await run_in_threadpool(
            resolve_created_newapi_channel_id, site, body, existing_ids,
        )
        if resolve_error:
            response["cache_pending"] = True
            response["cache_message"] = resolve_error
    if created_id is not None:
        created_id = int(created_id)
        response["id"] = created_id
        if "key" in body:
            sync_admin_channel_key(admin_site_id, int(created_id), body.get("key"))
            response["key_cached"] = bool(
                str(body.get("key") or "").strip()
                and not _channel_key_is_masked(body.get("key"))
            )
    return JSONResponse(response)


# ---------------------------------------------------------------------------
# Update channel (PUT /admin/sites/{id}/channels/{cid})
# ---------------------------------------------------------------------------

@router.put("/admin/sites/{admin_site_id}/channels/{channel_id}")
async def update_channel(
    admin_site_id: int,
    channel_id: int,
    request: Request,
):
    site, error, status = get_admin_site_or_404(admin_site_id)
    if error:
        return JSONResponse(error, status_code=status)
    body = await _read_json_body(request)
    if not body:
        return JSONResponse(
            {"success": False, "message": "无更新字段"}, status_code=400
        )
    if _platform(site) == "sub2api":
        validation_error = validate_sub2api_admin_channel_patch(body)
        if validation_error:
            return JSONResponse(
                {"success": False, "message": validation_error},
                status_code=400,
            )
    ok, payload, error = await run_in_threadpool(
        update_admin_site_channel, site, channel_id, body,
    )
    if not ok:
        if _platform(site) == "sub2api":
            status, response = sub2api_proxy_error_response(
                payload, error, "更新 sub2api 渠道失败"
            )
            return JSONResponse(response, status_code=status)
        return JSONResponse(
            {"success": False, "message": error, "upstream": payload},
            status_code=502,
        )
    if _platform(site) == "newapi" and "key" in body:
        sync_admin_channel_key(admin_site_id, channel_id, body.get("key"))
    return JSONResponse(payload)


# ---------------------------------------------------------------------------
# Delete channel (DELETE /admin/sites/{id}/channels/{cid})
# ---------------------------------------------------------------------------

@router.delete("/admin/sites/{admin_site_id}/channels/{channel_id}")
async def delete_channel(admin_site_id: int, channel_id: int):
    site, error, status = get_admin_site_or_404(admin_site_id)
    if error:
        return JSONResponse(error, status_code=status)
    if _platform(site) == "sub2api":
        return JSONResponse(
            {"success": False, "message": "sub2api 主站不允许在本系统删除渠道"},
            status_code=405,
        )
    ok, payload, error = await run_in_threadpool(
        delete_newapi_channel, site, channel_id,
    )
    if not ok:
        return JSONResponse(
            {"success": False, "message": error, "upstream": payload},
            status_code=502,
        )
    db_execute(
        "DELETE FROM channel_upstream_bindings WHERE admin_site_id = ? AND channel_id = ?",
        (admin_site_id, channel_id),
    )
    clear_admin_channel_key(admin_site_id, channel_id)
    return JSONResponse(payload)
