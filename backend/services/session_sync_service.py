"""Browser-session synchronisation service."""
from __future__ import annotations

import hashlib
import hmac
import re
import secrets
import threading
from datetime import datetime, timedelta
from typing import Any

from backend.core.errors import DomainError, NotFoundError, ValidationError
from urllib.parse import urlparse

from backend.core.time import app_now, application_timezone, utc_now_iso
from backend.repositories.admin_sites import AdminSiteRepository
from backend.repositories.session_sync import SessionSyncRepository
from backend.repositories.sites import SiteRepository
from backend.integrations.newapi import NewApiClient
from backend.integrations.sub2api import Sub2ApiClient
from backend.services.check_service import CheckService


BROWSER_AUTH_MODE = "browser"
SESSION_SYNC_TTL_SECONDS = 60
SESSION_SYNC_MAX_TOKEN_LENGTH = 16 * 1024
SUB2API_SESSION_SYNC_FIELDS = frozenset(
    {"access_token", "refresh_token", "token_expires_at"}
)
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
ALL_SESSION_SYNC_FIELDS = SUB2API_SESSION_SYNC_FIELDS | NEWAPI_SESSION_SYNC_FIELDS
SESSION_SYNC_PAGE_FAILURES = {
    "EXTENSION_UNAVAILABLE": (
        "extension_unavailable",
        "未安装或未连接浏览器同步扩展",
    ),
    "ORIGIN_PERMISSION_REQUIRED": (
        "permission_required",
        "扩展需要该站点的读取权限",
    ),
    "COOKIE_PERMISSION_REQUIRED": (
        "permission_required",
        "扩展需要读取 NewAPI 登录 Cookie 的权限，请允许后重新同步",
    ),
    "SYNC_FAILED": ("failed", "登录态同步失败"),
}
SESSION_SYNC_LOCK = threading.RLock()


def _site_origin(value: str) -> str:
    try:
        parsed = urlparse(str(value or "").strip())
        parsed.port
    except (TypeError, ValueError):
        return ""
    if (
        parsed.scheme.lower() not in {"http", "https"}
        or not parsed.hostname
        or parsed.username
        or parsed.password
    ):
        return ""
    return f"{parsed.scheme.lower()}://{parsed.netloc.lower()}"


def _request_expired(row: dict[str, Any]) -> bool:
    try:
        expires_at = datetime.fromisoformat(str(row.get("expires_at") or ""))
    except ValueError:
        return True
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=application_timezone())
    return expires_at <= app_now()


def _public_payload(row: dict[str, Any]) -> dict[str, Any]:
    has_site = row.get("site_id") is not None
    has_admin_site = row.get("admin_site_id") is not None
    target_kind = "site" if has_site and not has_admin_site else "admin_site" if has_admin_site and not has_site else ""
    return {
        "request_id": str(row.get("id") or ""),
        "target_kind": target_kind,
        "status": str(row.get("status") or "failed"),
        "platform": str(row.get("platform") or ""),
        "target_origin": str(row.get("target_origin") or ""),
        "error_code": str(row.get("error_code") or ""),
        "message": str(row.get("error_message") or ""),
        "expires_at": str(row.get("expires_at") or ""),
        "updated_at": str(row.get("updated_at") or ""),
        "consumed_at": str(row.get("consumed_at") or ""),
    }


class SessionSyncService:
    def __init__(
        self,
        repository: SessionSyncRepository | None = None,
        sites: SiteRepository | None = None,
        admin_sites: AdminSiteRepository | None = None,
        checks: CheckService | None = None,
        newapi: NewApiClient | None = None,
        sub2api: Sub2ApiClient | None = None,
    ) -> None:
        self.repository = repository or SessionSyncRepository()
        self.sites = sites or SiteRepository()
        self.admin_sites = admin_sites or AdminSiteRepository()
        self.newapi = newapi or NewApiClient()
        self.sub2api = sub2api or Sub2ApiClient()
        self.checks = checks or CheckService(sites=self.sites)
        # Database CAS predicates protect cross-process races; this lock only
        # keeps local create/claim/finalise transitions ordered.
        self._lock = SESSION_SYNC_LOCK

    def create_site(self, site_id: int):
        return self._create("site", int(site_id))

    def _create(self, target_kind: str, target_id: int):
        target = (
            self.sites.get(target_id)
            if target_kind == "site"
            else self.admin_sites.get(target_id)
        )
        if not target:
            return False, {}, "渠道不存在" if target_kind == "site" else "管理站不存在"
        platform = str(target.get("platform") or "newapi").strip().lower()
        if platform not in {"newapi", "sub2api"}:
            return False, {}, "当前平台不支持浏览器登录态同步"
        if target_kind == "site" and (
            str(target.get("auth_mode") or "").strip().lower() != BROWSER_AUTH_MODE
        ):
            return False, {}, "请先将渠道认证方式切换为浏览器登录态"
        if target_kind == "admin_site" and platform != "newapi":
            return False, {}, "当前管理站平台暂不支持浏览器登录态同步"
        origin = _site_origin(str(target.get("base_url") or ""))
        if not origin:
            return False, {}, "渠道 Base URL 无效"

        request_id = secrets.token_urlsafe(24)
        secret = secrets.token_urlsafe(32)
        now_dt = app_now()
        now = now_dt.isoformat(timespec="seconds")
        expires_at = (now_dt + timedelta(seconds=SESSION_SYNC_TTL_SECONDS)).isoformat(
            timespec="seconds"
        )
        with self._lock:
            self.repository.create_request(
                target_kind=target_kind,
                target_id=target_id,
                platform=platform,
                target_origin=origin,
                request_id=request_id,
                secret_hash=hashlib.sha256(secret.encode("utf-8")).hexdigest(),
                expires_at=expires_at,
                now=now,
            )
        return True, {
            "request_id": request_id,
            "secret": secret,
            "platform": platform,
            "target_kind": target_kind,
            "target_origin": origin,
            "expires_in": SESSION_SYNC_TTL_SECONDS,
        }, None

    def create_for_site(self, site_id: int) -> dict[str, Any]:
        ok, payload, error = self._create("site", int(site_id))
        if not ok:
            if error == "渠道不存在":
                raise NotFoundError(error)
            raise ValidationError(error or "无法创建同步请求")
        return {"success": True, "data": payload}

    def create_for_admin_site(self, admin_site_id: int) -> dict[str, Any]:
        ok, payload, error = self._create("admin_site", int(admin_site_id))
        if not ok:
            if error == "管理站不存在":
                raise NotFoundError(error)
            raise ValidationError(error or "无法创建同步请求")
        return {"success": True, "data": payload}

    def get_for_site(self, site_id: int, request_id: str) -> dict[str, Any]:
        payload = self._get("site", int(site_id), request_id)
        if payload is None:
            raise NotFoundError("同步请求不存在")
        return {"success": True, "data": payload}

    def get_for_admin_site(self, admin_site_id: int, request_id: str) -> dict[str, Any]:
        payload = self._get("admin_site", int(admin_site_id), request_id)
        if payload is None:
            raise NotFoundError("同步请求不存在")
        return {"success": True, "data": payload}

    def fail_for_site(self, site_id: int, request_id: str, code: str) -> dict[str, Any]:
        ok, error = self._fail("site", int(site_id), request_id, str(code or ""))
        if not ok:
            if error == "同步请求不存在":
                raise NotFoundError(error)
            raise ValidationError(error or "同步请求无法结束")
        return {"success": True}

    def fail_for_admin_site(self, admin_site_id: int, request_id: str, code: str) -> dict[str, Any]:
        ok, error = self._fail(
            "admin_site", int(admin_site_id), request_id, str(code or "")
        )
        if not ok:
            if error == "同步请求不存在":
                raise NotFoundError(error)
            raise ValidationError(error or "同步请求无法结束")
        return {"success": True}

    def complete(
        self, request_id: str, secret: str, payload: Any
    ) -> dict[str, Any]:
        """Validate and persist one browser-session bridge completion."""
        if not str(secret or ""):
            self._raise_sync_error(401, "SYNC_REQUEST_SECRET_REQUIRED", "缺少同步凭证")
        if not isinstance(payload, dict):
            self._raise_sync_error(400, "INVALID_SYNC_PAYLOAD", "同步数据格式无效")
        status = str(payload.get("status") or "")
        if status not in {"session_found", "no_session"}:
            self._raise_sync_error(400, "INVALID_SYNC_STATUS", "同步状态无效")
        platform = str(payload.get("platform") or "").strip().lower()
        observed_origin = _site_origin(str(payload.get("observed_origin") or ""))
        session = payload.get("session")
        payload_error = self._validate_completion_payload(platform, status, session)
        if payload_error:
            code, message = payload_error
            self._raise_sync_error(400, code, message)
        session_data = session if isinstance(session, dict) else {}

        with self._lock:
            request_row, claim_error = self._claim_request(request_id, secret)
        if not request_row:
            self._raise_claim_error(claim_error)

        target_platform = str(request_row.get("platform") or "").strip().lower()
        target_origin = str(request_row.get("target_origin") or "")
        if platform != target_platform:
            self._finish(
                request_id, "failed", "PLATFORM_MISMATCH", "同步平台不匹配"
            )
            self._raise_sync_error(400, "PLATFORM_MISMATCH", "同步平台不匹配")
        allowed_fields = (
            SUB2API_SESSION_SYNC_FIELDS
            if platform == "sub2api"
            else NEWAPI_SESSION_SYNC_FIELDS
            if platform == "newapi"
            else frozenset()
        )
        if status == "session_found" and set(session_data) - allowed_fields:
            self._finish(
                request_id, "failed", "SESSION_FIELDS_INVALID", "登录态字段无效"
            )
            self._raise_sync_error(400, "SESSION_FIELDS_INVALID", "登录态字段无效")
        if not observed_origin or observed_origin != target_origin:
            self._finish(
                request_id, "failed", "ORIGIN_MISMATCH", "同步站点 Origin 不匹配"
            )
            self._raise_sync_error(400, "ORIGIN_MISMATCH", "同步站点 Origin 不匹配")
        if status == "no_session":
            self._finish(
                request_id,
                "no_session",
                "NO_SESSION",
                "没有登录态，请提前登录",
            )
            return {
                "success": False,
                "status": "no_session",
                "code": "NO_SESSION",
                "message": "没有登录态，请提前登录",
            }

        target_kind = self._target_kind(request_row)
        if platform == "sub2api" and target_kind == "site":
            applied, apply_error = self._apply_sub2api_site(
                request_row, session_data
            )
        elif platform == "newapi" and target_kind == "site":
            applied, apply_error = self._apply_newapi_site(
                request_row, session_data
            )
        elif platform == "newapi" and target_kind == "admin_site":
            applied, apply_error = self._apply_newapi_admin_site(
                request_row, session_data
            )
        else:
            self._finish(
                request_id, "failed", "UNSUPPORTED_TARGET", "当前同步目标暂不支持"
            )
            self._raise_sync_error(400, "UNSUPPORTED_TARGET", "当前同步目标暂不支持")

        if not applied:
            message = apply_error or "登录态已过期，请重新登录"
            self._finish(request_id, "expired", "SESSION_INVALID", message)
            self._raise_sync_error(401, "SESSION_INVALID", message)

        detected = False
        if target_kind == "site":
            try:
                detected = bool(
                    self.checks.check(int(request_row.get("site_id") or 0)).get(
                        "success"
                    )
                )
            except Exception:
                pass
        return {
            "success": True,
            "status": "ready",
            "message": "浏览器登录态已同步",
            "detected": detected,
        }

    @staticmethod
    def _raise_sync_error(status: int, code: str, message: str) -> None:
        error = DomainError(message, {"status": "failed"})
        error.status_code = int(status)
        error.code = str(code)
        raise error

    def _raise_claim_error(self, claim_error: str | None) -> None:
        status_codes = {
            "SYNC_REQUEST_SECRET_REQUIRED": 401,
            "SYNC_REQUEST_SECRET_INVALID": 401,
            "SYNC_REQUEST_NOT_FOUND": 404,
            "SYNC_REQUEST_CONSUMED": 409,
            "SYNC_REQUEST_EXPIRED": 410,
        }
        code = str(claim_error or "SYNC_REQUEST_REJECTED")
        self._raise_sync_error(
            status_codes.get(code, 400),
            code,
            "缺少同步凭证" if code == "SYNC_REQUEST_SECRET_REQUIRED" else "同步请求不可用",
        )

    @staticmethod
    def _target_kind(row: dict[str, Any]) -> str:
        has_site = row.get("site_id") is not None
        has_admin_site = row.get("admin_site_id") is not None
        if has_site == has_admin_site:
            return ""
        return "site" if has_site else "admin_site"

    @staticmethod
    def _newapi_session_payload_error(session: dict[str, Any]) -> str | None:
        browser_cookie = str(session.get("browser_cookie") or "").strip()
        refresh_cookie = str(session.get("browser_refresh_cookie") or "").strip()
        session_id = str(session.get("browser_session_id") or "").strip()
        raw_expires_at = session.get("browser_access_expires_at")
        has_expires_at = raw_expires_at not in (None, "", 0, "0")
        if browser_cookie:
            cookie_parts = [part.strip() for part in browser_cookie.split(";")]
            if not cookie_parts or any(
                not re.fullmatch(r"[A-Za-z0-9_-]+=[^;\s]+", part)
                for part in cookie_parts
            ):
                return "SESSION_COOKIE_INVALID"
            if refresh_cookie or session_id or has_expires_at:
                return "SESSION_FIELDS_INVALID"
            return None
        if refresh_cookie and not re.fullmatch(
            r"new_api_refresh=[^\s;,]+", refresh_cookie
        ):
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

    def _validate_completion_payload(
        self, platform: str, status: str, session: Any
    ) -> tuple[str, str] | None:
        if status == "session_found":
            if not isinstance(session, dict):
                return "SESSION_REQUIRED", "未提供浏览器登录态"
            if set(session) - ALL_SESSION_SYNC_FIELDS:
                return "SESSION_FIELDS_INVALID", "登录态字段无效"
            for key in ALL_SESSION_SYNC_FIELDS:
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
                    return "SESSION_FIELD_TOO_LARGE", "登录态字段过长"
            if platform == "newapi":
                code = self._newapi_session_payload_error(session)
                if code:
                    return (
                        code,
                        "NewAPI Refresh Cookie 必须严格使用 new_api_refresh"
                        if code == "SESSION_COOKIE_INVALID"
                        else "NewAPI 浏览器登录态字段不完整",
                    )
        elif session is not None:
            return "SESSION_NOT_ALLOWED", "无登录态响应不能携带 session"
        return None

    @staticmethod
    def _normalise_expiry(value: Any) -> str:
        """Keep the existing accepted epoch/ISO expiry formats."""
        from datetime import timezone

        text = str(value or "").strip()
        if not text:
            return ""
        try:
            epoch = float(text)
        except (TypeError, ValueError):
            epoch = -1
        if epoch >= 0:
            if epoch >= 100_000_000_000:
                epoch /= 1000
            try:
                return datetime.fromtimestamp(epoch, tz=timezone.utc).astimezone(
                    application_timezone()
                ).isoformat(timespec="seconds")
            except (OverflowError, OSError, ValueError):
                return ""
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return ""
        if parsed.tzinfo is None:
            return ""
        return parsed.isoformat(timespec="seconds")

    def _claim_request(
        self, request_id: str, secret: str
    ) -> tuple[dict[str, Any] | None, str | None]:
        if not str(secret or ""):
            return None, "SYNC_REQUEST_SECRET_REQUIRED"
        row = self.repository.get(str(request_id))
        if not row:
            return None, "SYNC_REQUEST_NOT_FOUND"
        if str(row.get("status") or "") != "pending":
            return None, "SYNC_REQUEST_CONSUMED"
        now = utc_now_iso()
        if _request_expired(row):
            self.repository.finish_request(
                str(request_id),
                "expired",
                "SYNC_REQUEST_EXPIRED",
                "同步请求已过期",
                now,
            )
            return None, "SYNC_REQUEST_EXPIRED"
        expected = str(row.get("secret_hash") or "")
        actual = hashlib.sha256(str(secret).encode("utf-8")).hexdigest()
        if not hmac.compare_digest(expected, actual):
            return None, "SYNC_REQUEST_SECRET_INVALID"
        claimed = self.repository.claim_pending(str(request_id), now)
        return (claimed, None) if claimed else (None, "SYNC_REQUEST_CONSUMED")

    def _finish(
        self, request_id: str, status: str, code: str = "", message: str = ""
    ) -> bool:
        with self._lock:
            return self.repository.finish_request(
                str(request_id), status, code, message, utc_now_iso()
            )

    def _current_site(
        self, site_id: int, platform: str, target_origin: str
    ) -> dict[str, Any] | None:
        target = self.sites.get(int(site_id))
        if (
            not target
            or str(target.get("platform") or "").strip().lower() != platform
            or str(target.get("auth_mode") or "").strip().lower()
            != BROWSER_AUTH_MODE
            or _site_origin(str(target.get("base_url") or "")) != target_origin
        ):
            return None
        return target

    def _current_admin_site(
        self, admin_site_id: int, target_origin: str
    ) -> dict[str, Any] | None:
        target = self.admin_sites.get(int(admin_site_id))
        if (
            not target
            or str(target.get("platform") or "").strip().lower() != "newapi"
            or _site_origin(str(target.get("base_url") or "")) != target_origin
        ):
            return None
        return target

    def _request_is_validating(
        self,
        request_id: str,
        *,
        target_kind: str,
        target_id: int,
        platform: str,
        target_origin: str,
    ) -> bool:
        row = self.repository.get(str(request_id))
        if (
            not row
            or str(row.get("status") or "") != "validating"
            or str(row.get("platform") or "").strip().lower() != platform
            or str(row.get("target_origin") or "") != target_origin
        ):
            return False
        if target_kind == "site":
            return (
                row.get("site_id") is not None
                and int(row.get("site_id") or 0) == int(target_id)
                and row.get("admin_site_id") is None
            )
        return (
            row.get("admin_site_id") is not None
            and int(row.get("admin_site_id") or 0) == int(target_id)
            and row.get("site_id") is None
        )

    def _apply_sub2api_site(
        self, request_row: dict[str, Any], session: dict[str, Any]
    ) -> tuple[bool, str | None]:
        site_id = int(request_row.get("site_id") or 0)
        target_origin = str(request_row.get("target_origin") or "")
        target = self._current_site(site_id, "sub2api", target_origin)
        if not target:
            return False, "同步目标已变更，请重新发起同步"
        ok, _validation, error = self.sub2api.validate_browser_session(
            str(target.get("base_url") or ""), str(session.get("access_token") or "")
        )
        if not ok:
            return False, error
        with self._lock:
            if not self._request_is_validating(
                str(request_row.get("id") or ""),
                target_kind="site",
                target_id=site_id,
                platform="sub2api",
                target_origin=target_origin,
            ) or not self._current_site(site_id, "sub2api", target_origin):
                return False, "同步请求已失效，请重新发起同步"
            now = utc_now_iso()
            applied = self.repository.persist_sub2api_browser_session_cas(
                site_id=site_id,
                request_id=str(request_row.get("id") or ""),
                target_origin=target_origin,
                access_token=str(session.get("access_token") or ""),
                refresh_token=str(session.get("refresh_token") or ""),
                token_expires_at=self._normalise_expiry(
                    session.get("token_expires_at")
                ),
                now=now,
            )
            if not applied:
                return False, "同步请求已失效，请重新发起同步"
            if not self.repository.finish_request(
                str(request_row.get("id") or ""), "ready", now=now
            ):
                return False, "同步请求已失效，请重新发起同步"
        return True, None

    def _apply_newapi_site(
        self, request_row: dict[str, Any], session: dict[str, Any]
    ) -> tuple[bool, str | None]:
        site_id = int(request_row.get("site_id") or 0)
        target_origin = str(request_row.get("target_origin") or "")
        target = self._current_site(site_id, "newapi", target_origin)
        if not target:
            return False, "同步目标已变更，请重新发起同步"
        ok, _validation, error = self.newapi.validate_browser_session(
            str(target.get("base_url") or ""), session
        )
        if not ok:
            return False, error
        with self._lock:
            if not self._request_is_validating(
                str(request_row.get("id") or ""),
                target_kind="site",
                target_id=site_id,
                platform="newapi",
                target_origin=target_origin,
            ) or not self._current_site(site_id, "newapi", target_origin):
                return False, "同步请求已失效，请重新发起同步"
            now = utc_now_iso()
            applied = self.repository.persist_newapi_site_browser_session_cas(
                site_id=site_id,
                request_id=str(request_row.get("id") or ""),
                target_origin=target_origin,
                session=session,
                now=now,
            )
            if not applied:
                return False, "同步请求已失效，请重新发起同步"
            if not self.repository.finish_request(
                str(request_row.get("id") or ""), "ready", now=now
            ):
                return False, "同步请求已失效，请重新发起同步"
        return True, None

    def _apply_newapi_admin_site(
        self, request_row: dict[str, Any], session: dict[str, Any]
    ) -> tuple[bool, str | None]:
        admin_site_id = int(request_row.get("admin_site_id") or 0)
        target_origin = str(request_row.get("target_origin") or "")
        target = self._current_admin_site(admin_site_id, target_origin)
        if not target:
            return False, "同步目标已变更，请重新发起同步"
        ok, validation, error = self.newapi.validate_browser_session(
            str(target.get("base_url") or ""), session
        )
        if not ok:
            return False, error
        account = validation.get("account") if isinstance(validation, dict) else {}
        try:
            is_admin = int(account.get("role") or 0) >= 10
        except (AttributeError, TypeError, ValueError):
            is_admin = False
        if not is_admin:
            return False, "当前浏览器登录用户不是 NewAPI 管理员"
        with self._lock:
            if not self._request_is_validating(
                str(request_row.get("id") or ""),
                target_kind="admin_site",
                target_id=admin_site_id,
                platform="newapi",
                target_origin=target_origin,
            ) or not self._current_admin_site(admin_site_id, target_origin):
                return False, "同步请求已失效，请重新发起同步"
            now = utc_now_iso()
            applied = self.repository.persist_newapi_admin_browser_session_cas(
                admin_site_id=admin_site_id,
                request_id=str(request_row.get("id") or ""),
                target_origin=target_origin,
                session=session,
                now=now,
            )
            if not applied:
                return False, "同步请求已失效，请重新发起同步"
            if not self.repository.finish_request(
                str(request_row.get("id") or ""), "ready", now=now
            ):
                return False, "同步请求已失效，请重新发起同步"
        return True, None

    def _get(
        self, target_kind: str, target_id: int, request_id: str
    ) -> dict[str, Any] | None:
        with self._lock:
            row = self.repository.get_for_target(target_kind, target_id, request_id)
            if not row:
                return None
            if str(row.get("status") or "") in {"pending", "validating"} and _request_expired(row):
                now = utc_now_iso()
                self.repository.finish_request(
                    str(row.get("id") or ""),
                    "expired",
                    "SYNC_REQUEST_EXPIRED",
                    "同步请求已过期",
                    now,
                )
                row = {
                    **row,
                    "status": "expired",
                    "error_code": "SYNC_REQUEST_EXPIRED",
                    "error_message": "同步请求已过期",
                    "updated_at": now,
                }
            return _public_payload(row)

    def _fail(
        self, target_kind: str, target_id: int, request_id: str, code: str
    ) -> tuple[bool, str | None]:
        failure = SESSION_SYNC_PAGE_FAILURES.get(code)
        if not failure:
            return False, "不支持的同步失败代码"
        with self._lock:
            row = self.repository.get_for_target(target_kind, target_id, request_id)
            if not row:
                return False, "同步请求不存在"
            if str(row.get("status") or "") != "pending":
                return False, "同步请求已结束"
            now = utc_now_iso()
            if _request_expired(row):
                self.repository.finish_request(
                    str(request_id),
                    "expired",
                    "SYNC_REQUEST_EXPIRED",
                    "同步请求已过期",
                    now,
                )
                return False, "同步请求已过期"
            status, message = failure
            self.repository.finish_request(str(request_id), status, code, message, now)
            return True, None
