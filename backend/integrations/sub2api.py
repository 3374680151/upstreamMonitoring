"""sub2api integration facade."""

from __future__ import annotations

from typing import Any

from backend import legacy_runtime as legacy


class Sub2ApiClient:
    def fetch_groups(self, site: dict[str, Any]):
        return legacy.fetch_sub2api_user_groups(
            site["base_url"],
            username=site.get("login_username") or "",
            password=site.get("login_password") or "",
            auth_mode=site.get("auth_mode") or "password",
            access_token=site.get("access_token") or "",
            refresh_token=site.get("refresh_token") or "",
            site_id=int(site.get("id") or 0),
        )

    def fetch_account(self, site: dict[str, Any]):
        return legacy.fetch_sub2api_account(
            site["base_url"],
            username=site.get("login_username") or "",
            password=site.get("login_password") or "",
            auth_mode=site.get("auth_mode") or "password",
            access_token=site.get("access_token") or "",
            refresh_token=site.get("refresh_token") or "",
            site_id=int(site.get("id") or 0),
        )

    def fetch_models(self, site: dict[str, Any]):
        return legacy.fetch_sub2api_model_data(
            site["base_url"],
            username=site.get("login_username") or "",
            password=site.get("login_password") or "",
            auth_mode=site.get("auth_mode") or "password",
            access_token=site.get("access_token") or "",
            refresh_token=site.get("refresh_token") or "",
            site_id=int(site.get("id") or 0),
        )
