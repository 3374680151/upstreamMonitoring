"""Application services for monitoring reads and checks."""

from __future__ import annotations

from typing import Any

from backend.repositories.changes import ChangeRepository
from backend.repositories.monitoring import MonitoringRepository
from backend.core.errors import ConflictError, UpstreamError
from backend.services.check_service import CheckService
from backend.integrations.newapi import NewApiClient
from backend.integrations.sub2api import normalize_account as normalize_sub2api_account
from backend.core.time import utc_now_iso
from backend.services.sub2api_auth_service import Sub2ApiSiteAuthService


class MonitoringService:
    def __init__(
        self,
        changes: ChangeRepository | None = None,
        monitoring: MonitoringRepository | None = None,
        checks: CheckService | None = None,
    ) -> None:
        self.changes = changes or ChangeRepository()
        self.monitoring = monitoring or MonitoringRepository()
        self.checks = checks or CheckService()
        self.sub2api_auth = Sub2ApiSiteAuthService()

    def overview(self) -> dict[str, Any]:
        return self.monitoring.overview()

    def list_sites(self):
        return self.monitoring.list_sites(), []

    def list_changes(self, limit: int = 100):
        return self.changes.list(limit)

    def list_site_changes(self, site_id: int, limit: int = 100):
        return self.changes.list_for_site(site_id, limit)

    def snapshots(self, site_id: int):
        return self.changes.snapshots_for_site(site_id)

    def account(self, site: dict[str, Any]):
        platform = str(site.get("platform") or "newapi").strip().lower()
        if platform == "newapi":
            auth_mode = str(site.get("auth_mode") or "token").strip().lower()
            has_browser_session = bool(
                auth_mode == "browser"
                and site.get("access_user_id")
                and (
                    site.get("access_token")
                    or site.get("browser_cookie")
                )
            )
            has_token_auth = bool(
                auth_mode != "browser"
                and site.get("access_token")
                and site.get("access_user_id")
            )
            if not (site.get("login_enabled") and (has_browser_session or has_token_auth)):
                raise ConflictError("该 NewAPI 站点未配置可用登录态，无法读取账户额度")
            ok, data, error = NewApiClient().fetch_account_for_site(site)
            if not ok:
                raise UpstreamError(error or "读取 NewAPI 账户失败")
            return {
                "success": True,
                "source": "/api/user/self",
                "fetched_at": utc_now_iso(),
                "account": NewApiClient.normalize_account(data),
            }

        if not (site.get("login_enabled") or site.get("access_token") or site.get("login_username")):
            raise ConflictError("该 sub2api 站点未配置登录信息，无法读取账户额度")
        ok, data, error = self.sub2api_auth.fetch_account(site)
        if not ok:
            raise UpstreamError(error or "读取 sub2api 账户失败")
        return {
            "success": True,
            "source": "/api/v1/auth/me",
            "fetched_at": utc_now_iso(),
            "account": normalize_sub2api_account(data),
        }

    def check(self, site_id: int):
        return self.checks.check(site_id)
