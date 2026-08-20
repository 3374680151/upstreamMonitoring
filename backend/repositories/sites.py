"""Persistence operations for monitoring sites.

The repository owns the ``sites`` table contract.  Services pass already
validated values here; no HTTP or upstream behavior belongs in this module.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from backend.core.time import app_now, utc_now_iso
from backend.db import execute, execute_rowcount, query_all, query_one


SITE_COLUMNS = frozenset(
    {
        "name",
        "base_url",
        "platform",
        "enabled",
        "interval_minutes",
        "login_enabled",
        "auth_mode",
        "login_username",
        "login_password",
        "access_token",
        "access_user_id",
        "refresh_token",
        "token_expires_at",
        "status",
        "last_error",
        "last_check_at",
        "next_check_at",
        "consecutive_failures",
        "auto_disabled",
        "current_groups_json",
        "current_login_groups_json",
        "login_last_error",
        "login_last_check_at",
        "session_sync_status",
        "session_sync_error",
        "session_synced_at",
        "browser_refresh_cookie",
        "browser_cookie",
        "browser_session_id",
        "browser_access_expires_at",
        "created_at",
        "updated_at",
    }
)


class SiteRepository:
    def get(self, site_id: int) -> dict[str, Any] | None:
        return query_one("SELECT * FROM sites WHERE id = ?", (site_id,))

    def list(self) -> list[dict[str, Any]]:
        return query_all("SELECT * FROM sites ORDER BY id DESC")

    def list_enabled(self) -> list[dict[str, Any]]:
        """Return enabled monitoring sites in the legacy matching order."""
        return query_all("SELECT * FROM sites WHERE enabled = 1 ORDER BY id DESC")

    def find_by_base_url(self, base_url: str) -> dict[str, Any] | None:
        return query_one(
            "SELECT * FROM sites WHERE base_url = ? LIMIT 1", (base_url,)
        )

    def create(self, values: dict[str, Any]) -> int:
        """Insert one site and return its generated id.

        The column allow-list prevents request fields from becoming SQL
        identifiers.  Defaults that are part of the existing schema are kept
        explicit so this remains compatible with old MySQL installations.
        """
        now = str(values.get("created_at") or utc_now_iso())
        row = {
            "name": str(values.get("name") or ""),
            "base_url": str(values.get("base_url") or ""),
            "platform": str(values.get("platform") or "newapi"),
            "enabled": 1 if values.get("enabled", True) else 0,
            "interval_minutes": int(values.get("interval_minutes") or 3),
            "login_enabled": 1 if values.get("login_enabled") else 0,
            "auth_mode": str(values.get("auth_mode") or "password"),
            "login_username": values.get("login_username") or "",
            "login_password": values.get("login_password") or "",
            "access_token": values.get("access_token") or "",
            "access_user_id": values.get("access_user_id") or "",
            "refresh_token": values.get("refresh_token") or "",
            "token_expires_at": values.get("token_expires_at") or "",
            "status": values.get("status") or "unknown",
            "last_error": values.get("last_error"),
            "last_check_at": values.get("last_check_at"),
            "next_check_at": values.get("next_check_at"),
            "consecutive_failures": int(values.get("consecutive_failures") or 0),
            "current_groups_json": values.get("current_groups_json"),
            "created_at": now,
            "updated_at": str(values.get("updated_at") or now),
        }
        columns = list(row)
        placeholders = ", ".join("?" for _ in columns)
        return execute(
            f"INSERT INTO sites ({', '.join(columns)}) VALUES ({placeholders})",
            tuple(row[column] for column in columns),
        )

    def update_fields(
        self, site_id: int, values: dict[str, Any], *, connection: Any = None
    ) -> int:
        updates = {
            key: value
            for key, value in values.items()
            if key in SITE_COLUMNS and key not in {"created_at"}
        }
        if not updates:
            return 0
        assignments = ", ".join(f"{key} = ?" for key in updates)
        return execute_rowcount(
            f"UPDATE sites SET {assignments} WHERE id = ?",
            tuple(updates.values()) + (int(site_id),),
            connection=connection,
        )

    def persist_sub2api_refreshed_auth(
        self,
        site_id: int,
        auth: dict[str, Any],
        *,
        expected_access_token: str | None = None,
        expected_refresh_token: str | None = None,
        restore_browser_session: bool = False,
        now: str | None = None,
    ) -> int:
        """Persist rotated sub2api credentials with an optimistic CAS guard.

        A refresh token may rotate on every request.  The expected credential
        values prevent an older in-flight request from replacing a newer
        browser sync or manual credential edit.
        """
        if not isinstance(auth, dict):
            return 0
        try:
            expires_in = auth.get("expires_in")
            expires_at = (
                app_now() + timedelta(seconds=int(expires_in))
                if expires_in is not None
                else None
            )
            expires_at_value = (
                expires_at.isoformat(timespec="seconds") if expires_at else None
            )
        except (TypeError, ValueError, OverflowError):
            expires_at_value = None

        assignments = [
            "access_token = COALESCE(NULLIF(?, ''), access_token)",
            "refresh_token = COALESCE(NULLIF(?, ''), refresh_token)",
            "token_expires_at = COALESCE(?, token_expires_at)",
        ]
        params: list[Any] = [
            str(auth.get("access_token") or "").strip(),
            str(auth.get("refresh_token") or "").strip(),
            expires_at_value,
        ]
        if restore_browser_session:
            assignments.extend(
                [
                    "session_sync_status = 'ready'",
                    "session_sync_error = NULL",
                ]
            )
        assignments.append("updated_at = ?")
        params.append(str(now or utc_now_iso()))

        where = ["id = ?", "platform = 'sub2api'"]
        where_params: list[Any] = [int(site_id)]
        if restore_browser_session:
            where.append("auth_mode = 'browser'")
        if expected_access_token is not None:
            where.append("COALESCE(access_token, '') = ?")
            where_params.append(str(expected_access_token or "").strip())
        if expected_refresh_token is not None:
            where.append("COALESCE(refresh_token, '') = ?")
            where_params.append(str(expected_refresh_token or "").strip())
        params.extend(where_params)
        return execute_rowcount(
            f"UPDATE sites SET {', '.join(assignments)} WHERE {' AND '.join(where)}",
            tuple(params),
        )

    def persist_newapi_browser_session(
        self,
        site_id: int,
        session: dict[str, Any],
        *,
        auth_mode: str = "browser",
        preserve_login_credentials: bool = False,
        expected_auth_mode: str | None = None,
        expected_access_token: str | None = None,
        expected_refresh_cookie: str | None = None,
        expected_session_id: str | None = None,
        now: str | None = None,
    ) -> int:
        """Persist a locally authenticated NewAPI browser/password session."""
        if not isinstance(session, dict):
            return 0
        try:
            expires_at = int(session.get("browser_access_expires_at") or 0)
        except (TypeError, ValueError):
            expires_at = 0
        updates: dict[str, Any] = {
            "auth_mode": str(auth_mode or "browser"),
            "login_enabled": 1,
            "access_token": str(session.get("access_token") or "").strip(),
            "access_user_id": str(session.get("access_user_id") or "").strip(),
            "browser_cookie": str(session.get("browser_cookie") or "").strip()
            or None,
            "browser_refresh_cookie": str(
                session.get("browser_refresh_cookie") or ""
            ).strip()
            or None,
            "browser_session_id": str(session.get("browser_session_id") or "").strip()
            or None,
            "browser_access_expires_at": expires_at,
            "session_sync_status": "ready",
            "session_sync_error": None,
            "session_synced_at": str(now or utc_now_iso()),
            "updated_at": str(now or utc_now_iso()),
        }
        if not preserve_login_credentials:
            updates["login_username"] = None
            updates["login_password"] = None
        assignments = ", ".join(f"{key} = ?" for key in updates)
        where = ["id = ?", "platform = 'newapi'"]
        params: list[Any] = list(updates.values()) + [int(site_id)]
        if expected_auth_mode is not None:
            where.append("COALESCE(auth_mode, '') = ?")
            params.append(str(expected_auth_mode or "").strip())
        if expected_access_token is not None:
            where.append("COALESCE(access_token, '') = ?")
            params.append(str(expected_access_token or "").strip())
        if expected_refresh_cookie is not None:
            where.append("COALESCE(browser_refresh_cookie, '') = ?")
            params.append(str(expected_refresh_cookie or "").strip())
        if expected_session_id is not None:
            where.append("COALESCE(browser_session_id, '') = ?")
            params.append(str(expected_session_id or "").strip())
        return execute_rowcount(
            f"UPDATE sites SET {assignments} WHERE {' AND '.join(where)}",
            tuple(params),
        )

    def delete(self, site_id: int) -> int:
        return execute_rowcount("DELETE FROM sites WHERE id = ?", (site_id,))
