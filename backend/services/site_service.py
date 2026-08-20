"""Site service facade."""
from __future__ import annotations

from datetime import timedelta
from typing import Any

from backend.core.errors import InternalError, NotFoundError, ValidationError
from backend.core.time import app_now, utc_now_iso
from backend.db import normalize_base_url
from backend.repositories.sites import SiteRepository
from backend.services.check_service import CheckService
from backend.services.model_cache import ModelCacheService
from backend.services.newapi_site_auth_service import NewApiSiteAuthService


DEFAULT_INTERVAL_MINUTES = 3
MIN_INTERVAL_MINUTES = 1
BROWSER_AUTH_MODE = "browser"


def _next_check_iso(interval_minutes: int) -> str:
    return (
        app_now() + timedelta(minutes=max(MIN_INTERVAL_MINUTES, interval_minutes))
    ).isoformat(timespec="seconds")


class SiteService:
    def __init__(
        self,
        repository: SiteRepository | None = None,
        checks: CheckService | None = None,
        cache: ModelCacheService | None = None,
    ) -> None:
        self.repository = repository or SiteRepository()
        self.checks = checks or CheckService(sites=self.repository)
        self.cache = cache or ModelCacheService()
        self.newapi_auth = NewApiSiteAuthService(sites=self.repository)

    def get_or_404(self, site_id: int):
        site = self.repository.get(int(site_id))
        if not site:
            raise NotFoundError("site not found")
        return site

    def check(self, site_id: int) -> dict[str, Any]:
        return self.checks.check(site_id)

    def model_cache(self, site_id: int):
        return self.cache.get(site_id)

    def create(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Validate and persist one monitoring site."""
        body = payload if isinstance(payload, dict) else {}
        name = str(body.get("name") or "").strip()
        base_url = normalize_base_url(str(body.get("base_url") or ""))
        platform = str(body.get("platform") or "newapi").strip().lower()
        enabled = bool(body.get("enabled", True))
        interval = int(body.get("interval_minutes") or DEFAULT_INTERVAL_MINUTES)
        interval = max(MIN_INTERVAL_MINUTES, interval)
        login_enabled = bool(body.get("login_enabled", False))
        login_username = str(body.get("login_username") or "").strip()
        login_password = str(body.get("login_password") or "")
        access_token = str(body.get("access_token") or "").strip()
        access_user_id = str(body.get("access_user_id") or "").strip()
        refresh_token = str(body.get("refresh_token") or "").strip()
        token_expires_at = str(body.get("token_expires_at") or "").strip()
        auth_mode = str(body.get("auth_mode") or "password").strip().lower()
        if platform not in {"newapi", "sub2api"}:
            raise ValidationError("platform invalid")
        if auth_mode not in {"password", "token", BROWSER_AUTH_MODE}:
            raise ValidationError("auth_mode invalid")
        if not name or not base_url:
            raise ValidationError("name/base_url required")
        if (
            platform == "newapi"
            and auth_mode == "token"
            and login_enabled
            and (not access_token or not access_user_id)
        ):
            raise ValidationError("使用系统访问令牌时需要填写 NewAPI 用户 ID")
        if platform == "newapi" and auth_mode == "password" and (
            not login_username or not login_password
        ):
            raise ValidationError("NewAPI 用户名密码模式需要填写用户名和密码")
        if platform == "sub2api" and auth_mode == "password" and (
            not login_username or not login_password
        ):
            raise ValidationError("sub2api 需要填写普通用户邮箱和密码")
        if platform == "sub2api" and auth_mode == "token" and not access_token:
            raise ValidationError("导入登录态时需要填写 auth_token")

        now = utc_now_iso()
        site_values = {
            "name": name,
            "base_url": base_url,
            "platform": platform,
            "enabled": enabled,
            "interval_minutes": interval,
            "login_enabled": bool(
                login_enabled
                or platform == "sub2api"
                or auth_mode == BROWSER_AUTH_MODE
                or (platform == "newapi" and auth_mode == "password")
            ),
            "auth_mode": auth_mode,
            "login_username": login_username
            if (
                (platform == "sub2api" and auth_mode in {"password", BROWSER_AUTH_MODE})
                or (platform == "newapi" and auth_mode == "password")
            )
            else "",
            "login_password": login_password
            if (
                (platform == "sub2api" and auth_mode in {"password", BROWSER_AUTH_MODE})
                or (platform == "newapi" and auth_mode == "password")
            )
            else "",
            "access_token": access_token
            if (
                (platform == "newapi" and login_enabled and auth_mode == "token")
                or (platform == "sub2api" and auth_mode in {"token", BROWSER_AUTH_MODE})
            )
            else "",
            "access_user_id": access_user_id
            if platform == "newapi" and login_enabled and auth_mode == "token"
            else "",
            "refresh_token": refresh_token
            if platform == "sub2api" and auth_mode in {"token", BROWSER_AUTH_MODE}
            else "",
            "token_expires_at": token_expires_at
            if platform == "sub2api" and auth_mode in {"token", BROWSER_AUTH_MODE}
            else "",
            "next_check_at": _next_check_iso(interval),
            "status": "unknown",
            "created_at": now,
            "updated_at": now,
        }
        try:
            site_id = self.repository.create(site_values)
            return {"success": True, "id": int(site_id)}
        except Exception as insert_err:
            err_text = str(insert_err).lower()
            if "1062" in err_text or "duplicate" in err_text:
                existing = self.repository.find_by_base_url(base_url)
                if existing and "id" in existing:
                    return {
                        "success": True,
                        "id": int(existing["id"]),
                        "existed": True,
                    }
            raise InternalError("站点保存失败") from insert_err

    def update(self, site_id: int, payload: dict[str, Any]) -> dict[str, Any]:
        """Validate and update one monitoring site."""
        body = payload if isinstance(payload, dict) else {}
        site = self.repository.get(int(site_id))
        if not site:
            raise NotFoundError("site not found")
        fields: list[str] = []
        params: list[Any] = []

        if "name" in body:
            fields.append("name = ?")
            params.append(str(body["name"]).strip())
        if "base_url" in body:
            fields.append("base_url = ?")
            params.append(normalize_base_url(str(body["base_url"])))
        target_platform = str(body.get("platform") or site.get("platform") or "newapi").strip().lower()
        if target_platform not in {"newapi", "sub2api"}:
            raise ValidationError("platform invalid")
        if "platform" in body:
            fields.append("platform = ?")
            params.append(target_platform)
        if "enabled" in body:
            fields.append("enabled = ?")
            params.append(1 if body["enabled"] else 0)
        if "interval_minutes" in body:
            fields.append("interval_minutes = ?")
            params.append(max(MIN_INTERVAL_MINUTES, int(body["interval_minutes"])))
        if "login_enabled" in body:
            login_enabled = bool(body["login_enabled"])
            login_username = str(body.get("login_username") or "").strip()
            login_password = str(body.get("login_password") or "")
            access_token = str(body.get("access_token") or "").strip()
            access_user_id = str(body.get("access_user_id") or "").strip()
            refresh_token = str(body.get("refresh_token") or "").strip()
            token_expires_at = str(body.get("token_expires_at") or "").strip()
            auth_mode = str(body.get("auth_mode") or site.get("auth_mode") or "password").strip().lower()
            existing_access_token = site.get("access_token") or ""
            existing_access_user_id = site.get("access_user_id") or ""
            existing_refresh_token = site.get("refresh_token") or ""
            existing_username = site.get("login_username") or ""
            existing_password = site.get("login_password") or ""
            existing_platform = str(site.get("platform") or "newapi").strip().lower()
            existing_auth_mode = str(site.get("auth_mode") or "password").strip().lower()
            same_platform = existing_platform == target_platform
            same_auth_mode = same_platform and existing_auth_mode == auth_mode
            can_preserve_newapi_auth = same_auth_mode and target_platform == "newapi"
            can_preserve_sub2api_password = (
                same_auth_mode and target_platform == "sub2api" and auth_mode == "password"
            )
            can_preserve_sub2api_token = (
                same_auth_mode and target_platform == "sub2api" and auth_mode == "token"
            )
            can_preserve_newapi_password = (
                same_auth_mode and target_platform == "newapi" and auth_mode == "password"
            )
            if auth_mode not in {"password", "token", BROWSER_AUTH_MODE}:
                raise ValidationError("auth_mode invalid")
            if target_platform == "newapi" and auth_mode == "token":
                has_token_after_update = bool(
                    access_token or (existing_access_token if can_preserve_newapi_auth else "")
                )
                has_user_id_after_update = bool(
                    access_user_id or (existing_access_user_id if can_preserve_newapi_auth else "")
                )
                if login_enabled and (not has_token_after_update or not has_user_id_after_update):
                    raise ValidationError("使用系统访问令牌时需要填写 NewAPI 用户 ID")
            if target_platform == "newapi" and auth_mode == "password" and (
                not (
                    login_username
                    or (existing_username if can_preserve_newapi_password else "")
                )
                or not (
                    login_password
                    or (existing_password if can_preserve_newapi_password else "")
                )
            ):
                raise ValidationError("NewAPI 用户名密码模式需要填写用户名和密码")
            if target_platform == "sub2api" and auth_mode == "password" and (
                not (login_username or (existing_username if can_preserve_sub2api_password else ""))
                or not (login_password or (existing_password if can_preserve_sub2api_password else ""))
            ):
                raise ValidationError("sub2api 需要填写普通用户邮箱和密码")
            if target_platform == "sub2api" and auth_mode == "token" and not (
                access_token or (existing_access_token if can_preserve_sub2api_token else "")
            ):
                raise ValidationError("导入登录态时需要填写 auth_token")
            fields.append("login_enabled = ?")
            params.append(
                1
                if (
                    login_enabled
                    or target_platform == "sub2api"
                    or auth_mode == BROWSER_AUTH_MODE
                    or (target_platform == "newapi" and auth_mode == "password")
                )
                else 0
            )
            fields.append("auth_mode = ?")
            params.append(auth_mode)
            if target_platform == "sub2api":
                if auth_mode == "password" and (login_username or not can_preserve_sub2api_password):
                    fields.append("login_username = ?")
                    params.append(login_username)
                if auth_mode == "password" and (login_password or not can_preserve_sub2api_password):
                    fields.append("login_password = ?")
                    params.append(login_password)
                if auth_mode == "token":
                    fields.append("login_username = ?")
                    params.append("")
                    fields.append("login_password = ?")
                    params.append("")
                if access_token or not can_preserve_sub2api_token:
                    fields.append("access_token = ?")
                    params.append(access_token)
                if refresh_token or not can_preserve_sub2api_token:
                    fields.append("refresh_token = ?")
                    params.append(refresh_token)
                if token_expires_at or not can_preserve_sub2api_token:
                    fields.append("token_expires_at = ?")
                    params.append(token_expires_at)
            if target_platform == "newapi":
                if auth_mode == "password":
                    if login_username or not can_preserve_newapi_password:
                        fields.append("login_username = ?")
                        params.append(login_username)
                    if login_password or not can_preserve_newapi_password:
                        fields.append("login_password = ?")
                        params.append(login_password)
                if access_token or not can_preserve_newapi_auth:
                    fields.append("access_token = ?")
                    params.append(access_token)
                if access_user_id or not can_preserve_newapi_auth:
                    fields.append("access_user_id = ?")
                    params.append(access_user_id)
                if (
                    not can_preserve_newapi_auth
                    or (auth_mode == "password" and (login_username or login_password))
                ):
                    fields.append("browser_cookie = ?")
                    params.append(None)
                    fields.append("browser_refresh_cookie = ?")
                    params.append(None)
                    fields.append("browser_session_id = ?")
                    params.append(None)
                    fields.append("browser_access_expires_at = ?")
                    params.append(0)
                if not same_auth_mode or (
                    auth_mode == "password" and (login_username or login_password)
                ):
                    fields.append("session_sync_status = ?")
                    params.append("not_requested")
                    fields.append("session_sync_error = ?")
                    params.append(None)
                    fields.append("session_synced_at = ?")
                    params.append(None)
        if "status" in body:
            fields.append("status = ?")
            params.append(str(body["status"]))
        if not fields:
            raise ValidationError("no fields")
        # Convert the validated assignment list into repository values.  The
        # repository owns SQL construction and its column allow-list; this
        # service remains responsible only for the legacy field semantics.
        updates = {
            assignment.split(" = ?", 1)[0]: value
            for assignment, value in zip(fields, params)
        }
        updates["updated_at"] = utc_now_iso()
        self.repository.update_fields(int(site_id), updates)
        self.cache.invalidate(int(site_id))
        self.cache.schedule(int(site_id))
        return {"success": True}

    def delete(self, site_id: int) -> dict[str, Any]:
        self.repository.delete(int(site_id))
        self.cache.invalidate(int(site_id))
        return {"success": True}

    def password_login(self, site_id: int, two_factor_code: str) -> dict[str, Any]:
        site = self.repository.get(int(site_id))
        if not site:
            raise NotFoundError("site not found")
        ok, result, error = self.newapi_auth.password_login(
            site, str(two_factor_code or "").strip()
        )
        if not ok:
            return {
                "success": False,
                "requires_2fa": bool(result.get("requires_2fa")),
                "message": error or "NewAPI 登录失败",
            }
        return {
            "success": True,
            "message": "NewAPI 用户登录成功",
            "groups_count": result.get("groups_count", 0),
            "warning": result.get("warning"),
        }
