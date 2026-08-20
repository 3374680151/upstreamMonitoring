"""Persistence-aware sub2api administrator session management."""

from __future__ import annotations

import threading
import time
from typing import Any, Optional

from backend.core.time import utc_now_iso
from backend.integrations.sub2api_admin import admin_login, admin_refresh_token
from backend.repositories.admin_sites import AdminSiteRepository


ADMIN_SESSION_EXPIRY_SKEW_SECONDS = 60


class Sub2ApiAdminSessionService:
    """Coordinate refresh-token rotation and credential fallback atomically."""

    _locks: dict[int, threading.RLock] = {}
    _locks_guard = threading.RLock()

    def __init__(self, repository: AdminSiteRepository | None = None) -> None:
        self.repository = repository or AdminSiteRepository()

    @classmethod
    def _lock_for(cls, site_id: int) -> threading.RLock:
        with cls._locks_guard:
            return cls._locks.setdefault(int(site_id), threading.RLock())

    @staticmethod
    def _expires_at(data: dict[str, Any], fallback: int = 0) -> int:
        raw_absolute = data.get("access_expires_at")
        try:
            if raw_absolute is not None and int(raw_absolute) > 0:
                return int(raw_absolute)
        except (TypeError, ValueError):
            pass
        try:
            expires_in = max(0, int(data.get("expires_in") or 0))
        except (TypeError, ValueError):
            expires_in = 0
        return int(time.time()) + expires_in if expires_in else int(fallback or 0)

    def ensure_session(
        self,
        site: dict[str, Any],
        force_refresh: bool = False,
        rejected_access_token: str = "",
    ) -> tuple[bool, str, Optional[str]]:
        try:
            site_id = int(site.get("id") or 0)
        except (TypeError, ValueError):
            site_id = 0
        if site_id <= 0:
            return False, "", "sub2api 主站记录无效"

        with self._lock_for(site_id):
            current = self.repository.get(site_id) or dict(site)
            access_token = str(current.get("sub2api_access_token") or "").strip()
            refresh_token = str(current.get("sub2api_refresh_token") or "").strip()
            try:
                expires_at = int(current.get("sub2api_access_expires_at") or 0)
            except (TypeError, ValueError):
                expires_at = 0
            rejected = str(rejected_access_token or "").strip()

            # Another request may have rotated the token while this request
            # was waiting for the per-site lock. Reuse that newer value.
            if force_refresh and access_token and rejected and access_token != rejected:
                return True, access_token, None
            if (
                access_token
                and not force_refresh
                and expires_at > int(time.time()) + ADMIN_SESSION_EXPIRY_SKEW_SECONDS
            ):
                return True, access_token, None

            refresh_error: Optional[str] = None
            if refresh_token:
                refreshed, data, refresh_error = admin_refresh_token(
                    str(current.get("base_url") or ""), refresh_token
                )
                if refreshed and isinstance(data, dict):
                    next_access = str(data.get("access_token") or "").strip()
                    next_refresh = str(data.get("refresh_token") or refresh_token).strip()
                    if next_access:
                        auth = {
                            "access_token": next_access,
                            "refresh_token": next_refresh,
                            "access_expires_at": self._expires_at(data, expires_at),
                        }
                        self.repository.persist_sub2api_auth(
                            site_id, auth, utc_now_iso()
                        )
                        return True, next_access, None

            logged_in, auth, login_error = admin_login(
                str(current.get("base_url") or ""),
                str(current.get("login_username") or ""),
                str(current.get("login_password") or ""),
            )
            if logged_in and isinstance(auth, dict):
                access = str(auth.get("access_token") or "").strip()
                refresh = str(auth.get("refresh_token") or "").strip()
                if access and refresh:
                    persisted = {
                        "access_token": access,
                        "refresh_token": refresh,
                        "access_expires_at": self._expires_at(auth),
                    }
                    self.repository.persist_sub2api_auth(
                        site_id, persisted, utc_now_iso()
                    )
                    return True, access, None

            message = login_error or refresh_error or "sub2api 主站登录失败"
            self.repository.persist_sub2api_error(site_id, message, utc_now_iso())
            return False, "", message


_default_service = Sub2ApiAdminSessionService()


def ensure_sub2api_admin_session(
    site: dict[str, Any],
    force_refresh: bool = False,
    rejected_access_token: str = "",
) -> tuple[bool, str, Optional[str]]:
    """Adapter-shaped entry point for ``Sub2ApiAdminProtocol``."""
    return _default_service.ensure_session(
        site,
        force_refresh=force_refresh,
        rejected_access_token=rejected_access_token,
    )


__all__ = ["Sub2ApiAdminSessionService", "ensure_sub2api_admin_session"]
