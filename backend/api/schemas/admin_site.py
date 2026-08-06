"""Admin-site payload schemas."""

from backend.api.schemas.common import CompatibilityModel


class AdminSiteRequest(CompatibilityModel):
    name: str = ""
    platform: str = "newapi"
    base_url: str = ""
