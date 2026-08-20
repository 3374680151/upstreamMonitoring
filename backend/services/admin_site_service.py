"""Service layer for admin sites, channels, and channel upstream bindings.

This is the brain of the migration: every business rule the legacy
handler enforced by hand now lives here.  The router layer is expected
to be a thin pass-through that only does HTTP serialization.

Layering rules (enforced by review, not by an interface):

* The router MUST NOT call ``legacy.*`` functions directly.
* The router MUST NOT touch the repository or integration clients.
* The service MUST NOT call ``request`` / ``Response`` FastAPI symbols.
* The service MUST NOT raise ``HTTPException``; raise ``DomainError``
  subclasses and let the router map them.
* The service is the only place that maps a platform string to a
  concrete integration client.
"""

from __future__ import annotations

import json
import re
from typing import Any, Optional, Tuple

from backend.core.capabilities import (
    capabilities_for,
    supports as platform_supports,
)
from backend.core.errors import (
    CapabilityError,
    ConflictError,
    KeyRefreshError,
    NotFoundError,
    UpstreamError,
    ValidationError,
)
from backend.core.sanitize import (
    is_masked_key,
    mask_channel_key,
    safe_value,
    sanitize_error_text,
)
from backend.core.time import utc_now_iso
from backend.integrations.clients import NewApiClient, Sub2ApiClient
from backend.integrations.newapi_admin import validate_admin_site_base_url
from backend.integrations.sub2api_admin import (
    proxy_error_response,
    validate_channel_patch as validate_sub2api_channel_patch,
)
from backend.repositories.admin_sites import (
    ADMIN_SITE_MUTABLE_COLUMNS,
    AdminSiteRepository,
    ChannelKeyCacheRepository,
    ChannelUpstreamBindingRepository,
)
from backend.services.channel_match_service import ChannelMatchService
from backend.services.newapi_protected_key_service import NewApiProtectedKeyService


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _classify_key_error(message: str) -> str:
    """Match the legacy 11390-11400 classification rules."""
    if "429" in message or "限流" in message:
        return "rate_limited"
    if any(marker in message for marker in ("安全验证", "2FA", "proof")):
        return "security_verification_required"
    return "key_refresh_failed"


def _binding_row_to_payload(row: Optional[dict[str, Any]]) -> dict[str, Any]:
    """Match the legacy ``channel_upstream_binding_payload`` contract exactly.

    Keeping the same field names / shapes is the cheapest way to keep the
    frontend un-touched during the migration.
    """
    if not row:
        return {
            "configured": False,
            "match_status": "unmatched",
            "matched_groups": [],
        }
    matched_groups: Any = []
    try:
        matched_groups = json.loads(row.get("matched_groups_json") or "[]")
    except (TypeError, ValueError):
        matched_groups = []
    if not isinstance(matched_groups, list):
        matched_groups = []
    return {
        "configured": bool(row.get("upstream_base_url")),
        "upstream_base_url": row.get("upstream_base_url") or "",
        "upstream_platform": row.get("upstream_platform") or "newapi",
        "auth_mode": row.get("auth_mode") or "token",
        "has_login_username": bool(row.get("login_username")),
        "has_login_password": bool(row.get("login_password")),
        "has_access_token": bool(row.get("access_token")),
        "has_refresh_token": bool(row.get("refresh_token")),
        "access_user_id": row.get("access_user_id") or "",
        "has_channel_key": bool(row.get("channel_key")),
        "match_status": row.get("match_status") or "unmatched",
        "match_message": row.get("match_message") or "",
        "matched_groups": matched_groups,
        "matched_at": row.get("matched_at"),
    }


_CHANNEL_LIST_SECRET_FIELDS = frozenset(
    {
        "access_token",
        "api_key",
        "api_token",
        "authorization",
        "browser_access_token",
        "browser_refresh_cookie",
        "channel_key",
        "login_password",
        "password",
        "refresh_token",
        "security_proof",
        "secret",
        "token",
    }
)


def _safe_channel_list_item(channel: dict[str, Any]) -> dict[str, Any]:
    """Return a list-safe channel row for either upstream platform.

    NewAPI has historically exposed a masked ``key`` in list responses.
    sub2api payloads are less uniform and can include either ``key`` or
    ``channel_key``.  Preserve the former UI behavior while never allowing a
    plaintext credential from either upstream into a collection response.
    """
    safe = dict(channel)
    key = safe.get("key")
    channel_key = safe.get("channel_key")
    source_key = key if key is not None else channel_key
    if source_key is not None:
        safe["key"] = mask_channel_key(source_key)
        safe["key_masked"] = True
        safe["has_key"] = bool(str(source_key).strip())
    for field in _CHANNEL_LIST_SECRET_FIELDS:
        safe.pop(field, None)
    return safe


def _safe_upstream_metadata(payload: Any) -> dict[str, Any]:
    """Drop credential fields before retaining an upstream diagnostic."""
    safe = safe_value(payload)
    if isinstance(safe, dict):
        return safe
    if safe is None:
        return {}
    return {"raw": sanitize_error_text(safe)}


def _safe_upstream_success(payload: Any) -> dict[str, Any]:
    """Keep useful mutation metadata without echoing upstream credentials."""
    safe = safe_value(payload)
    if isinstance(safe, dict):
        return safe
    return {"data": safe}


def _sub2api_upstream_error(
    payload: Any,
    error: Optional[str],
    fallback_message: str,
) -> UpstreamError:
    """Translate sub2api failures into the legacy-safe public envelope."""
    status_code, envelope = proxy_error_response(
        payload, error, fallback_message
    )
    category = str(envelope.get("category") or "upstream_error")
    details = {
        key: value
        for key, value in envelope.items()
        if key not in {"success", "message"}
    }
    exc = UpstreamError(
        str(envelope.get("message") or fallback_message),
        upstream=details,
        code=f"sub2api_{category}",
    )
    exc.status_code = status_code
    return exc


def _upstream_error_for_site(
    site: dict[str, Any],
    payload: Any,
    error: Optional[str],
    fallback_message: str,
) -> UpstreamError:
    """Preserve sub2api's classified errors while sanitizing all payloads."""
    platform = str(site.get("platform") or "newapi").strip().lower()
    if platform == "sub2api":
        return _sub2api_upstream_error(payload, error, fallback_message)
    return UpstreamError(
        error or fallback_message,
        upstream=_safe_upstream_metadata(payload),
    )


_NEWAPI_CHANNEL_UPDATE_FIELDS = frozenset(
    {
        "name",
        "type",
        "key",
        "base_url",
        "models",
        "group",
        "weight",
        "priority",
        "status",
        "model_mapping",
        "tag",
        "test_model",
        "auto_ban",
    }
)


# ---------------------------------------------------------------------------
# AdminSiteService
# ---------------------------------------------------------------------------


class AdminSiteService:
    """All admin-site / channel business logic."""

    def __init__(
        self,
        admin_repo: Optional[AdminSiteRepository] = None,
        key_cache: Optional[ChannelKeyCacheRepository] = None,
        binding_repo: Optional[ChannelUpstreamBindingRepository] = None,
    ) -> None:
        self.admin_repo = admin_repo or AdminSiteRepository()
        self.key_cache = key_cache or ChannelKeyCacheRepository()
        self.binding_repo = binding_repo or ChannelUpstreamBindingRepository()
        self.protected_keys = NewApiProtectedKeyService(
            admins=self.admin_repo,
            keys=self.key_cache,
        )
        self.channel_matcher = ChannelMatchService(
            keys=self.key_cache,
            bindings=self.binding_repo,
        )

    # ========== internal helpers ==========

    def _must_get_site(self, admin_site_id: int) -> dict[str, Any]:
        site = self.admin_repo.get(admin_site_id)
        if not site:
            raise NotFoundError("管理站点不存在")
        return site

    def _client_for(self, site: dict[str, Any]):
        """Platform dispatch — the only place that branches on platform.

        The full site row is forwarded so the integration layer can
        preserve browser session, security proof, and sub2api token
        state without re-querying the database.
        """
        platform = (site.get("platform") or "newapi").strip().lower()
        if platform == "newapi":
            return NewApiClient(site=site)
        if platform == "sub2api":
            return Sub2ApiClient(site=site)
        raise ValidationError(f"未知平台: {platform}")

    def _require_capability(self, site: dict[str, Any], capability: str) -> None:
        platform = (site.get("platform") or "newapi").strip().lower()
        if not platform_supports(platform, capability):
            raise CapabilityError(f"平台 {platform} 不支持操作: {capability}")

    # ========== admin_site CRUD ==========

    def list_sites(self) -> list[dict[str, Any]]:
        """Return all admin sites with capability maps and the safe (masked)
        flags the frontend consumes.  Tokens are NEVER returned."""
        rows = self.admin_repo.list()
        out: list[dict[str, Any]] = []
        for row in rows:
            platform = (row.get("platform") or "newapi").strip().lower()
            out.append(
                {
                    "id": int(row["id"]),
                    "name": str(row.get("name") or ""),
                    "platform": platform,
                    "platform_label": ("sub2api" if platform == "sub2api" else "NewAPI"),
                    "capabilities": capabilities_for(platform),
                    "base_url": str(row.get("base_url") or ""),
                    "access_user_id": row.get("access_user_id") or "",
                    "has_access_token": bool(row.get("access_token")),
                    "login_username": row.get("login_username") or "",
                    "has_login_password": bool(row.get("login_password")),
                    "has_sub2api_session": bool(
                        row.get("sub2api_access_token")
                        and row.get("sub2api_refresh_token")
                    ),
                    # Only expose whether an admin browser session exists;
                    # the session token/cookies remain repository-internal.
                    "has_browser_session": bool(
                        row.get("browser_access_token")
                        or row.get("browser_refresh_cookie")
                        or row.get("browser_session_id")
                    ),
                    "login_last_error": row.get("browser_login_last_error"),
                    "login_last_check_at": row.get("browser_login_last_check_at"),
                    "has_security_proof": bool(row.get("security_proof")),
                    "security_proof_verified_at": row.get("security_proof_verified_at"),
                    "key_sync_enabled": bool(row.get("key_sync_enabled")),
                    "key_sync_interval_minutes": int(
                        row.get("key_sync_interval_minutes") or 5
                    ),
                    "key_sync_last_at": row.get("key_sync_last_at"),
                    "key_sync_next_at": row.get("key_sync_next_at"),
                    "key_sync_last_error": row.get("key_sync_last_error"),
                    "key_sync_backoff_until": row.get("key_sync_backoff_until"),
                    "key_sync_failure_count": int(
                        row.get("key_sync_failure_count") or 0
                    ),
                    "created_at": row.get("created_at"),
                    "updated_at": row.get("updated_at"),
                }
            )
        return out

    def get_site(self, admin_site_id: int) -> dict[str, Any]:
        return self._must_get_site(admin_site_id)

    def test_connection(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Verify connectivity without persisting.

        Returns the same shape the legacy handler did
        (``{platform, groups_count|channels_count, ...}``) so the
        frontend can keep its existing ``ConnectionTestResult`` parse.
        """
        body = dict(payload or {})
        saved: dict[str, Any] = {}
        if body.get("admin_site_id"):
            try:
                saved = self.admin_repo.get(int(body["admin_site_id"])) or {}
            except (TypeError, ValueError):
                saved = {}
        platform = str(body.get("platform") or saved.get("platform") or "newapi").strip().lower()
        if platform not in {"newapi", "sub2api"}:
            raise ValidationError("主站平台无效")
        if saved and platform != str(saved.get("platform") or "newapi").strip().lower():
            raise ValidationError("主站平台与已保存配置不一致")
        base_url, base_error = validate_admin_site_base_url(
            body.get("base_url") or saved.get("base_url") or ""
        )
        if base_error:
            raise ValidationError(base_error)
        if platform == "newapi":
            access_token = str(body.get("access_token") or saved.get("access_token") or "").strip()
            access_user_id = str(body.get("access_user_id") or saved.get("access_user_id") or "").strip()
            if not access_token or not access_user_id:
                raise ValidationError("请填写管理员系统访问令牌和 NewAPI 用户 ID")
            ok, result, error = NewApiClient(
                site={
                    **saved,
                    "base_url": base_url,
                    "access_token": access_token,
                    "access_user_id": access_user_id,
                    "platform": "newapi",
                }
            ).test_connection()
        else:
            username = str(body.get("login_username") or saved.get("login_username") or "").strip()
            password = str(body.get("login_password") or saved.get("login_password") or "")
            if not username or not password:
                raise ValidationError("sub2api 主站需要管理员邮箱和密码")
            client, login_error, login_payload = Sub2ApiClient.from_login_with_result(
                base_url, username, password
            )
            if client is None:
                raise _sub2api_upstream_error(
                    login_payload, login_error, "sub2api 主站登录失败"
                )
            ok, result, error = client.test_connection()
        if not ok:
            if platform == "sub2api":
                details = result.get("details") if isinstance(result, dict) else result
                raise _sub2api_upstream_error(
                    details,
                    error,
                    "sub2api 主站连接测试失败",
                )
            raise UpstreamError(
                error or "主站连接测试失败",
                upstream=_safe_upstream_metadata(result),
            )
        # Keep the stable wire contract used by the admin-site connection
        # dialog.  The low-level protocol probes intentionally return only
        # their platform-specific count (``groups_count`` or
        # ``channels_count``), while the FastAPI response model requires the
        # selected platform discriminator as well.
        if isinstance(result, dict):
            result = dict(result)
            result.setdefault("platform", platform)
        else:
            result = {"platform": platform}
        return result

    def create_site(self, payload: dict[str, Any]) -> int:
        """Validate + create a new management site.

        NewAPI rows are inserted with the supplied access_token / user_id.
        sub2api rows are created only after a fresh admin login so the
        stored access/refresh tokens always reflect a successful auth —
        a failed login never leaves a half-configured site behind.
        """
        platform = str(payload.get("platform") or "newapi").strip().lower()
        # ``admin_site_id`` exists only for the edit-dialog connection probe;
        # it is not a column and must never cross the persistence boundary.
        fields = {
            key: value for key, value in payload.items() if key != "admin_site_id"
        }
        if platform not in {"newapi", "sub2api"}:
            raise ValidationError("主站平台只支持 NewAPI 或 sub2api")
        name = str(fields.get("name") or "").strip()
        base_url, base_url_error = validate_admin_site_base_url(
            str(fields.get("base_url") or "")
        )
        if not name or not base_url:
            raise ValidationError("请填写管理站点名称和 Base URL")
        if base_url_error:
            raise ValidationError(base_url_error)
        fields["name"] = name
        fields["base_url"] = base_url
        fields["platform"] = platform
        try:
            interval = max(
                5,
                min(1440, int(fields.get("key_sync_interval_minutes") or 5)),
            )
        except (TypeError, ValueError):
            raise ValidationError("key 自动更新间隔无效")
        key_sync_enabled = bool(fields.get("key_sync_enabled")) and platform == "newapi"
        fields["key_sync_enabled"] = key_sync_enabled
        fields["key_sync_interval_minutes"] = interval
        if key_sync_enabled:
            fields["key_sync_next_at"] = utc_now_iso()
        if platform == "sub2api":
            for field in ("access_token", "access_user_id"):
                if str(fields.get(field) or "").strip():
                    raise ValidationError("sub2api 主站不使用 NewAPI 系统访问凭据")
                fields.pop(field, None)
            if not str(fields.get("login_username") or "").strip() or not str(
                fields.get("login_password") or ""
            ):
                raise ValidationError("请填写 sub2api 管理员邮箱和密码")
            # Login once and use the returned token for the connectivity probe.
            # Calling the public test_connection path first would perform a
            # second login, which is wasteful and can trip upstream rate
            # limits or invalidate a one-time auth response.
            client, login_error, login_payload = Sub2ApiClient.from_login_with_result(
                fields.get("base_url") or "",
                str(fields.get("login_username") or "").strip(),
                str(fields.get("login_password") or ""),
            )
            if client is None:
                raise _sub2api_upstream_error(
                    login_payload, login_error, "sub2api 主站登录失败"
                )
            ok, probe, probe_error = client.test_connection()
            if not ok:
                raise _sub2api_upstream_error(
                    probe, probe_error, "sub2api 主站连接测试失败"
                )
            fields["sub2api_access_token"] = client.access_token
            fields["sub2api_refresh_token"] = client.refresh_token
            fields["sub2api_access_expires_at"] = client.access_expires_at
            fields["browser_login_last_check_at"] = utc_now_iso()
        else:
            if not str(fields.get("access_token") or "").strip() or not str(
                fields.get("access_user_id") or ""
            ).strip():
                raise ValidationError("请填写管理员系统访问令牌和 NewAPI 用户 ID")
            # ``test_connection`` already normalises the legacy triplet into a
            # domain result (or raises ``UpstreamError``).  Treating that dict
            # as a tuple made every successful create fail before the INSERT.
            self.test_connection(fields)
        return self.admin_repo.create(fields)

    def update_site(self, admin_site_id: int, fields: dict[str, Any]) -> None:
        """Patchable update that mirrors the legacy ``update_admin_site``.

        - ``platform`` is locked once set: 409 if the caller tries to flip it.
        - Empty-string secrets mean "keep the old value" so the edit form
          never has to re-enter a token just to rename a site.
        - sub2api credential changes trigger a fresh admin login so the
          access/refresh tokens stay in sync with the new credentials.
        - NewAPI login-credential changes clear the browser session and
          security proof so the next key read is forced through a real
          2FA proof instead of a stale one.
        - key_sync configuration changes reset backoff / failure counters
          and recompute ``key_sync_next_at`` so the scheduler can pick up
          the new interval immediately.
        """
        existing = self._must_get_site(admin_site_id)
        if "platform" in fields:
            if str(fields.get("platform") or "").strip().lower() != (
                existing.get("platform") or "newapi"
            ).strip().lower():
                raise ConflictError("平台不可修改")
            # Same-platform values are harmless but are not writable columns
            # in the repository.  Remove them after enforcing the lock.
            fields = {key: value for key, value in fields.items() if key != "platform"}
        if not fields:
            return

        platform = (existing.get("platform") or "newapi").strip().lower()
        if platform == "sub2api":
            for field in ("access_token", "access_user_id"):
                if str(fields.get(field) or "").strip():
                    raise ValidationError("sub2api 主站不使用 NewAPI 系统访问凭据")
                fields.pop(field, None)

        if "name" in fields:
            name = str(fields.get("name") or "").strip()
            if not name:
                raise ValidationError("名称不能为空")
            fields["name"] = name
        if "base_url" in fields:
            base_url, base_url_error = validate_admin_site_base_url(
                str(fields.get("base_url") or "")
            )
            if base_url_error:
                raise ValidationError(base_url_error)
            fields["base_url"] = base_url

        merged = self._merge_keep_existing(fields, existing)

        # ---- sub2api: re-login on credential change ----
        if platform == "sub2api":
            credentials_changed = self._sub2api_credentials_changed(
                merged, existing
            )
            if credentials_changed:
                username = str(
                    merged.get("login_username", existing.get("login_username"))
                    or ""
                ).strip()
                password = str(
                    merged.get("login_password", existing.get("login_password"))
                    or ""
                )
                if not username or not password:
                    raise ValidationError("sub2api 主站需要管理员邮箱和密码")
                client, login_error, login_payload = Sub2ApiClient.from_login_with_result(
                    merged.get("base_url") or existing.get("base_url") or "",
                    username,
                    password,
                )
                if client is None:
                    raise _sub2api_upstream_error(
                        login_payload, login_error, "sub2api 主站登录失败"
                    )
                merged["sub2api_access_token"] = client.access_token
                merged["sub2api_refresh_token"] = client.refresh_token
                merged["sub2api_access_expires_at"] = client.access_expires_at
                merged["browser_login_last_error"] = None

        # ---- NewAPI: clear browser session on login-credential change ----
        if platform == "newapi" and self._newapi_login_credentials_changed(
            merged, existing
        ):
            for col in (
                "browser_access_token",
                "browser_refresh_cookie",
                "browser_session_id",
                "browser_access_expires_at",
                "security_proof",
                "security_proof_verified_at",
            ):
                merged[col] = None
            merged["browser_login_last_error"] = None

        # ---- key_sync configuration change ----
        if (
            "key_sync_enabled" in fields
            or "key_sync_interval_minutes" in fields
        ):
            enabled = bool(merged.get("key_sync_enabled")) and platform == "newapi"
            try:
                interval = max(
                    5,
                    min(
                        1440,
                        int(merged.get("key_sync_interval_minutes") or 5),
                    ),
                )
            except (TypeError, ValueError):
                raise ValidationError("key 自动更新间隔无效")
            merged["key_sync_enabled"] = 1 if enabled else 0
            merged["key_sync_interval_minutes"] = interval
            merged["key_sync_next_at"] = (
                utc_now_iso() if enabled else None
            )
            merged["key_sync_last_error"] = None
            merged["key_sync_backoff_until"] = None
            merged["key_sync_failure_count"] = 0

        affected = self.admin_repo.update(admin_site_id, merged)
        # MySQL reports rowcount=0 for a valid no-op update (all values are
        # already equal).  The row was checked above, so only treat a zero
        # count as NotFound if a concurrent delete actually removed it.
        if affected == 0 and not self.admin_repo.get(admin_site_id):
            raise NotFoundError("管理站点不存在")

    @staticmethod
    def _merge_keep_existing(
        fields: dict[str, Any], existing: dict[str, Any]
    ) -> dict[str, Any]:
        """Empty / null secret fields keep the stored value.

        Mirrors the legacy ``update_admin_site`` semantics: the edit
        form is allowed to leave ``access_token`` and ``login_password``
        blank and the stored value must survive the round-trip.
        """
        keep_if_blank = {
            "access_token",
            "access_user_id",
            "login_username",
            "login_password",
            "refresh_token",
            "sub2api_access_token",
            "sub2api_refresh_token",
            "token_expires_at",
        }
        merged: dict[str, Any] = {}
        for key, value in fields.items():
            if key in keep_if_blank:
                text = "" if value is None else str(value)
                if not text.strip():
                    continue
                merged[key] = text
            else:
                merged[key] = value
        return merged

    @staticmethod
    def _sub2api_credentials_changed(
        merged: dict[str, Any], existing: dict[str, Any]
    ) -> bool:
        if "base_url" in merged and str(merged["base_url"] or "").strip() != str(
            existing.get("base_url") or ""
        ).strip():
            return True
        if "login_username" in merged and str(merged["login_username"] or "").strip() != str(
            existing.get("login_username") or ""
        ).strip():
            return True
        if "login_password" in merged:
            return True
        return False

    @staticmethod
    def _newapi_login_credentials_changed(
        merged: dict[str, Any], existing: dict[str, Any]
    ) -> bool:
        if "login_username" in merged and str(merged["login_username"] or "").strip() != str(
            existing.get("login_username") or ""
        ).strip():
            return True
        if "login_password" in merged:
            return True
        return False

    def delete_site(self, admin_site_id: int) -> None:
        self._must_get_site(admin_site_id)
        # Atomic in the repository: bindings → key cache → admin_site.
        self.admin_repo.delete(admin_site_id)

    def verify_key(self, admin_site_id: int, code: str) -> None:
        """Issue a NewAPI 2FA proof (sub2api raises CapabilityError)."""
        site = self._must_get_site(admin_site_id)
        self._require_capability(site, "key_verification")
        ok, error = self.protected_keys.verify_access(site, code)
        if not ok:
            raise ValidationError(error or "主站安全验证失败")

    # ========== channel CRUD ==========

    def list_channels(
        self, admin_site_id: int, keyword: str = ""
    ) -> dict[str, Any]:
        site = self._must_get_site(admin_site_id)
        ok, items, meta, error = self._client_for(site).list_channels(
            keyword=keyword
        )
        if not ok:
            upstream = meta if isinstance(meta, dict) else {}
            raise _upstream_error_for_site(site, upstream, error, "读取渠道失败")
        # Both platform adapters can return credentials in an upstream channel
        # row.  List responses are a collection boundary and must always mask
        # or remove them, regardless of platform.
        items = [
            _safe_channel_list_item(item)
            for item in items
            if isinstance(item, dict)
        ]
        return {"success": True, "data": items, "meta": meta}

    def get_channel_detail(
        self, admin_site_id: int, channel_id: int, include_key: bool = False
    ) -> dict[str, Any]:
        site = self._must_get_site(admin_site_id)
        client = self._client_for(site)
        ok, payload, error = client.get_channel(channel_id)
        if not ok:
            raise _upstream_error_for_site(site, payload, error, "读取渠道详情失败")
        detail = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(detail, dict):
            detail = payload if isinstance(payload, dict) else {}
        # Mask key for list views; the router only sets include_key=True on
        # the explicit detail endpoint.
        if not include_key and "key" in detail:
            detail = dict(detail)
            detail["key"] = mask_channel_key(detail.get("key"))
            detail["key_masked"] = True
        # sub2api detail is already a rich row; NewAPI needs the masking.
        if include_key and (site.get("platform") or "newapi").strip().lower() == "newapi":
            if is_masked_key(detail.get("key")):
                ok_key, key_value, key_error = client.get_channel_key(channel_id)
                if ok_key:
                    detail = dict(detail)
                    detail["key"] = key_value
                else:
                    detail = dict(detail)
                    detail["key_error"] = key_error
        return detail

    def create_channel(self, admin_site_id: int, payload: dict[str, Any]) -> dict[str, Any]:
        site = self._must_get_site(admin_site_id)
        self._require_capability(site, "create_channel")
        if (site.get("platform") or "").strip().lower() == "sub2api":
            raise CapabilityError("sub2api 主站不允许在本系统新建渠道")
        client = self._client_for(site)
        ok, upstream, error = client.create_channel(payload)
        if not ok:
            raise _upstream_error_for_site(site, upstream, error, "创建渠道失败")
        # Cache the plaintext key if it was included in the create body.
        new_id = upstream.get("id")
        if (
            new_id is None
            and isinstance(upstream.get("data"), dict)
            and "id" in upstream["data"]
        ):
            new_id = upstream["data"]["id"]
        if new_id and "key" in payload and not is_masked_key(
            payload.get("key") or ""
        ):
            self.key_cache.upsert(admin_site_id, int(new_id), payload["key"])
        return _safe_upstream_success(upstream)

    def update_channel(
        self, admin_site_id: int, channel_id: int, patch: dict[str, Any]
    ) -> dict[str, Any]:
        site = self._must_get_site(admin_site_id)
        platform = (site.get("platform") or "newapi").strip().lower()
        if not patch:
            raise ValidationError("没有要更新的渠道字段")
        if platform == "sub2api":
            err = validate_sub2api_channel_patch(patch)
            if err:
                raise ValidationError(err)
        else:
            unknown = sorted(set(patch) - _NEWAPI_CHANNEL_UPDATE_FIELDS)
            if unknown:
                raise ValidationError(
                    f"NewAPI 渠道不允许更新字段：{', '.join(unknown)}"
                )
            if "status" in patch:
                status = patch["status"]
                if (
                    isinstance(status, bool)
                    or not isinstance(status, int)
                    or status not in {1, 2}
                ):
                    raise ValidationError(
                        "NewAPI 渠道状态只允许启用(1)或手动停用(2)"
                    )
        client = self._client_for(site)
        ok, upstream, error = client.update_channel(channel_id, patch)
        if not ok:
            raise _upstream_error_for_site(site, upstream, error, "更新渠道失败")
        if platform == "newapi" and "key" in patch and not is_masked_key(
            patch.get("key") or ""
        ):
            self.key_cache.upsert(admin_site_id, channel_id, patch["key"])
        return _safe_upstream_success(upstream)

    def delete_channel(self, admin_site_id: int, channel_id: int) -> dict[str, Any]:
        site = self._must_get_site(admin_site_id)
        self._require_capability(site, "delete_channel")
        if (site.get("platform") or "").strip().lower() == "sub2api":
            raise CapabilityError("sub2api 主站不允许在本系统删除渠道")
        client = self._client_for(site)
        ok, upstream, error = client.delete_channel(channel_id)
        if not ok:
            raise _upstream_error_for_site(site, upstream, error, "删除渠道失败")
        # Bug fix: cascade the local cache.  Legacy handler only did this
        # on batch delete; single-channel delete left orphan rows.
        self.key_cache.clear(admin_site_id, channel_id)
        self.binding_repo.delete(admin_site_id, channel_id)
        return _safe_upstream_success(upstream)

    def batch_channels(
        self,
        admin_site_id: int,
        action: str,
        ids: list[int],
        extra: dict[str, Any],
    ) -> dict[str, Any]:
        site = self._must_get_site(admin_site_id)
        self._require_capability(site, "batch_channel")
        client = self._client_for(site)
        ok, payload, error = client.batch_channel(action, ids, extra)
        if not ok:
            raise _upstream_error_for_site(site, payload, error, "批量操作失败")
        if action == "delete":
            # Cascade the same way as single-channel delete.
            self.key_cache.clear_many(admin_site_id, ids)
            self.binding_repo.delete_many(admin_site_id, ids)
        return _safe_upstream_success(payload)

    def test_channel(self, admin_site_id: int, channel_id: int) -> dict[str, Any]:
        site = self._must_get_site(admin_site_id)
        platform = (site.get("platform") or "newapi").strip().lower()
        if platform != "newapi":
            raise CapabilityError("sub2api 主站不支持 NewAPI 渠道测试接口")
        client = self._client_for(site)
        ok, payload, error = client.test_channel(channel_id)
        if not ok:
            raise _upstream_error_for_site(site, payload, error, "测试渠道失败")
        return _safe_upstream_success(payload)

    # ========== channel upstream bindings ==========

    def list_bindings(self, admin_site_id: int) -> dict[str, dict[str, Any]]:
        site = self._must_get_site(admin_site_id)
        platform = (site.get("platform") or "newapi").strip().lower()
        if not platform_supports(platform, "channel_key_match"):
            raise CapabilityError("该主站不支持渠道 key 匹配")
        rows = self.binding_repo.list_by_site(admin_site_id)
        return {
            str(row["channel_id"]): _binding_row_to_payload(row) for row in rows
        }

    def get_binding(
        self, admin_site_id: int, channel_id: int
    ) -> dict[str, Any]:
        site = self._must_get_site(admin_site_id)
        self._require_capability(site, "channel_key_match")
        row = self.binding_repo.get(admin_site_id, channel_id)
        return _binding_row_to_payload(row)

    def save_binding(
        self, admin_site_id: int, channel_id: int, payload: dict[str, Any]
    ) -> dict[str, Any]:
        site = self._must_get_site(admin_site_id)
        self._require_capability(site, "channel_key_match")
        if (site.get("platform") or "").strip().lower() == "sub2api":
            raise CapabilityError("sub2api 主站不使用渠道 key 匹配配置")

        # Binding merge rules are complex enough to deserve their own block.
        # Translate them here so the repository stays focused on persistence.
        existing = self.binding_repo.get(admin_site_id, channel_id) or {}
        platform = (
            str(
                payload.get("upstream_platform")
                or existing.get("upstream_platform")
                or "newapi"
            )
            .strip()
            .lower()
        )
        auth_mode = (
            str(
                payload.get("auth_mode")
                or existing.get("auth_mode")
                or "token"
            )
            .strip()
            .lower()
        )
        if platform not in {"newapi", "sub2api"}:
            raise ValidationError("上游平台只支持 NewAPI 或 sub2api")
        if auth_mode not in {"password", "token"}:
            raise ValidationError("上游认证方式无效")

        existing_platform = (
            str(existing.get("upstream_platform") or "newapi").strip().lower()
        )
        existing_auth_mode = (
            str(existing.get("auth_mode") or "token").strip().lower()
        )
        same_platform = existing_platform == platform
        same_auth_mode = same_platform and existing_auth_mode == auth_mode

        def merged(name: str, preserve: bool = False) -> str:
            new = str(payload.get(name) or "").strip()
            if new:
                return new
            return (
                str(existing.get(name) or "").strip()
                if preserve
                else ""
            )

        raw_base_url = (
            payload.get("upstream_base_url")
            if "upstream_base_url" in payload
            else existing.get("upstream_base_url") or ""
        )
        if str(raw_base_url or "").strip():
            base_url, base_url_error = validate_admin_site_base_url(
                str(raw_base_url)
            )
            if base_url_error:
                raise ValidationError(base_url_error)
        else:
            # An empty value explicitly clears the optional binding.  It is
            # distinct from a malformed non-empty URL, which must be rejected.
            base_url = ""
        fields: dict[str, Any] = {
            "upstream_base_url": base_url,
            "upstream_platform": platform,
            "auth_mode": auth_mode,
            "login_username": merged(
                "login_username",
                same_auth_mode and auth_mode == "password",
            ),
            "login_password": merged(
                "login_password",
                same_auth_mode and auth_mode == "password",
            ),
            "access_token": merged(
                "access_token",
                same_platform
                and (platform == "newapi" or (auth_mode == "token" and same_auth_mode)),
            ),
            "access_user_id": merged(
                "access_user_id", same_platform and platform == "newapi"
            ),
            "refresh_token": merged(
                "refresh_token",
                same_auth_mode and platform == "sub2api" and auth_mode == "token",
            ),
            "channel_key": merged("channel_key", True),
        }

        # Don't keep the other protocol's credentials around.  This also
        # keeps the has_* flags honest.
        if platform == "newapi":
            fields["refresh_token"] = ""
            if auth_mode == "token":
                fields["login_username"] = ""
                fields["login_password"] = ""
            else:
                fields["access_token"] = ""
                fields["access_user_id"] = ""
        else:
            fields["access_user_id"] = ""
            if auth_mode == "password":
                fields["access_token"] = ""
                fields["refresh_token"] = ""
            else:
                fields["login_username"] = ""
                fields["login_password"] = ""

        if base_url and platform == "newapi":
            if auth_mode == "token" and (
                not fields["access_token"] or not fields["access_user_id"]
            ):
                raise ValidationError("NewAPI 上游匹配需要系统访问令牌和用户 ID")
            if auth_mode == "password" and (
                not fields["login_username"] or not fields["login_password"]
            ):
                raise ValidationError("NewAPI 上游匹配需要用户名和密码")
        if base_url and platform == "sub2api":
            if auth_mode == "password" and (
                not fields["login_username"] or not fields["login_password"]
            ):
                raise ValidationError("sub2api 上游匹配需要用户邮箱和密码")
            if auth_mode == "token" and not fields["access_token"]:
                raise ValidationError("sub2api 上游匹配需要 auth_token")

        saved = self.binding_repo.upsert(
            admin_site_id,
            channel_id,
            fields,
            match_status="unmatched",
            match_message="待匹配",
        )
        return _binding_row_to_payload(saved)

    def match_channel(
        self,
        admin_site_id: int,
        channel_id: int,
        force_refresh: bool = False,
    ) -> dict[str, Any]:
        """Match a single channel's key against its upstream groups.

        Returns ``{"success": True, "data": binding}`` on success or
        ``{"success": False, "data": binding, "message": ...}`` on a
        *business* failure (key missing, no group, upstream unavailable).
        The caller distinguishes the two via ``success``; the persisted
        binding is always returned so the UI can keep showing the last
        good state.
        """
        site = self._must_get_site(admin_site_id)
        self._require_capability(site, "channel_key_match")
        ok, payload, error = self.channel_matcher.match(
            site, int(channel_id), force_refresh=bool(force_refresh)
        )
        if not ok:
            row = self.binding_repo.get(admin_site_id, channel_id)
            # A password-authenticated upstream can return a useful
            # verification payload even though the match itself failed.  Keep
            # it instead of reducing an inherited monitor source to a blank
            # local binding row.
            binding = (
                dict(payload)
                if isinstance(payload, dict) and payload
                else _binding_row_to_payload(row)
            )
            binding["configured"] = True
            binding["inherited_from_monitor"] = not bool(
                row and row.get("upstream_base_url")
            )
            return {"success": False, "data": binding, "message": error}
        return {"success": True, "data": payload}

    def refresh_channel_key(
        self, admin_site_id: int, channel_id: int
    ) -> dict[str, Any]:
        site = self._must_get_site(admin_site_id)
        self._require_capability(site, "key_refresh")
        previous_key = self.key_cache.get(admin_site_id, channel_id)
        client = self._client_for(site)
        ok, new_key, error = client.get_channel_key(
            channel_id, force_refresh=True
        )
        if not ok:
            code = _classify_key_error(error or "")
            raise KeyRefreshError(code, error or "读取渠道真实 key 失败")
        changed = new_key != previous_key
        self.key_cache.upsert(admin_site_id, channel_id, new_key)
        # And re-match while we're here.
        match_result = self.match_channel(
            admin_site_id, channel_id, force_refresh=False
        )
        match_payload = match_result.get("data") or {}
        match_status = str(match_payload.get("match_status") or "")
        match_success = match_result.get("success") and match_status in {
            "matched",
            "matched_partial",
        }
        return {
            "channel_id": int(channel_id),
            "changed": changed,
            "first_fetch": not bool(previous_key),
            "fetched_at": utc_now_iso(),
            "match_success": match_success,
            "match_message": match_payload.get("match_message")
            or (None if match_success else "未匹配到上游分组倍率"),
            "binding": match_payload,
        }

    # ========== discovery ==========

    def list_channel_candidates(
        self, admin_site_id: int, keyword: str = ""
    ) -> dict[str, Any]:
        site = self._must_get_site(admin_site_id)
        if (site.get("platform") or "").strip().lower() != "newapi":
            raise CapabilityError("主站渠道发现仅支持 NewAPI")
        client = self._client_for(site)
        ok, candidates, meta, error = client.channel_candidates(keyword=keyword)
        if not ok:
            raise _upstream_error_for_site(site, meta, error, "读取候选渠道失败")
        return {"success": True, "data": candidates, "meta": meta}

    # ========== groups ==========

    def list_groups(self, admin_site_id: int) -> dict[str, Any]:
        """Return ``{success, data: {group_name: GroupItem, ...}}``.

        Mirrors the legacy ``/api/admin/sites/:id/groups`` contract exactly
        so the frontend can keep its ``Record<string, GroupItem>`` shape.
        """
        site = self._must_get_site(admin_site_id)
        client = self._client_for(site)
        ok, payload, error = client.list_groups()
        if not ok:
            upstream = payload if isinstance(payload, dict) else {}
            raise _upstream_error_for_site(site, upstream, error, "读取分组失败")
        return payload
