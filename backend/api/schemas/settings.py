"""Console settings patch schema."""

from typing import Optional

from backend.api.schemas.common import CompatibilityModel


class SettingsPatchRequest(CompatibilityModel):
    """PATCH 语义：传哪个字段更新哪个，两者都缺视为无效请求。

    主站同步范围（原 main_site_sync_all）已改为每次同步时在请求里传 scope。
    """

    main_site_reconcile_mode: Optional[str] = None
