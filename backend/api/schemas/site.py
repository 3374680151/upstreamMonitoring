"""Site payload schemas kept permissive for backwards compatibility."""

from __future__ import annotations

from typing import Any

from pydantic import Field

from backend.api.schemas.common import CompatibilityModel


class SiteCreateRequest(CompatibilityModel):
    name: str = ""
    base_url: str = ""
    platform: str = "newapi"
    enabled: bool = True
    interval_minutes: int = 3
    login_enabled: bool = False
    auth_mode: str = "password"


class SiteUpdateRequest(CompatibilityModel):
    name: str | None = None
    base_url: str | None = None
    platform: str | None = None
    enabled: bool | None = None
    interval_minutes: int | None = None
    login_enabled: bool | None = None
    auth_mode: str | None = None


class DiscoveryImportRequest(CompatibilityModel):
    admin_site_id: int = 0
    interval_minutes: int = 3
    items: list[dict[str, Any]] = Field(default_factory=list)
