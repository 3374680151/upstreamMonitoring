"""Site payload schemas kept permissive for backwards compatibility."""

from __future__ import annotations

from typing import Any, Optional

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
    name: Optional[str] = None
    base_url: Optional[str] = None
    platform: Optional[str] = None
    enabled: Optional[bool] = None
    interval_minutes: Optional[int] = None
    login_enabled: Optional[bool] = None
    auth_mode: Optional[str] = None


class DiscoveryImportRequest(CompatibilityModel):
    admin_site_id: int = 0
    interval_minutes: int = 3
    items: list[dict[str, Any]] = Field(default_factory=list)
