"""Notification configuration schema."""

from backend.api.schemas.common import CompatibilityModel


class NotificationSettingsRequest(CompatibilityModel):
    wecom_enabled: bool | None = None
    email_enabled: bool | None = None
