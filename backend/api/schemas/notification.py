"""Notification configuration schema."""

from typing import Optional

from backend.api.schemas.common import CompatibilityModel


class NotificationSettingsRequest(CompatibilityModel):
    wecom_enabled: Optional[bool] = None
    email_enabled: Optional[bool] = None
