"""Read models for the monitoring dashboard.

This repository owns the SQL and shaping needed by ``/api/overview`` and
``/api/sites``.  It deliberately does not perform upstream requests: those
belong to the monitoring/check service.  Keeping this read model here lets
the FastAPI service serve dashboard reads without importing the compatibility
runtime.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from backend.db import connection, query_all, query_one


def _application_now() -> datetime:
    timezone_name = os.getenv("APP_TIMEZONE") or os.getenv("TZ") or "Asia/Shanghai"
    try:
        current_timezone = ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError:
        current_timezone = timezone(timedelta(hours=8), "Asia/Shanghai")
    return datetime.now(current_timezone)


def _decode_groups(value: Any) -> dict[str, Any]:
    if not value:
        return {}
    try:
        groups = json.loads(value) if isinstance(value, str) else value
    except (TypeError, ValueError):
        return {}
    return groups if isinstance(groups, dict) else {}


class MonitoringRepository:
    """All database reads used by the monitoring dashboard."""

    def _summary(
        self,
        site: dict[str, Any],
        *,
        connection: Optional[Any] = None,
    ) -> dict[str, Any]:
        groups = _decode_groups(site.get("current_groups_json"))
        login_groups = _decode_groups(site.get("current_login_groups_json"))
        latest_snapshot = query_one(
            """
            SELECT checked_at, status, error_message
            FROM snapshots
            WHERE site_id = ?
            ORDER BY id DESC
            LIMIT 1
            """,
            (site["id"],),
            connection=connection,
        )
        latest_change = query_one(
            "SELECT * FROM changes WHERE site_id = ? ORDER BY id DESC LIMIT 1",
            (site["id"],),
            connection=connection,
        )
        platform = str(site.get("platform") or "newapi").strip().lower()
        auth_mode = str(
            site.get("auth_mode")
            or ("token" if platform == "newapi" else "password")
        ).strip().lower()
        # ``session_sync_*`` are redacted state metadata (never credentials),
        # so expose the persisted value for both platforms.  Discovery-created
        # NewAPI rows rely on this to show browser-sync progress after the
        # import; older code incorrectly forced NewAPI rows to ``token`` and
        # hid their session state.
        session_sync_status = str(
            site.get("session_sync_status") or "not_requested"
        ).strip().lower()
        has_browser_session = bool(
            auth_mode == "browser"
            and (
                # NewAPI browser sessions can be represented by a cookie,
                # refresh cookie/session id, or a validated access token pair.
                site.get("browser_cookie")
                or site.get("browser_refresh_cookie")
                or site.get("browser_session_id")
                or (
                    site.get("access_token")
                    and (site.get("access_user_id") or site.get("refresh_token"))
                )
            )
        )
        return {
            "id": site["id"],
            "name": site["name"],
            "base_url": site["base_url"],
            "platform": site["platform"],
            "platform_label": "sub2api" if platform == "sub2api" else "NewAPI",
            "enabled": bool(site["enabled"]),
            "interval_minutes": site["interval_minutes"],
            "login_enabled": bool(site.get("login_enabled")),
            "auth_mode": auth_mode,
            "login_username": site.get("login_username") or "",
            "has_login_password": bool(site.get("login_password")),
            "has_access_token": bool(site.get("access_token")),
            "has_refresh_token": bool(site.get("refresh_token")),
            "has_browser_session": has_browser_session,
            "token_expires_at": site.get("token_expires_at") or "",
            "access_user_id": site.get("access_user_id") or "",
            "login_last_error": site.get("login_last_error"),
            "login_last_check_at": site.get("login_last_check_at"),
            "session_sync_status": session_sync_status,
            "session_sync_error": site.get("session_sync_error"),
            "session_synced_at": site.get("session_synced_at"),
            "status": site["status"],
            "last_error": site["last_error"],
            "last_check_at": site["last_check_at"],
            "next_check_at": site["next_check_at"],
            "consecutive_failures": site["consecutive_failures"],
            "current_groups": groups,
            "current_groups_count": len(groups),
            "current_login_groups": login_groups,
            "current_login_groups_count": len(login_groups),
            "latest_snapshot": latest_snapshot,
            "latest_change": latest_change,
        }

    def list_sites(self) -> list[dict[str, Any]]:
        with connection() as conn:
            sites = query_all(
                "SELECT * FROM sites ORDER BY id DESC", connection=conn
            )
            return [self._summary(site, connection=conn) for site in sites]

    def overview(self) -> dict[str, Any]:
        with connection() as conn:
            sites = query_all(
                "SELECT * FROM sites ORDER BY id DESC", connection=conn
            )
            changes = query_all(
                "SELECT * FROM changes ORDER BY id DESC LIMIT 8", connection=conn
            )
            midnight = _application_now().replace(
                hour=0, minute=0, second=0, microsecond=0
            )
            changes_today = query_one(
                "SELECT COUNT(*) AS count FROM changes WHERE created_at >= ?",
                (midnight.isoformat(timespec="seconds"),),
                connection=conn,
            ) or {"count": 0}
            stats = {
                "sites_total": len(sites),
                "sites_enabled": sum(1 for site in sites if site["enabled"]),
                "sites_ok": sum(1 for site in sites if site["status"] == "ok"),
                "sites_failed": sum(
                    1
                    for site in sites
                    if site["status"] in {"failed", "warning"}
                ),
                "changes_today": changes_today["count"],
            }
            return {
                "stats": stats,
                "sites": [self._summary(site, connection=conn) for site in sites],
                "changes": changes,
            }
