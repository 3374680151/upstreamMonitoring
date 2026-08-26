"""Browser-session synchronization service.

Session-sync request lifecycle (create / get / fail / claim / finish /
complete) and the shared sub2api browser-session persistence primitives.
These were moved verbatim from ``backend.legacy_runtime``; the legacy
runtime re-exports them so existing callers keep working unchanged.

The NewAPI-specific helpers (``_newapi_session_payload_error``,
``_newapi_session_sync_request_error``, ``validate_newapi_site_browser_session``,
``persist_newapi_site_browser_session``) already live in
:mod:`backend.integrations.newapi` and are imported here directly.
"""

from __future__ import annotations

import hmac
import secrets
from datetime import timedelta
from typing import Any, Dict, List, Optional, Tuple

from backend.core.normalize import (
    normalize_session_expiry,
    registered_domain,
    site_origin,
)
from backend.core.security import hash_session_sync_secret
from backend.core.state import (
    BROWSER_AUTH_MODE,
    SESSION_SYNC_MAX_TOKEN_LENGTH,
    SESSION_SYNC_PAGE_FAILURES,
    SESSION_SYNC_REQUEST_LOCK,
    SESSION_SYNC_TERMINAL_STATUSES,
    SESSION_SYNC_TTL_SECONDS,
)
from backend.core.time import APP_TIMEZONE, app_now, parse_iso_dt, utc_now_iso
from backend.db.connection import (
    db_execute,
    db_execute_rowcount,
    db_query_all,
    db_query_one,
)

# NewAPI / sub2api integration helpers are imported lazily inside
# ``complete_session_sync_request``.  They are only needed at call time, and
# importing them eagerly at module top would create a cycle:
# ``session_sync_service`` -> ``backend.integrations.newapi`` -> (lazy
# ``__getattr__``) -> ``backend.legacy_runtime`` -> ``session_sync_service``
# (partially initialized).  This mirrors the lazy-import pattern the
# integration modules themselves use for legacy-runtime names.
#
# ``detect_site`` (from ``monitoring_service``) is also imported lazily for
# the same reason: ``monitoring_service`` imports ``mark_site_browser_session_expired``
# from this module at top level.


SUB2API_SESSION_SYNC_FIELDS = frozenset(
    {"access_token", "refresh_token", "token_expires_at"}
)


def _session_sync_request_expired(row: Dict[str, Any]) -> bool:
    expires_at = parse_iso_dt(str(row.get("expires_at") or ""))
    if not expires_at:
        return True
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=APP_TIMEZONE)
    return expires_at <= app_now()


def session_sync_target_kind(row: Dict[str, Any]) -> str:
    has_site = row.get("site_id") is not None
    has_admin_site = row.get("admin_site_id") is not None
    if has_site == has_admin_site:
        return ""
    return "site" if has_site else "admin_site"


def _site_session_sync_request_error(
    site_id: int,
    request_id: str,
    expected_origin: str,
    platform: str,
) -> Optional[str]:
    """Return a safe error when a claimed site sync is no longer current.

    A browser completion can spend several seconds validating the upstream
    session.  During that time the user may start a replacement sync or edit
    the monitor into a different authentication mode.  Re-read both records
    immediately before a credential write so an older completion cannot win
    that race.
    """
    normalized_platform = str(platform or "").strip().lower()
    normalized_origin = site_origin(expected_origin)
    if not request_id or not normalized_origin or normalized_platform != "sub2api":
        return "同步请求无效，请重新发起同步"

    # Keep this target lookup before the request lookup.  Apart from producing
    # the clearest failure for a deleted/edited site, it also avoids touching a
    # stale request when its target no longer accepts browser credentials.
    target = db_query_one(
        "SELECT id, base_url, platform, auth_mode FROM sites WHERE id = ?",
        (int(site_id),),
    )
    if (
        not target
        or str(target.get("platform") or "").strip().lower()
        != normalized_platform
        or str(target.get("auth_mode") or "").strip().lower()
        != BROWSER_AUTH_MODE
        or site_origin(str(target.get("base_url") or "")) != normalized_origin
    ):
        return "同步目标已变更，请重新发起同步"

    request = db_query_one(
        """
        SELECT id, site_id, admin_site_id, platform, target_origin, status
        FROM browser_session_sync_requests
        WHERE id = ? AND site_id = ? AND admin_site_id IS NULL
        """,
        (str(request_id), int(site_id)),
    )
    if (
        not request
        or str(request.get("status") or "") != "validating"
        or str(request.get("platform") or "").strip().lower()
        != normalized_platform
        or str(request.get("target_origin") or "") != normalized_origin
    ):
        return "同步请求已失效，请重新发起同步"
    return None


def _session_sync_public_payload(row: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "request_id": str(row.get("id") or ""),
        "target_kind": session_sync_target_kind(row),
        "status": str(row.get("status") or "failed"),
        "platform": str(row.get("platform") or ""),
        "target_origin": str(row.get("target_origin") or ""),
        "error_code": str(row.get("error_code") or ""),
        "message": str(row.get("error_message") or ""),
        "expires_at": str(row.get("expires_at") or ""),
        "updated_at": str(row.get("updated_at") or ""),
        "consumed_at": str(row.get("consumed_at") or ""),
    }


def _create_session_sync_request(
    site_id: int, target: Dict[str, Any]
) -> Tuple[bool, Dict[str, Any], Optional[str]]:
    platform = str(target.get("platform") or "newapi").strip().lower()
    if platform not in {"sub2api", "newapi"}:
        return False, {}, "当前平台不支持浏览器登录态同步"
    if str(target.get("auth_mode") or "").strip().lower() != BROWSER_AUTH_MODE:
        return False, {}, "请先将渠道认证方式切换为浏览器登录态"
    origin = site_origin(str(target.get("base_url") or ""))
    if not origin:
        return False, {}, "渠道 Base URL 无效"

    request_id = secrets.token_urlsafe(24)
    secret = secrets.token_urlsafe(32)
    now_dt = app_now()
    now = now_dt.isoformat(timespec="seconds")
    expires_at = (now_dt + timedelta(seconds=SESSION_SYNC_TTL_SECONDS)).isoformat(
        timespec="seconds"
    )
    with SESSION_SYNC_REQUEST_LOCK:
        db_execute(
            """
            UPDATE browser_session_sync_requests
            SET status = 'expired', error_code = 'REPLACED',
                error_message = '已创建新的同步请求', updated_at = ?
            WHERE status IN ('pending', 'validating') AND site_id = ?
              AND admin_site_id IS NULL
            """,
            (now, int(site_id)),
        )
        db_execute(
            """
            INSERT INTO browser_session_sync_requests
            (id, site_id, admin_site_id, platform, target_origin, secret_hash,
             status, error_code, error_message, expires_at, created_at, updated_at,
             consumed_at)
            VALUES (?, ?, ?, ?, ?, ?, 'pending', NULL, NULL, ?, ?, ?, NULL)
            """,
            (
                request_id,
                int(site_id),
                None,
                platform,
                origin,
                hash_session_sync_secret(secret),
                expires_at,
                now,
                now,
            ),
        )
        db_execute(
            """
            UPDATE sites
            SET session_sync_status = 'pending', session_sync_error = NULL,
                updated_at = ?
            WHERE id = ?
            """,
            (now, int(site_id)),
        )
    return True, {
        "request_id": request_id,
        "secret": secret,
        "platform": platform,
        "target_kind": "site",
        "target_origin": origin,
        "expires_in": SESSION_SYNC_TTL_SECONDS,
    }, None


def create_site_session_sync_request(
    site_id: int,
) -> Tuple[bool, Dict[str, Any], Optional[str]]:
    site = db_query_one("SELECT * FROM sites WHERE id = ?", (int(site_id),))
    if not site:
        return False, {}, "渠道不存在"
    return _create_session_sync_request(int(site_id), site)


def share_site_browser_session(site_id: int) -> Dict[str, Any]:
    """尝试把同注册域兄弟站点的浏览器登录态复用到本站点。

    场景：同一家 sub2api 服务部署了 www.xxx.com（控制台，有登录 cookie）
    和 api.xxx.com（API 域，无登录页）。同一账号在 www 域同步成功后，
    api 域的站点直接复用这份 token（token 是账号级 JWT，对 API 域同样
    有效），不需要扩展去一个没有登录态的域上再找一遍 cookie。
    """
    from backend.integrations.sub2api import validate_sub2api_browser_session

    empty = {"shared": False, "source_site_id": None, "source_name": ""}
    site = db_query_one(
        """
        SELECT id, name, base_url, platform, auth_mode, session_sync_status,
               access_token, token_expires_at
        FROM sites WHERE id = ?
        """,
        (int(site_id),),
    )
    if (
        not site
        or str(site.get("platform") or "").strip().lower() != "sub2api"
        or str(site.get("auth_mode") or "").strip().lower() != BROWSER_AUTH_MODE
    ):
        return empty

    # 自己已有有效登录态时无需复用
    now_iso = app_now().isoformat(timespec="seconds")
    own_token = str(site.get("access_token") or "").strip()
    own_expiry = str(site.get("token_expires_at") or "").strip()
    if (
        site.get("session_sync_status") == "ready"
        and own_token
        and (not own_expiry or own_expiry > now_iso)
    ):
        return empty

    domain = registered_domain(str(site.get("base_url") or ""))
    if not domain:
        return empty

    candidates = db_query_all(
        """
        SELECT id, name, base_url, platform, auth_mode, session_sync_status,
               access_token, refresh_token, token_expires_at
        FROM sites
        WHERE id != ?
          AND platform = 'sub2api'
          AND auth_mode = 'browser'
          AND session_sync_status = 'ready'
          AND access_token IS NOT NULL AND access_token != ''
        """,
        (int(site_id),),
    )
    source = None
    for row in candidates:
        if registered_domain(str(row.get("base_url") or "")) != domain:
            continue
        expiry = str(row.get("token_expires_at") or "").strip()
        if expiry and expiry <= now_iso:
            continue
        source = row
        break
    if not source:
        return empty

    access = str(source.get("access_token") or "").strip()
    refresh = str(source.get("refresh_token") or "").strip()
    expires = str(source.get("token_expires_at") or "").strip()

    # 复用前先对目标站点自己的 base_url 校验 token 有效性，
    # 校验不过就走正常扩展同步，不写入垃圾 token
    ok, _validated, error = validate_sub2api_browser_session(
        str(site.get("base_url") or ""), access
    )
    if not ok:
        return {**empty, "message": error or "兄弟站点登录态对当前渠道无效"}

    persist_site_browser_session(
        int(site_id), access, refresh, expires
    )
    return {
        "shared": True,
        "source_site_id": int(source["id"]),
        "source_name": str(source.get("name") or ""),
    }


def get_site_session_sync_request(
    site_id: int, request_id: str
) -> Optional[Dict[str, Any]]:
    row = db_query_one(
        """
        SELECT * FROM browser_session_sync_requests
        WHERE id = ? AND site_id = ? AND admin_site_id IS NULL
        """,
        (str(request_id), int(site_id)),
    )
    if not row:
        return None
    if (
        str(row.get("status") or "") in {"pending", "validating"}
        and _session_sync_request_expired(row)
    ):
        finish_session_sync_request(
            str(row.get("id") or ""),
            "expired",
            "SYNC_REQUEST_EXPIRED",
            "同步请求已过期",
        )
        row = {
            **row,
            "status": "expired",
            "error_code": "SYNC_REQUEST_EXPIRED",
            "error_message": "同步请求已过期",
            "updated_at": utc_now_iso(),
        }
    return _session_sync_public_payload(row)


def fail_site_session_sync_request(
    site_id: int, request_id: str, error_code: str
) -> Tuple[bool, Optional[str]]:
    failure = SESSION_SYNC_PAGE_FAILURES.get(str(error_code or ""))
    if not failure:
        return False, "不支持的同步失败代码"
    row = db_query_one(
        """
        SELECT * FROM browser_session_sync_requests
        WHERE id = ? AND site_id = ? AND admin_site_id IS NULL
        """,
        (str(request_id), int(site_id)),
    )
    if not row:
        return False, "同步请求不存在"
    if str(row.get("status") or "") != "pending":
        return False, "同步请求已结束"
    if _session_sync_request_expired(row):
        finish_session_sync_request(
            str(request_id), "expired", "SYNC_REQUEST_EXPIRED", "同步请求已过期"
        )
        return False, "同步请求已过期"
    status, message = failure
    finish_session_sync_request(str(request_id), status, str(error_code), message)
    return True, None


def claim_session_sync_request(
    request_id: str, secret: str
) -> Tuple[bool, Optional[Dict[str, Any]], Optional[str]]:
    with SESSION_SYNC_REQUEST_LOCK:
        row = db_query_one(
            "SELECT * FROM browser_session_sync_requests WHERE id = ?",
            (str(request_id),),
        )
        if not row:
            return False, None, "SYNC_REQUEST_NOT_FOUND"
        if str(row.get("status") or "") != "pending":
            return False, None, "SYNC_REQUEST_CONSUMED"
        if _session_sync_request_expired(row):
            now = utc_now_iso()
            db_execute(
                """
                UPDATE browser_session_sync_requests
                SET status = 'expired', error_code = 'SYNC_REQUEST_EXPIRED',
                    error_message = '同步请求已过期', updated_at = ?
                WHERE id = ? AND status = 'pending'
                """,
                (now, str(request_id)),
            )
            if row.get("site_id") is not None:
                db_execute(
                    """
                    UPDATE sites SET session_sync_status = 'expired',
                        session_sync_error = '同步请求已过期', updated_at = ?
                    WHERE id = ?
                    """,
                    (now, int(row["site_id"])),
                )
            return False, None, "SYNC_REQUEST_EXPIRED"
        expected = str(row.get("secret_hash") or "")
        actual = hash_session_sync_secret(secret)
        if not hmac.compare_digest(expected, actual):
            return False, None, "SYNC_REQUEST_SECRET_INVALID"
        now = utc_now_iso()
        db_execute(
            """
            UPDATE browser_session_sync_requests
            SET status = 'validating', updated_at = ?, consumed_at = ?
            WHERE id = ? AND status = 'pending'
            """,
            (now, now, str(request_id)),
        )
        if row.get("site_id") is not None:
            db_execute(
                """
                UPDATE sites SET session_sync_status = 'validating',
                    session_sync_error = NULL, updated_at = ? WHERE id = ?
                """,
                (now, int(row["site_id"])),
            )
        return True, {**row, "status": "validating", "consumed_at": now}, None


def finish_session_sync_request(
    request_id: str, status: str, code: str = "", message: str = ""
) -> bool:
    """Mark a sync request as terminal and update the target site in one lock.

    The terminal write on ``browser_session_sync_requests`` and the matching
    site/admin_site write share a single process lock.  Before touching the
    site state we re-read the request to confirm it is still the active one
    for that target; if a newer request has already been claimed, we end this
    request's terminal state but leave the site alone so the new request
    keeps controlling the visible status.
    """
    if status not in SESSION_SYNC_TERMINAL_STATUSES:
        raise ValueError("invalid session sync status")
    with SESSION_SYNC_REQUEST_LOCK:
        row = db_query_one(
            "SELECT id, site_id, admin_site_id, platform, target_origin, status "
            "FROM browser_session_sync_requests WHERE id = ?",
            (str(request_id),),
        )
        if not row:
            return False
        now = utc_now_iso()
        target_origin = str(row.get("target_origin") or "")
        platform = str(row.get("platform") or "").strip().lower()
        request_id_value = str(request_id)
        changed = db_execute_rowcount(
            """
            UPDATE browser_session_sync_requests
            SET status = ?, error_code = ?, error_message = ?, updated_at = ?
            WHERE id = ? AND status IN ('pending', 'validating')
            """,
            (status, str(code or "") or None, str(message or "") or None, now, request_id_value),
        )
        if changed <= 0:
            return False
        # Atomicity guarantee: a newer request may have been claimed while
        # this one was in flight.  Only touch the site if this request is
        # still the active one for the target.
        if row.get("site_id") is not None and platform == "sub2api":
            active = db_query_one(
                """
                SELECT id FROM browser_session_sync_requests
                WHERE site_id = ? AND admin_site_id IS NULL
                  AND platform = 'sub2api'
                  AND target_origin = ? AND status = 'validating'
                """,
                (int(row["site_id"]), target_origin),
            )
            if active and str(active.get("id") or "") != request_id_value:
                return True
            db_execute_rowcount(
                """
                UPDATE sites AS s
                SET session_sync_status = ?, session_sync_error = ?,
                    session_synced_at = CASE WHEN ? = 'ready' THEN ? ELSE session_synced_at END,
                    updated_at = ?
                WHERE s.id = ?
                  AND s.platform = 'sub2api'
                  AND s.auth_mode = 'browser'
                  AND EXISTS (
                      SELECT 1
                      FROM browser_session_sync_requests AS r
                      WHERE r.id = ?
                        AND r.site_id = s.id
                        AND r.admin_site_id IS NULL
                        AND r.platform = 'sub2api'
                        AND r.target_origin = ?
                        AND r.status IN ('validating', 'pending', 'ready', 'failed', 'expired')
                  )
                """,
                (
                    status,
                    str(message or "") or None,
                    status,
                    now,
                    now,
                    int(row["site_id"]),
                    request_id_value,
                    target_origin,
                ),
            )
    return True


def complete_session_sync_request(
    request_id: str, secret: str, body: Any
) -> Tuple[int, Dict[str, Any]]:
    # Deferred to call time to avoid a top-level import cycle (see note above).
    from backend.integrations.newapi import (  # noqa: E402
        NEWAPI_SESSION_SYNC_FIELDS,
        _newapi_session_payload_error,
        _newapi_session_sync_request_error,
        persist_newapi_site_browser_session,
        validate_newapi_site_browser_session,
    )
    from backend.integrations.sub2api import apply_sub2api_browser_session  # noqa: E402

    if not str(secret or ""):
        return 401, {
            "success": False,
            "status": "failed",
            "code": "SYNC_REQUEST_SECRET_REQUIRED",
            "message": "缺少同步凭证",
        }
    if not isinstance(body, dict):
        return 400, {
            "success": False,
            "status": "failed",
            "code": "INVALID_SYNC_PAYLOAD",
            "message": "同步数据格式无效",
        }
    status = str(body.get("status") or "")
    if status not in {"session_found", "no_session"}:
        return 400, {
            "success": False,
            "status": "failed",
            "code": "INVALID_SYNC_STATUS",
            "message": "同步状态无效",
        }
    platform = str(body.get("platform") or "").strip().lower()
    observed_origin = site_origin(str(body.get("observed_origin") or ""))
    session = body.get("session")
    if status == "session_found":
        if not isinstance(session, dict):
            return 400, {
                "success": False,
                "status": "failed",
                "code": "SESSION_REQUIRED",
                "message": "未提供浏览器登录态",
            }
        all_session_keys = SUB2API_SESSION_SYNC_FIELDS | NEWAPI_SESSION_SYNC_FIELDS
        if set(session) - all_session_keys:
            return 400, {
                "success": False,
                "status": "failed",
                "code": "SESSION_FIELDS_INVALID",
                "message": "登录态字段无效",
            }
        for key in all_session_keys:
            limit = (
                256
                if key
                in {
                    "token_expires_at",
                    "access_user_id",
                    "browser_access_expires_at",
                }
                else SESSION_SYNC_MAX_TOKEN_LENGTH
            )
            if len(str(session.get(key) or "")) > limit:
                return 400, {
                    "success": False,
                    "status": "failed",
                    "code": "SESSION_FIELD_TOO_LARGE",
                    "message": "登录态字段过长",
                }
        if platform == "newapi":
            payload_error = _newapi_session_payload_error(session)
            if payload_error:
                message = (
                    "NewAPI Refresh Cookie 必须严格使用 new_api_refresh"
                    if payload_error == "SESSION_COOKIE_INVALID"
                    else "NewAPI 浏览器登录态字段不完整"
                )
                return 400, {
                    "success": False,
                    "status": "failed",
                    "code": payload_error,
                    "message": message,
                }
    elif session is not None:
        return 400, {
            "success": False,
            "status": "failed",
            "code": "SESSION_NOT_ALLOWED",
            "message": "无登录态响应不能携带 session",
        }

    claimed, request_row, claim_error = claim_session_sync_request(
        str(request_id), str(secret)
    )
    if not claimed or not request_row:
        status_codes = {
            "SYNC_REQUEST_SECRET_INVALID": 401,
            "SYNC_REQUEST_NOT_FOUND": 404,
            "SYNC_REQUEST_CONSUMED": 409,
            "SYNC_REQUEST_EXPIRED": 410,
        }
        return status_codes.get(str(claim_error), 400), {
            "success": False,
            "status": "failed",
            "code": str(claim_error or "SYNC_REQUEST_REJECTED"),
            "message": "同步请求不可用",
        }

    if platform != str(request_row.get("platform") or "").strip().lower():
        finish_session_sync_request(
            str(request_id), "failed", "PLATFORM_MISMATCH", "同步平台不匹配"
        )
        return 400, {
            "success": False,
            "status": "failed",
            "code": "PLATFORM_MISMATCH",
            "message": "同步平台不匹配",
        }
    allowed_session_keys = (
        SUB2API_SESSION_SYNC_FIELDS
        if platform == "sub2api"
        else NEWAPI_SESSION_SYNC_FIELDS
        if platform == "newapi"
        else frozenset()
    )
    if status == "session_found" and set(session) - allowed_session_keys:
        finish_session_sync_request(
            str(request_id), "failed", "SESSION_FIELDS_INVALID", "登录态字段无效"
        )
        return 400, {
            "success": False,
            "status": "failed",
            "code": "SESSION_FIELDS_INVALID",
            "message": "登录态字段无效",
        }
    if not observed_origin or observed_origin != str(
        request_row.get("target_origin") or ""
    ):
        finish_session_sync_request(
            str(request_id), "failed", "ORIGIN_MISMATCH", "同步站点 Origin 不匹配"
        )
        return 400, {
            "success": False,
            "status": "failed",
            "code": "ORIGIN_MISMATCH",
            "message": "同步站点 Origin 不匹配",
        }
    if status == "no_session":
        finish_session_sync_request(
            str(request_id), "no_session", "NO_SESSION", "没有登录态，请提前登录"
        )
        return 200, {
            "success": False,
            "status": "no_session",
            "code": "NO_SESSION",
            "message": "没有登录态，请提前登录",
        }

    site_id = request_row.get("site_id")
    target_kind = session_sync_target_kind(request_row)
    target_origin = str(request_row.get("target_origin") or "")
    if platform == "sub2api" and target_kind == "site" and site_id is not None:
        applied, apply_error = apply_sub2api_browser_session(
            int(site_id),
            str(request_row.get("target_origin") or ""),
            session,
            request_id=str(request_id),
            expected_origin=str(request_row.get("target_origin") or ""),
        )
    elif platform == "newapi" and target_kind == "site" and site_id is not None:
        target = db_query_one("SELECT * FROM sites WHERE id = ?", (int(site_id),))
        if (
            not target
            or str(target.get("platform") or "newapi").strip().lower() != "newapi"
            or str(target.get("auth_mode") or "").strip().lower() != BROWSER_AUTH_MODE
            or site_origin(str(target.get("base_url") or "")) != target_origin
        ):
            applied, apply_error = False, "同步目标已变更，请重新发起同步"
        else:
            applied, _validation, apply_error = validate_newapi_site_browser_session(
                str(target.get("base_url") or ""), session
            )
            if applied:
                sync_error = _newapi_session_sync_request_error(
                    target_kind, int(site_id), str(request_id), target_origin
                )
                if sync_error:
                    applied, apply_error = False, sync_error
            if applied:
                persist_newapi_site_browser_session(int(site_id), session)
    else:
        finish_session_sync_request(
            str(request_id), "failed", "UNSUPPORTED_TARGET", "当前同步目标暂不支持"
        )
        return 400, {
            "success": False,
            "status": "failed",
            "code": "UNSUPPORTED_TARGET",
            "message": "当前同步目标暂不支持",
        }
    if not applied:
        message = apply_error or "登录态已过期，请重新登录"
        finish_session_sync_request(
            str(request_id), "expired", "SESSION_INVALID", message
        )
        return 401, {
            "success": False,
            "status": "expired",
            "code": "SESSION_INVALID",
            "message": message,
        }
    finish_session_sync_request(str(request_id), "ready")
    if target_kind == "site":
        from backend.services.monitoring_service import detect_site

        detection = detect_site(int(site_id))
    else:
        detection = None
    return 200, {
        "success": True,
        "status": "ready",
        "message": "浏览器登录态已同步",
        "detected": bool(detection.get("success"))
        if isinstance(detection, dict)
        else False,
    }


def persist_site_browser_session(
    site_id: int,
    access_token: str,
    refresh_token: str,
    expires_at: str,
    request_id: str = "",
    expected_origin: str = "",
) -> bool:
    """Persist a sub2api browser session.

    Ordinary refresh/password fallback writes retain their existing behaviour.
    A completion write carries its one-time request ID and uses a database CAS
    condition, so it cannot overwrite a newer request or a manual auth-mode
    change after validation has already started.
    """
    now = utc_now_iso()
    params: List[Any] = [
        str(access_token or "").strip(),
        str(refresh_token or "").strip(),
        normalize_session_expiry(expires_at) or None,
        now,
        now,
        int(site_id),
    ]
    sql = """
        UPDATE sites AS s
        SET auth_mode = 'browser', login_enabled = 1,
            access_token = ?, refresh_token = ?, token_expires_at = ?,
            session_sync_status = 'ready', session_sync_error = NULL,
            session_synced_at = ?, updated_at = ?
        WHERE s.id = ?
    """
    request_id = str(request_id or "").strip()
    if not request_id:
        db_execute(sql, params)
        return True

    origin = site_origin(expected_origin)
    if not origin:
        return False
    sql += """
          AND s.platform = 'sub2api'
          AND s.auth_mode = 'browser'
          AND EXISTS (
              SELECT 1
              FROM browser_session_sync_requests AS r
              WHERE r.id = ?
                AND r.site_id = s.id
                AND r.admin_site_id IS NULL
                AND r.platform = 'sub2api'
                AND r.target_origin = ?
                AND r.status = 'validating'
          )
    """
    params.extend((request_id, origin))
    return db_execute_rowcount(sql, params) > 0


def mark_site_browser_session_expired(
    site_id: int,
    message: str,
    request_id: str = "",
    expected_origin: str = "",
) -> bool:
    now = utc_now_iso()
    params: List[Any] = [
        str(message or "登录态已过期，请重新登录"),
        now,
        int(site_id),
    ]
    sql = """
        UPDATE sites AS s
        SET session_sync_status = 'expired', session_sync_error = ?, updated_at = ?
        WHERE s.id = ?
    """
    request_id = str(request_id or "").strip()
    if not request_id:
        db_execute(sql, params)
        return True

    origin = site_origin(expected_origin)
    if not origin:
        return False
    sql += """
          AND s.platform = 'sub2api'
          AND s.auth_mode = 'browser'
          AND EXISTS (
              SELECT 1
              FROM browser_session_sync_requests AS r
              WHERE r.id = ?
                AND r.site_id = s.id
                AND r.admin_site_id IS NULL
                AND r.platform = 'sub2api'
                AND r.target_origin = ?
                AND r.status = 'validating'
          )
    """
    params.extend((request_id, origin))
    return db_execute_rowcount(sql, params) > 0


class SessionSyncService:
    def create_site(self, site_id: int):
        return create_site_session_sync_request(site_id)

    def complete(self, request_id: str, secret: str, payload: Any):
        return complete_session_sync_request(request_id, secret, payload)
