"""Manual and scheduled monitoring checks.

Snapshot persistence, diff calculation, status transitions, and change
history are owned here and by their repositories.  Upstream collectors are
provided by protocol clients; credential refresh and fallback are coordinated
by the dedicated authentication service.
"""

from __future__ import annotations

import json
from datetime import timedelta
from typing import Any

from backend.core.time import app_now, utc_now_iso
from backend.domain.model_health import strip_sub2api_auth_context
from backend.domain.diff import diff_groups
from backend.integrations.newapi import NewApiClient
from backend.integrations.newapi_admin import parse_groups_payload
from backend.integrations.sub2api import parse_sub2api_groups
from backend.integrations.sub2api_admin import is_auth_error
from backend.repositories.changes import ChangeRepository
from backend.repositories.sites import SiteRepository
from backend.services.model_cache import ModelCacheService
from backend.services.notification_service import NotificationService
from backend.services.sub2api_auth_service import Sub2ApiSiteAuthService


DEFAULT_INTERVAL_MINUTES = 3


class CheckService:
    def __init__(
        self,
        sites: SiteRepository | None = None,
        changes: ChangeRepository | None = None,
        cache: ModelCacheService | None = None,
        newapi: NewApiClient | None = None,
        sub2api_auth: Sub2ApiSiteAuthService | None = None,
        notifications: NotificationService | None = None,
    ) -> None:
        self.sites = sites or SiteRepository()
        self.changes = changes or ChangeRepository()
        self.cache = cache or ModelCacheService()
        self.newapi = newapi or NewApiClient()
        self.sub2api_auth = sub2api_auth or Sub2ApiSiteAuthService(
            sites=self.sites
        )
        self.notifications = notifications or NotificationService()

    def check(self, site_id: int) -> dict[str, Any]:
        site = self.sites.get(int(site_id))
        if not site:
            return {"success": False, "message": "site not found"}

        checked_at = utc_now_iso()
        platform = str(site.get("platform") or "newapi").strip().lower()
        if platform == "newapi":
            ok, payload, error_message = self.newapi.fetch_groups(
                str(site.get("base_url") or "")
            )
            new_groups = parse_groups_payload(payload) if ok else {}
            source = "/api/user/groups"
        else:
            ok, payload, error_message = self.sub2api_auth.fetch_groups(site)
            new_groups = (
                parse_sub2api_groups(
                    payload.get("data") if isinstance(payload, dict) else [],
                    payload.get("user_rates") if isinstance(payload, dict) else {},
                )
                if ok
                else {}
            )
            source = "/api/v1/groups/available"
            if (
                not ok
                and str(site.get("auth_mode") or "").strip().lower() == "browser"
                and (
                    bool(
                        isinstance(payload, dict)
                        and payload.get("browser_sync_required")
                    )
                    or is_auth_error(payload, error_message)
                )
            ):
                self.sites.update_fields(
                    int(site_id),
                    {
                        "session_sync_status": "expired",
                        "session_sync_error": error_message
                        or "登录态已过期，请重新登录",
                        "updated_at": checked_at,
                    },
                )
        payload = self._safe_payload(payload, platform)
        interval = max(1, int(site.get("interval_minutes") or DEFAULT_INTERVAL_MINUTES))
        next_check_at = (
            app_now() + timedelta(minutes=interval)
        ).isoformat(timespec="seconds")
        if not ok:
            failures = int(site.get("consecutive_failures") or 0) + 1
            status = "failed" if failures >= 3 else "warning"
            self.changes.record_check(
                site_id=int(site_id),
                checked_at=checked_at,
                source=source,
                payload=payload,
                groups=None,
                status="failed",
                error_message=error_message,
                site_updates={
                    "status": status,
                    "last_error": error_message,
                    "last_check_at": checked_at,
                    "next_check_at": next_check_at,
                    "consecutive_failures": failures,
                    "updated_at": checked_at,
                },
            )
            result: dict[str, Any] = {
                "success": False,
                "message": error_message or "检测失败",
                "status": status,
            }
            if isinstance(payload, dict):
                for key in ("code", "browser_sync_required"):
                    if payload.get(key):
                        result[key] = payload[key] if key == "code" else True
            return result

        groups = dict(new_groups or {})
        # Cache enrichment is read-only and never adds model requests to the
        # check hot path.
        try:
            self.cache.attach_group_model_names(int(site_id), groups)
        except Exception:
            pass
        previous = self.changes.latest_success_snapshot(int(site_id))
        old_groups: dict[str, Any] = {}
        if previous and previous.get("groups_json"):
            try:
                decoded = json.loads(previous["groups_json"])
                if isinstance(decoded, dict):
                    old_groups = decoded
            except (TypeError, ValueError):
                old_groups = {}
        changes = diff_groups(old_groups, groups) if old_groups else []
        login_groups: dict[str, Any] = {}
        login_groups_json: str | None = None
        login_error: str | None = None
        if (
            platform == "newapi"
            and site.get("login_enabled")
            and (
                (
                    str(site.get("auth_mode") or "token").strip().lower()
                    == "browser"
                    and site.get("access_user_id")
                    and (
                        site.get("access_token")
                        or site.get("browser_cookie")
                    )
                )
                or (
                    str(site.get("auth_mode") or "token").strip().lower()
                    != "browser"
                    and site.get("access_token")
                    and site.get("access_user_id")
                )
            )
        ):
            login_ok, login_payload, login_error_message = self.newapi.fetch_groups_for_site(site)
            if login_ok:
                login_groups = parse_groups_payload(login_payload)
                login_groups_json = json.dumps(
                    login_groups, ensure_ascii=False, sort_keys=True
                )
                old_login_groups: dict[str, Any] = {}
                if site.get("current_login_groups_json"):
                    try:
                        decoded_login = json.loads(site["current_login_groups_json"])
                        if isinstance(decoded_login, dict):
                            old_login_groups = decoded_login
                    except (TypeError, ValueError):
                        old_login_groups = {}
                login_changes = diff_groups(old_login_groups, login_groups) if old_login_groups else []
                for change in login_changes:
                    change["message"] = f"认证增强 {change['message']}"
                changes.extend(login_changes)
            else:
                login_error = login_error_message or "认证增强采集失败"
        for change in changes:
            change["severity"] = self._severity(change)
        groups_json = json.dumps(groups, ensure_ascii=False, sort_keys=True)
        site_updates: dict[str, Any] = {
            "status": "warning" if login_error else "ok",
            "last_error": None,
            "last_check_at": checked_at,
            "next_check_at": next_check_at,
            "consecutive_failures": 0,
            "current_groups_json": groups_json,
            "login_last_error": login_error,
            "login_last_check_at": checked_at if site.get("login_enabled") else None,
            "updated_at": checked_at,
        }
        if login_groups_json is not None:
            site_updates["current_login_groups_json"] = login_groups_json
        self.changes.record_check(
            site_id=int(site_id),
            checked_at=checked_at,
            source=source,
            payload=payload,
            groups=groups,
            status="success",
            error_message=None,
            site_updates=site_updates,
            changes=changes,
        )
        if changes:
            try:
                self.notifications.notify_changes(site, changes, checked_at)
            except Exception:
                pass
        try:
            self.cache.schedule(int(site_id))
        except Exception:
            pass
        return {
            "success": not bool(login_error),
            "message": login_error or "ok",
            "checked_at": checked_at,
            "groups": groups,
            "login_groups": login_groups,
            "changes": changes,
        }

    @staticmethod
    def _severity(change: dict[str, Any]) -> str:
        if change.get("change_type") == "group_removed":
            return "critical"
        if change.get("change_type") == "model_removed_from_group":
            return "warning"
        if change.get("change_type") == "ratio_changed" and (change.get("change_percent") or 0) > 0:
            return "warning"
        return "info"

    @staticmethod
    def _safe_payload(payload: Any, platform: str) -> Any:
        if platform != "sub2api":
            return payload if isinstance(payload, (dict, list, str, int, float, bool)) else {}
        return strip_sub2api_auth_context(
            payload if isinstance(payload, (dict, list, str, int, float, bool)) else {}
        )


__all__ = ["CheckService"]
