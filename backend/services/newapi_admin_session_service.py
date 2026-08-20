"""Persistence-aware NewAPI administrator browser-session lifecycle."""

from __future__ import annotations

import threading
import time
from typing import Any, Optional

from backend.core.time import utc_now_iso
from backend.integrations.newapi import login_password, refresh_browser_session
from backend.repositories.admin_sites import AdminSiteRepository


ADMIN_SESSION_EXPIRY_SKEW_SECONDS = 60


class NewApiAdminSessionService:
    """Refresh or establish the session required by protected admin APIs."""

    _locks: dict[int, threading.RLock] = {}
    _locks_guard = threading.RLock()

    def __init__(self, repository: AdminSiteRepository | None = None) -> None:
        self.repository = repository or AdminSiteRepository()

    @classmethod
    def _lock_for(cls, site_id: int) -> threading.RLock:
        with cls._locks_guard:
            return cls._locks.setdefault(int(site_id), threading.RLock())

    @staticmethod
    def _apply(site: dict[str, Any], session: dict[str, Any]) -> None:
        site.update(
            {
                "browser_access_token": str(session.get("access_token") or "").strip(),
                "browser_refresh_cookie": str(
                    session.get("browser_refresh_cookie")
                    or session.get("refresh_cookie")
                    or ""
                ).strip(),
                "browser_session_id": str(
                    session.get("browser_session_id")
                    or session.get("session_id")
                    or ""
                ).strip(),
                "browser_access_expires_at": int(
                    session.get("browser_access_expires_at") or 0
                ),
                "browser_login_last_error": None,
                "browser_login_last_check_at": utc_now_iso(),
            }
        )

    @staticmethod
    def _cursor(site: dict[str, Any]) -> dict[str, str]:
        """Return the browser fields used as the optimistic CAS cursor."""
        return {
            "expected_access_token": str(
                site.get("browser_access_token") or ""
            ).strip(),
            "expected_refresh_cookie": str(
                site.get("browser_refresh_cookie") or ""
            ).strip(),
            "expected_session_id": str(
                site.get("browser_session_id") or ""
            ).strip(),
        }

    @staticmethod
    def _apply_latest(site: dict[str, Any], latest: dict[str, Any]) -> None:
        for key in (
            "browser_access_token",
            "browser_refresh_cookie",
            "browser_session_id",
            "browser_access_expires_at",
            "browser_login_last_error",
            "browser_login_last_check_at",
        ):
            if key in latest:
                site[key] = latest.get(key)

    def _persist(
        self,
        site: dict[str, Any],
        session: dict[str, Any],
        *,
        cursor: dict[str, str] | None = None,
    ) -> bool:
        site_id = int(site.get("id") or 0)
        now = utc_now_iso()
        expected = cursor or self._cursor(site)
        changed = self.repository.persist_newapi_browser_session(
            site_id,
            session,
            now,
            **expected,
        )
        # MySQL can report rowcount=0 when the refreshed bundle is identical;
        # that is still a successful persistence as long as the row remains.
        if changed:
            self._apply(site, session)
            return True
        # A concurrent process may have won the CAS with a newer bundle.  Use
        # that bundle instead of overwriting it or reporting a spurious login
        # failure to the caller.
        latest = self.repository.get(site_id)
        if latest:
            latest_cursor = self._cursor(latest)
            session_cursor = {
                "expected_access_token": str(session.get("access_token") or "").strip(),
                "expected_refresh_cookie": str(
                    session.get("browser_refresh_cookie")
                    or session.get("refresh_cookie")
                    or ""
                ).strip(),
                "expected_session_id": str(
                    session.get("browser_session_id")
                    or session.get("session_id")
                    or ""
                ).strip(),
            }
            if latest_cursor == session_cursor:
                self._apply(site, session)
                return True
            if latest_cursor != (cursor or self._cursor(site)):
                self._apply_latest(site, latest)
                return bool(
                    str(latest.get("browser_access_token") or "").strip()
                    and str(latest.get("browser_session_id") or "").strip()
                )
        return False

    def _fail(
        self,
        site: dict[str, Any],
        message: str,
        *,
        cursor: dict[str, str] | None = None,
    ) -> tuple[bool, Optional[str]]:
        site_id = int(site.get("id") or 0)
        now = utc_now_iso()
        self.repository.persist_newapi_browser_error(
            site_id,
            message,
            now,
            **(cursor or self._cursor(site)),
        )
        site["browser_login_last_error"] = str(message)
        site["browser_login_last_check_at"] = now
        return False, str(message)

    def refresh(
        self, site: dict[str, Any], force: bool = False
    ) -> tuple[bool, Optional[str]]:
        try:
            site_id = int(site.get("id") or 0)
        except (TypeError, ValueError):
            site_id = 0
        if site_id <= 0:
            return False, "主站记录无效，无法刷新网页登录态"

        with self._lock_for(site_id):
            previous_access = str(site.get("browser_access_token") or "").strip()
            latest = self.repository.get(site_id)
            if latest:
                self._apply_latest(site, latest)
            cursor = self._cursor(site)
            current_access = str(site.get("browser_access_token") or "").strip()
            try:
                expires_at = int(site.get("browser_access_expires_at") or 0)
            except (TypeError, ValueError):
                expires_at = 0
            if (
                latest
                and previous_access
                and current_access
                and current_access != previous_access
            ):
                return True, None
            if not force and expires_at > int(time.time()) + ADMIN_SESSION_EXPIRY_SKEW_SECONDS:
                return True, None

            refresh_cookie = str(site.get("browser_refresh_cookie") or "").strip()
            session_id = str(site.get("browser_session_id") or "").strip()
            if not refresh_cookie or not session_id:
                return self._fail(
                    site,
                    "主站网页登录态缺少 Refresh Cookie 或 Session ID",
                    cursor=cursor,
                )
            ok, session, error = refresh_browser_session(site)
            if not ok or not isinstance(session, dict):
                return self._fail(
                    site,
                    error or "主站网页登录态刷新失败",
                    cursor=cursor,
                )
            if not self._persist(site, session, cursor=cursor):
                return self._fail(
                    site,
                    "主站网页登录态保存失败，请重新同步",
                    cursor=cursor,
                )
            return True, None

    def ensure(
        self, site: dict[str, Any], verification_code: str = ""
    ) -> tuple[bool, Optional[str]]:
        try:
            site_id = int(site.get("id") or 0)
        except (TypeError, ValueError):
            site_id = 0
        if site_id <= 0:
            return False, "主站记录无效，无法建立网页登录态"

        with self._lock_for(site_id):
            latest = self.repository.get(site_id)
            if latest:
                # Keep the PAT and login credentials from the caller, but
                # refresh all session fields from the latest CAS row.
                self._apply_latest(site, latest)
            cursor = self._cursor(site)
            now = int(time.time())
            access_token = str(site.get("browser_access_token") or "").strip()
            session_id = str(site.get("browser_session_id") or "").strip()
            refresh_cookie = str(site.get("browser_refresh_cookie") or "").strip()
            try:
                expires_at = int(site.get("browser_access_expires_at") or 0)
            except (TypeError, ValueError):
                expires_at = 0
            has_session = bool(access_token and session_id)
            if has_session and (expires_at <= 0 or expires_at > now + 60):
                return True, None

            if has_session and refresh_cookie:
                refreshed, refresh_error = self.refresh(site, force=False)
                if refreshed:
                    return True, None
                try:
                    expires_at = int(site.get("browser_access_expires_at") or 0)
                except (TypeError, ValueError):
                    expires_at = 0
            else:
                refresh_error = None

            if has_session and expires_at > now:
                return True, None
            if has_session and expires_at > 0 and expires_at <= now + 60 and not str(
                verification_code or ""
            ).strip():
                return self._fail(
                    site,
                    refresh_error
                    or "主站网页登录 Session 已过期，请重新完成主站网页登录和 2FA 安全验证",
                    cursor=cursor,
                )

            username = str(site.get("login_username") or "").strip()
            password = str(site.get("login_password") or "")
            if not username or not password:
                return self._fail(
                    site,
                    "主站未配置网页登录账号和密码，无法完成 2FA 安全验证",
                    cursor=cursor,
                )
            ok, session, error = login_password(
                str(site.get("base_url") or ""),
                username,
                password,
                verification_code=str(verification_code or "").strip(),
                access_user_id=str(site.get("access_user_id") or ""),
                previous_refresh_cookie=refresh_cookie,
            )
            if not ok or not isinstance(session, dict):
                return self._fail(site, error or "主站网页登录失败", cursor=cursor)
            if not self._persist(site, session, cursor=cursor):
                return self._fail(
                    site,
                    "主站网页登录态保存失败，请重新同步",
                    cursor=cursor,
                )
            return True, None


_default_service = NewApiAdminSessionService()


def ensure_newapi_admin_session(
    site: dict[str, Any], verification_code: str = ""
) -> tuple[bool, Optional[str]]:
    return _default_service.ensure(site, verification_code)


def refresh_newapi_admin_session(
    site: dict[str, Any], force: bool = False
) -> tuple[bool, Optional[str]]:
    return _default_service.refresh(site, force=force)


__all__ = [
    "NewApiAdminSessionService",
    "ensure_newapi_admin_session",
    "refresh_newapi_admin_session",
]
