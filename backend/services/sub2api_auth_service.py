"""Credential lifecycle for ordinary sub2api monitoring sites.

The protocol client performs only upstream HTTP calls.  This service owns the
ordered fallback from an access token to refresh token and browser/password
login, plus the local compare-and-swap write for rotated credentials.
"""

from __future__ import annotations

import threading
import time
from typing import Any, Callable, Optional

from backend.integrations.sub2api import (
    Sub2ApiClient,
    browser_session_required,
    classify_auth_failure,
)
from backend.integrations.transport import normalize_base_url
from backend.repositories.sites import SiteRepository


FetchByToken = Callable[[str, str], tuple[bool, dict[str, Any], Optional[str]]]
REFRESH_CACHE_TTL_SECONDS = 30.0


class Sub2ApiSiteAuthService:
    """Run a monitoring read with the site's current sub2api credentials."""

    _refresh_locks_guard = threading.RLock()
    _refresh_locks: dict[str, threading.RLock] = {}
    _refresh_cache: dict[str, dict[str, Any]] = {}
    _site_locks_guard = threading.RLock()
    _site_locks: dict[int, threading.RLock] = {}

    def __init__(
        self,
        sites: SiteRepository | None = None,
        client: Sub2ApiClient | None = None,
    ) -> None:
        self.sites = sites or SiteRepository()
        self.client = client or Sub2ApiClient()

    def fetch_groups(
        self, site: dict[str, Any]
    ) -> tuple[bool, dict[str, Any], str | None]:
        return self._with_current_site(site, self.client.fetch_groups_by_token)

    def fetch_models(
        self, site: dict[str, Any]
    ) -> tuple[bool, dict[str, Any], str | None]:
        return self._with_current_site(site, self.client.fetch_models_by_token)

    def fetch_account(
        self, site: dict[str, Any]
    ) -> tuple[bool, dict[str, Any], str | None]:
        return self._with_current_site(site, self.client.fetch_account_by_token)

    @classmethod
    def _site_lock(cls, site_id: int) -> threading.RLock:
        with cls._site_locks_guard:
            return cls._site_locks.setdefault(int(site_id), threading.RLock())

    @classmethod
    def _refresh_lock(cls, base_url: str) -> threading.RLock:
        key = normalize_base_url(base_url)
        with cls._refresh_locks_guard:
            return cls._refresh_locks.setdefault(key, threading.RLock())

    @staticmethod
    def _site_id(site: dict[str, Any]) -> int:
        try:
            return int(site.get("id") or 0)
        except (TypeError, ValueError):
            return 0

    def _with_current_site(
        self, site: dict[str, Any], fetch: FetchByToken
    ) -> tuple[bool, dict[str, Any], str | None]:
        original = dict(site or {})
        site_id = self._site_id(original)
        if site_id <= 0:
            return self._fetch(original, fetch)
        with self._site_lock(site_id):
            current = self._latest_site(original)
            return self._fetch(current, fetch)

    def _latest_site(self, original: dict[str, Any]) -> dict[str, Any]:
        """Re-read credentials after obtaining the per-site lock.

        A browser completion or manual edit may have won while a scheduled
        check was queued.  Database trouble should not discard the immutable
        request snapshot, matching the pre-migration behavior.
        """
        site_id = self._site_id(original)
        try:
            row = self.sites.get(site_id)
        except Exception:
            return original
        if not row or str(row.get("platform") or "").strip().lower() != "sub2api":
            return original
        return row

    def _refresh_once(
        self, base_url: str, refresh_token: str
    ) -> tuple[bool, dict[str, Any], str | None]:
        token = str(refresh_token or "").strip()
        if not token:
            return False, {}, "refresh_token 为空"
        key = f"{normalize_base_url(base_url)}|{token}"
        with self._refresh_lock(base_url):
            cached = self._refresh_cache.get(key)
            if (
                cached
                and time.monotonic() - float(cached.get("created_monotonic") or 0)
                < REFRESH_CACHE_TTL_SECONDS
            ):
                data = cached.get("data")
                if isinstance(data, dict):
                    return True, dict(data), None
            ok, data, error = self.client.refresh(base_url, token)
            if not ok:
                return False, data, error
            self._refresh_cache[key] = {
                "data": dict(data),
                "created_monotonic": time.monotonic(),
            }
            return True, data, None

    def _persist(
        self,
        site: dict[str, Any],
        auth: dict[str, Any],
        expected_access_token: str,
        expected_refresh_token: str,
        *,
        restore_browser_session: bool,
    ) -> None:
        site_id = self._site_id(site)
        if site_id <= 0:
            return
        try:
            self.sites.persist_sub2api_refreshed_auth(
                site_id,
                auth,
                expected_access_token=expected_access_token,
                expected_refresh_token=expected_refresh_token,
                restore_browser_session=restore_browser_session,
            )
        except Exception:
            # An upstream request result is still valid for this response; a
            # future check will retry persistence from the stored credentials.
            pass

    @staticmethod
    def _rotated_auth(data: dict[str, Any], fallback_refresh_token: str) -> dict[str, Any]:
        return {
            "access_token": str(data.get("access_token") or "").strip(),
            "refresh_token": str(
                data.get("refresh_token") or fallback_refresh_token or ""
            ).strip(),
            "expires_in": data.get("expires_in"),
        }

    def _fetch(
        self, site: dict[str, Any], fetch: FetchByToken
    ) -> tuple[bool, dict[str, Any], str | None]:
        base_url = str(site.get("base_url") or "")
        mode = str(site.get("auth_mode") or "password").strip().lower()
        username = str(site.get("login_username") or "")
        password = str(site.get("login_password") or "")

        if mode == "password":
            ok, auth, error = self.client.login(base_url, username, password)
            if not ok:
                return False, {"login": auth}, error or "登录失败"
            return fetch(base_url, str(auth.get("access_token") or ""))
        if mode not in {"token", "browser"}:
            return False, {}, "auth_mode invalid"

        current_access = str(site.get("access_token") or "").strip()
        current_refresh = str(site.get("refresh_token") or "").strip()
        if current_access:
            ok, payload, error = fetch(base_url, current_access)
            if ok:
                return True, payload, None
            if classify_auth_failure(payload, error) != "auth":
                return False, payload, error
        elif mode == "token" and not current_refresh:
            return False, {}, "auth_token 为空"

        if current_refresh:
            refreshed, data, refresh_error = self._refresh_once(
                base_url, current_refresh
            )
            if refreshed:
                rotated_auth = self._rotated_auth(data, current_refresh)
                rotated_access = rotated_auth["access_token"]
                if not rotated_access:
                    return False, {"refresh": data}, "刷新成功但没有返回 access_token"
                ok, payload, error = fetch(base_url, rotated_access)
                if ok:
                    self._persist(
                        site,
                        rotated_auth,
                        current_access,
                        current_refresh,
                        restore_browser_session=mode == "browser",
                    )
                    return True, payload, None
                self._persist(
                    site,
                    rotated_auth,
                    current_access,
                    current_refresh,
                    restore_browser_session=False,
                )
                if classify_auth_failure(payload, error) != "auth":
                    return False, payload, error
                current_access = rotated_auth["access_token"]
                current_refresh = rotated_auth["refresh_token"]
            elif mode == "token" or classify_auth_failure(data, refresh_error) != "auth":
                return False, {"refresh": data}, refresh_error or "登录态刷新失败"

        if mode == "token":
            return False, {}, "登录态已过期"
        if not username.strip() or not password:
            return browser_session_required()

        logged_in, data, login_error = self.client.login(base_url, username, password)
        if not logged_in:
            if classify_auth_failure(data, login_error) == "interactive":
                return browser_session_required()
            return False, {"login": data}, login_error or "登录失败"
        login_auth = self._rotated_auth(data, current_refresh)
        login_access = login_auth["access_token"]
        if not login_access:
            return False, {"login": data}, "登录成功但没有返回 access_token"
        ok, payload, error = fetch(base_url, login_access)
        self._persist(
            site,
            login_auth,
            current_access,
            current_refresh,
            restore_browser_session=bool(ok),
        )
        if ok:
            return True, payload, None
        if classify_auth_failure(payload, error) == "interactive":
            return browser_session_required()
        return False, payload, error


__all__ = ["REFRESH_CACHE_TTL_SECONDS", "Sub2ApiSiteAuthService"]
