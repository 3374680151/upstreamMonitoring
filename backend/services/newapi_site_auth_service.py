"""NewAPI browser/password session lifecycle for monitoring sites.

The integration module owns the upstream login/refresh protocol.  This
service owns local session state, per-site ordering and persistence so normal
monitoring reads never have to return to ``legacy_runtime.py``.
"""

from __future__ import annotations

import threading
import time
from typing import Any, Optional

from backend.integrations.newapi import (
    _status_from_payload,
    auth_failure_message,
    browser_session_headers,
    fetch_groups_with_headers,
    login_password,
    refresh_browser_session,
)
from backend.integrations.newapi_admin import parse_groups_payload
from backend.integrations.transport import normalize_base_url, request_json
from backend.repositories.sites import SiteRepository


class NewApiSiteAuthService:
    """Execute browser/password-authenticated NewAPI reads for one site."""

    _locks_guard = threading.RLock()
    _locks: dict[int, threading.RLock] = {}

    def __init__(self, sites: SiteRepository | None = None) -> None:
        self.sites = sites or SiteRepository()

    @classmethod
    def _lock(cls, site_id: int) -> threading.RLock:
        with cls._locks_guard:
            return cls._locks.setdefault(int(site_id), threading.RLock())

    @staticmethod
    def _site_id(site: dict[str, Any]) -> int:
        try:
            return int(site.get("id") or 0)
        except (TypeError, ValueError):
            return 0

    def _latest_site(self, site: dict[str, Any]) -> dict[str, Any]:
        original = dict(site or {})
        site_id = self._site_id(original)
        if site_id <= 0:
            return original
        try:
            row = self.sites.get(site_id)
        except Exception:
            return original
        if not row or str(row.get("platform") or "").strip().lower() != "newapi":
            return original
        return row

    @staticmethod
    def _mode(site: dict[str, Any]) -> str:
        return str(site.get("auth_mode") or "browser").strip().lower()

    def _persist(self, site: dict[str, Any], session: dict[str, Any]) -> None:
        site_id = self._site_id(site)
        if site_id <= 0:
            return
        mode = self._mode(site)
        try:
            self.sites.persist_newapi_browser_session(
                site_id,
                session,
                auth_mode=mode if mode in {"browser", "password"} else "browser",
                preserve_login_credentials=mode == "password",
                expected_auth_mode=mode,
                expected_access_token=str(site.get("access_token") or ""),
                expected_refresh_cookie=str(
                    site.get("browser_refresh_cookie") or ""
                ),
                expected_session_id=str(site.get("browser_session_id") or ""),
            )
        except Exception:
            # A valid upstream response remains usable for the current request.
            # The next request will reload the stored session and retry refresh.
            pass

    def _refresh(
        self, site: dict[str, Any], *, force: bool = False
    ) -> tuple[bool, dict[str, Any], Optional[str]]:
        try:
            expires_at = int(site.get("browser_access_expires_at") or 0)
        except (TypeError, ValueError):
            expires_at = 0
        if not force and expires_at > int(time.time()) + 60:
            return True, site, None
        ok, session, error = refresh_browser_session(site)
        if not ok:
            return False, site, error
        updated = dict(site)
        updated.update(session)
        self._persist(site, session)
        return True, updated, None

    def _ensure_session(
        self, site: dict[str, Any]
    ) -> tuple[bool, dict[str, Any], Optional[str]]:
        access_token = str(site.get("access_token") or "").strip()
        access_user_id = str(site.get("access_user_id") or "").strip()
        browser_cookie = str(site.get("browser_cookie") or "").strip()
        if (not access_token and not browser_cookie) or not access_user_id:
            return False, site, "没有登录态，请提前登录"
        if browser_cookie:
            return True, site, None
        session_id = str(site.get("browser_session_id") or "").strip()
        refresh_cookie = str(site.get("browser_refresh_cookie") or "").strip()
        if not session_id and not refresh_cookie:
            return True, site, None
        if not session_id or not refresh_cookie:
            return False, site, "NewAPI 网页登录态不完整，请重新登录"
        try:
            expires_at = int(site.get("browser_access_expires_at") or 0)
        except (TypeError, ValueError):
            expires_at = 0
        if expires_at <= 0 or expires_at > int(time.time()) + 60:
            return True, site, None
        return self._refresh(site)

    def _request_with_session(
        self,
        site: dict[str, Any],
        method: str,
        path: str,
        payload: Any = None,
        query: str = "",
    ) -> tuple[bool, Any, Optional[str]]:
        ready, working, ready_error = self._ensure_session(site)
        if not ready:
            return False, {}, ready_error or "登录态已过期，请重新登录"
        url = f"{normalize_base_url(str(working.get('base_url') or ''))}{path}"
        if query:
            url = f"{url}?{query}" if "?" not in url else f"{url}&{query}"
        ok, response, error = request_json(
            url,
            headers=browser_session_headers(working),
            payload=payload,
            method=method,
        )
        if ok:
            return True, response, None
        if _status_from_payload(response) not in {401, 403}:
            return False, response, auth_failure_message(response, error)

        refreshed, working, refresh_error = self._refresh(working, force=True)
        if not refreshed:
            return False, response, refresh_error or "网页登录态已失效，请重新验证登录"
        ready, working, ready_error = self._ensure_session(working)
        if not ready:
            return False, response, ready_error or "请重新网页登录/同步后再试"
        ok, response, error = request_json(
            url,
            headers=browser_session_headers(working),
            payload=payload,
            method=method,
        )
        if ok:
            return True, response, None
        if _status_from_payload(response) in {401, 403}:
            return False, response, "网页登录态已失效，请重新验证登录"
        return False, response, auth_failure_message(response, error)

    def request(
        self,
        site: dict[str, Any],
        method: str,
        path: str,
        payload: Any = None,
        query: str = "",
    ) -> tuple[bool, Any, Optional[str]]:
        site_id = self._site_id(site)
        if site_id <= 0:
            return self._request_with_session(dict(site or {}), method, path, payload, query)
        with self._lock(site_id):
            return self._request_with_session(
                self._latest_site(site), method, path, payload, query
            )

    def password_login(
        self, site: dict[str, Any], verification_code: str = ""
    ) -> tuple[bool, dict[str, Any], Optional[str]]:
        if str(site.get("platform") or "newapi").strip().lower() != "newapi":
            return False, {}, "只有 NewAPI 渠道支持用户名密码登录"
        if self._mode(site) != "password":
            return False, {}, "请先将认证方式切换为用户名密码"
        site_id = self._site_id(site)
        if site_id <= 0:
            return False, {}, "渠道记录无效"
        with self._lock(site_id):
            current = self._latest_site(site)
            ok, session, error = login_password(
                str(current.get("base_url") or ""),
                str(current.get("login_username") or "").strip(),
                str(current.get("login_password") or ""),
                verification_code=str(verification_code or "").strip(),
                access_user_id=str(current.get("access_user_id") or ""),
                previous_refresh_cookie=str(
                    current.get("browser_refresh_cookie") or ""
                ),
            )
            if not ok:
                return False, session, error
            self._persist(current, session)
            working = {**current, **session}
            groups_ok, groups_payload, groups_error = fetch_groups_with_headers(
                str(working.get("base_url") or ""),
                browser_session_headers(working),
            )
            groups = parse_groups_payload(groups_payload) if groups_ok else {}
            return True, {
                "groups_count": len(groups),
                "warning": None
                if groups_ok
                else auth_failure_message(groups_payload, groups_error),
            }, None

    def probe_password_login(
        self,
        base_url: str,
        username: str,
        password: str,
        verification_code: str = "",
    ) -> tuple[bool, dict[str, Any], Optional[str]]:
        ok, session, error = login_password(
            base_url,
            username,
            password,
            verification_code=str(verification_code or "").strip(),
        )
        if not ok:
            return False, session, error
        groups_ok, groups_payload, groups_error = fetch_groups_with_headers(
            base_url, browser_session_headers(session)
        )
        groups = parse_groups_payload(groups_payload) if groups_ok else {}
        warning = (
            None
            if groups_ok
            else auth_failure_message(groups_payload, groups_error)
        )
        return groups_ok, {"groups_count": len(groups), "warning": warning}, warning


__all__ = ["NewApiSiteAuthService"]
