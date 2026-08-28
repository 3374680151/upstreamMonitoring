"""Console settings patch schema."""

from typing import Optional

from backend.api.schemas.common import CompatibilityModel


class SettingsPatchRequest(CompatibilityModel):
    """PATCH 语义：传哪个字段更新哪个，两者都缺视为无效请求。"""

    main_site_reconcile_mode: Optional[str] = None
    main_site_sync_all: Optional[bool] = None
