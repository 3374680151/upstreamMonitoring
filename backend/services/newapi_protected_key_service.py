"""Protected NewAPI channel-key reads for management sites."""

from __future__ import annotations

import json
import threading
import time
from typing import Any, Optional

from datetime import timedelta

from backend.core.time import app_now, utc_now_iso
from backend.integrations.newapi import browser_session_headers, site_origin
from backend.integrations.transport import newapi_auth_headers
from backend.integrations.transport import normalize_base_url, request_json
from backend.repositories.admin_sites import (
    AdminSiteRepository,
    ChannelKeyCacheRepository,
)
from backend.services.newapi_admin_session_service import (
    NewApiAdminSessionService,
)


KEY_MIN_INTERVAL_SECONDS = 2.0
KEY_RATE_LIMIT_COOLDOWN_SECONDS = 30.0
KEY_CACHE_TTL_SECONDS = 60.0


def _masked(value: Any) -> bool:
    text = str(value or "").strip()
    return not text or "****" in text or text == "-"


def _is_admin_site(site: dict[str, Any]) -> bool:
    """Distinguish an ``admin_sites`` row from an ordinary monitor site.

    Both tables have an ``id`` and a ``base_url``.  Using the id alone would
    make a monitor-site key read touch ``admin_channel_keys`` and try to build
    an admin browser session.  These fields are exclusive to the management
    table and match the historical compatibility boundary.
    """
    # ``browser_session_id`` and ``browser_access_expires_at`` also exist on
    # ordinary monitoring ``sites`` rows.  Use columns exclusive to
    # ``admin_sites`` so a monitor never reads/writes ``admin_channel_keys``
    # or attempts an administrator browser login merely because it has a
    # browser session.
    return any(
        field in site
        for field in (
            "security_proof",
            "browser_access_token",
            "sub2api_access_token",
            "key_sync_enabled",
        )
    )


def _response_code(payload: Any) -> tuple[int, str, str]:
    status = 0
    code = ""
    message = ""
    if isinstance(payload, dict):
        try:
            status = int(payload.get("status") or 0)
        except (TypeError, ValueError):
            status = 0
        code = str(payload.get("code") or "").strip()
        message = str(payload.get("message") or payload.get("error") or "").strip()
        raw = payload.get("raw")
        if isinstance(raw, str) and raw:
            try:
                parsed = json.loads(raw)
            except (TypeError, ValueError):
                parsed = {}
            if isinstance(parsed, dict):
                code = code or str(parsed.get("code") or "").strip()
                message = str(parsed.get("message") or message).strip()
    return status, code, message


class NewApiProtectedKeyService:
    """Read, cache and persist one real NewAPI channel key."""

    _request_lock = threading.RLock()
    _last_request_at: dict[str, float] = {}
    _rate_limit_until: dict[str, float] = {}
    _memory_cache: dict[str, tuple[str, float]] = {}

    def __init__(
        self,
        admins: AdminSiteRepository | None = None,
        keys: ChannelKeyCacheRepository | None = None,
        sessions: NewApiAdminSessionService | None = None,
    ) -> None:
        self.admins = admins or AdminSiteRepository()
        self.keys = keys or ChannelKeyCacheRepository()
        self.sessions = sessions or NewApiAdminSessionService(self.admins)

    @staticmethod
    def _cache_key(site: dict[str, Any], channel_id: int) -> str:
        return "|".join(
            (
                normalize_base_url(str(site.get("base_url") or "")),
                str(site.get("id") or 0),
                str(int(channel_id)),
            )
        )

    @staticmethod
    def _headers(site: dict[str, Any]) -> dict[str, str]:
        headers: dict[str, str] = {}
        token = str(site.get("browser_access_token") or "").strip()
        session_id = str(site.get("browser_session_id") or "").strip()
        refresh_cookie = str(site.get("browser_refresh_cookie") or "").strip()
        if token:
            headers["Authorization"] = f"Bearer {token}"
        if session_id:
            headers["X-Auth-Session"] = session_id
        if refresh_cookie:
            headers["Cookie"] = refresh_cookie
        if not token:
            # Ordinary monitoring rows use the normal NewAPI user token.  A
            # browser-authenticated monitor can still carry the browser
            # session fields even though it has no admin-only token column.
            auth_mode = str(site.get("auth_mode") or "token").strip().lower()
            if auth_mode in {"browser", "password"} and (
                site.get("browser_cookie")
                or site.get("browser_session_id")
                or site.get("browser_refresh_cookie")
            ):
                headers = browser_session_headers(site)
            else:
                headers.update(
                    newapi_auth_headers(
                        str(site.get("access_token") or ""),
                        str(site.get("access_user_id") or ""),
                    )
                )
        proof = str(site.get("security_proof") or "").strip()
        if proof:
            headers["X-Security-Proof"] = proof
        return headers

    def _cached(self, cache_key: str) -> str:
        cached = self._memory_cache.get(cache_key)
        if not cached:
            return ""
        key, stored_at = cached
        if time.monotonic() - stored_at >= KEY_CACHE_TTL_SECONDS:
            self._memory_cache.pop(cache_key, None)
            return ""
        return key

    def _gate(self, gate_key: str) -> Optional[str]:
        now = time.monotonic()
        cooldown_until = self._rate_limit_until.get(gate_key, 0.0)
        if cooldown_until > now:
            wait_seconds = max(1, int(cooldown_until - now))
            return f"主站 key 接口触发限流（HTTP 429），已暂停请求，请等待约 {wait_seconds} 秒后再刷新"
        elapsed = now - self._last_request_at.get(gate_key, 0.0)
        wait_seconds = KEY_MIN_INTERVAL_SECONDS - elapsed
        if wait_seconds > 0:
            time.sleep(wait_seconds)
        self._last_request_at[gate_key] = time.monotonic()
        return None

    @staticmethod
    def _security_error(code: str) -> Optional[str]:
        return {
            "SECURITY_PROOF_REQUIRED": "主站尚未完成 key 读取安全验证",
            "SECURITY_PROOF_INVALID": "主站网页登录 Session 或安全验证 proof 已失效",
            "SECURITY_PROOF_EXPIRED": "主站 key 读取安全验证已过期",
        }.get(code)

    def read(
        self, site: dict[str, Any], channel_id: int, force_refresh: bool = False
    ) -> tuple[bool, str, Optional[str]]:
        try:
            channel_id = int(channel_id)
        except (TypeError, ValueError):
            return False, "", "渠道 ID 无效"
        if channel_id <= 0:
            return False, "", "渠道 ID 无效"
        admin_id = int(site.get("id") or 0)
        admin_site = _is_admin_site(site)
        if admin_site and admin_id <= 0:
            return False, "", "主站记录无效"
        cache_key = self._cache_key(site, channel_id)
        if admin_site and not force_refresh:
            persisted = self.keys.get(admin_id, channel_id)
            if persisted:
                self._memory_cache[cache_key] = (persisted, time.monotonic())
                return True, persisted, None
            cached = self._cached(cache_key)
            if cached:
                return True, cached, None

        if admin_site:
            ready, ready_error = self.sessions.ensure(site)
            if not ready:
                return False, "", ready_error or "主站网页登录态不可用"
        base = normalize_base_url(str(site.get("base_url") or ""))
        if not base or not site_origin(base):
            return False, "", "主站 URL 无法生成有效 Origin，请检查主站地址"
        gate_key = f"{admin_id}|{base}"
        with self._request_lock:
            gate_error = self._gate(gate_key)
            if gate_error:
                return False, "", gate_error
            ok, payload, error = request_json(
                f"{base}/api/channel/{channel_id}/key",
                headers=self._headers(site),
                method="POST",
                admin=True,
            )
            status, code, message = _response_code(payload)
            # NewAPI deployments differ: some return HTTP 401 while others
            # return HTTP 200 with an ``AUTH_TOKEN_EXPIRED`` business code.
            # Both forms must get exactly one session refresh and retry.
            if admin_site and (
                (status == 401 and code == "AUTH_TOKEN_EXPIRED")
                or code == "AUTH_TOKEN_EXPIRED"
            ):
                refreshed, refresh_error = self.sessions.refresh(site, force=True)
                if not refreshed:
                    return False, "", refresh_error or "主站网页登录态刷新失败"
                ok, payload, error = request_json(
                    f"{base}/api/channel/{channel_id}/key",
                    headers=self._headers(site),
                    method="POST",
                    admin=True,
                )
                status, code, message = _response_code(payload)

        if not ok:
            if status == 429:
                with self._request_lock:
                    self._rate_limit_until[gate_key] = (
                        time.monotonic() + KEY_RATE_LIMIT_COOLDOWN_SECONDS
                    )
                return False, "", "主站 key 接口触发限流（HTTP 429），已暂停请求，请等待约 30 秒后再刷新"
            proof_error = self._security_error(code)
            if proof_error:
                if admin_site:
                    self.admins.clear_security_proof(admin_id, utc_now_iso())
                self._memory_cache.pop(cache_key, None)
                return False, "", proof_error
            return False, "", message or error or "读取主站渠道 key 失败"

        if not isinstance(payload, dict) or not payload.get("success"):
            _status, code, message = _response_code(payload)
            proof_error = self._security_error(code)
            if proof_error:
                if admin_site:
                    self.admins.clear_security_proof(admin_id, utc_now_iso())
                self._memory_cache.pop(cache_key, None)
                return False, "", proof_error
            return False, "", message or "主站 key 接口响应异常"
        data = payload.get("data")
        key = data.get("key") if isinstance(data, dict) else ""
        if _masked(key):
            return False, "", "主站 key 接口没有返回明文 key"
        key = str(key).strip()
        self._memory_cache[cache_key] = (key, time.monotonic())
        if admin_site:
            self.keys.upsert(admin_id, channel_id, key)
        return True, key, None

    def verify_access(
        self, site: dict[str, Any], verification_code: str
    ) -> tuple[bool, Optional[str]]:
        """Establish the short-lived proof required by the key endpoint."""
        code = str(verification_code or "").strip()
        if not code:
            return False, "请输入主站 2FA 验证码"
        try:
            admin_id = int(site.get("id") or 0)
        except (TypeError, ValueError):
            admin_id = 0
        if admin_id <= 0:
            return False, "管理站点不存在"
        ready, ready_error = self.sessions.ensure(site, verification_code=code)
        if not ready:
            return False, ready_error or "主站网页登录失败"
        base = normalize_base_url(str(site.get("base_url") or ""))
        if not base or not site_origin(base):
            return False, "主站 URL 无法生成有效 Origin，请检查主站地址"
        ok, payload, error = request_json(
            f"{base}/api/verify",
            headers=self._headers(site),
            payload={"method": "2fa", "code": code, "scope": "channel.key.read"},
            method="POST",
            admin=True,
        )
        status, response_code, message = _response_code(payload)
        if not ok:
            return False, message or error or "主站安全验证失败"
        if not isinstance(payload, dict) or not payload.get("success"):
            return False, message or "主站安全验证失败"
        data = payload.get("data")
        proof = data.get("proof_token") if isinstance(data, dict) else ""
        if _masked(proof):
            return False, "主站安全验证没有返回 proof"
        verified_at = utc_now_iso()
        guard_until = (
            app_now() + timedelta(seconds=60)
        ).isoformat(timespec="seconds")
        self.admins.update_security_proof(
            admin_id,
            str(proof).strip(),
            verified_at,
            next_at=verified_at,
            backoff_until=guard_until,
        )
        site["security_proof"] = str(proof).strip()
        site["security_proof_verified_at"] = verified_at
        # Preserve the established operator flow: a successful proof should
        # immediately read one real channel key while it is still valid.
        # Import lazily to avoid a service-construction cycle.
        from backend.services.key_sync_service import KeySyncService

        refreshed_site = self.admins.get(admin_id) or site
        refresh_result = KeySyncService(
            admins=self.admins, keys=self.keys
        ).refresh_one(refreshed_site)
        if not refresh_result.get("success"):
            refresh_message = str(
                refresh_result.get("message") or "读取渠道 key 失败"
            )
            self.admins.record_key_refresh_error(
                admin_id, refresh_message, utc_now_iso()
            )
            return False, f"2FA 验证已通过，但读取渠道 key 失败：{refresh_message}"

        # The proof write temporarily holds the scheduled worker for one
        # minute.  Once the immediate refresh succeeds, restore the normal
        # success schedule just as a worker-run refresh would do.
        completed_at = utc_now_iso()
        if refreshed_site.get("key_sync_enabled"):
            try:
                interval = max(
                    5,
                    min(
                        1440,
                        int(refreshed_site.get("key_sync_interval_minutes") or 5),
                    ),
                )
            except (TypeError, ValueError):
                interval = 5
            next_at = (
                completed_at
                if int(refresh_result.get("batch_remaining") or 0) > 0
                else (
                    app_now() + timedelta(minutes=interval)
                ).isoformat(timespec="seconds")
            )
        else:
            next_at = refreshed_site.get("key_sync_next_at")
        self.admins.mark_key_sync_success(admin_id, completed_at, next_at)
        return True, None


_default_service = NewApiProtectedKeyService()


def read_newapi_channel_key(
    site: dict[str, Any], channel_id: int, force_refresh: bool = False
) -> tuple[bool, str, Optional[str]]:
    return _default_service.read(site, channel_id, force_refresh=force_refresh)


__all__ = ["NewApiProtectedKeyService", "read_newapi_channel_key"]
