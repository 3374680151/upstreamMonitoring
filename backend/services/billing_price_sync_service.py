"""Daily sync of sub2api official model prices into ``official_model_prices``.

sub2api admin channels carry per-model ``model_pricing`` rules; the billing
audit treats those as the reference "official" price for models routed
through sub2api upstreams.  This service replays the last admin-site sync
snapshot (``admin_site_sync_state.channels_json``) into the shared price
table — it never talks to the upstream directly, so a failed upstream login
never blocks the nightly sync.  Rows maintained by hand (``source='manual'``)
are protected by the repo-level upsert guard.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from backend.core.time import utc_now_iso
from backend.db.connection import db_query_all
from backend.repositories import billing_audit as billingRepo
from backend.repositories.admin_sites import admin_site_platform

# sub2api 按 token 计价时单价单位是「美元 / token」；official_model_prices
# 统一存「美元 / 百万 token」，因此折算系数 1e6。
_PER_TOKEN_TO_PER_MILLION = 1_000_000

# official_model_prices 列 ← sub2api token 计价字段（cache_read 上游字段名）
_TOKEN_FIELD_MAP = {
    "input_usd_per_m": "input_price",
    "cached_input_usd_per_m": "cache_read_price",
    "cache_write_usd_per_m": "cache_write_price",
    "output_usd_per_m": "output_price",
}


def _firstNonNegativeFloat(*values: Any) -> Optional[float]:
    for value in values:
        if value is None or value == "":
            continue
        try:
            parsed = float(value)
        except (TypeError, ValueError):
            continue
        if parsed >= 0:
            return parsed
    return None


def collectSub2apiOfficialPrices(
    channels: List[Dict[str, Any]],
) -> Dict[str, Dict[str, Optional[float]]]:
    """从 sub2api 渠道快照提取 ``{model_name: official_model_prices 字段}``。

    token 计价 ×1e6 折算成每百万 token；按次计价取 per_request_price 原值
    （本身即美元/次，不折算）；image 计价无法映射进官方价表，跳过。
    同一模型在多渠道重复出现时非空字段后写覆盖先写（渠道按 id 升序，结果稳定）。
    """
    merged: Dict[str, Dict[str, Optional[float]]] = {}
    for channel in channels:
        if not isinstance(channel, dict):
            continue
        for rule in channel.get("model_pricing") or []:
            if not isinstance(rule, dict):
                continue
            billingMode = str(rule.get("billing_mode") or "").strip().lower()
            models = rule.get("models")
            if not isinstance(models, list):
                continue
            if billingMode == "token":
                fields: Dict[str, Optional[float]] = {}
                for column, rawField in _TOKEN_FIELD_MAP.items():
                    price = _firstNonNegativeFloat(rule.get(rawField))
                    fields[column] = (
                        None if price is None else price * _PER_TOKEN_TO_PER_MILLION
                    )
            elif billingMode == "per_request":
                fields = {
                    "price_per_call_usd": _firstNonNegativeFloat(
                        rule.get("per_request_price")
                    )
                }
            else:
                continue
            for model in models:
                name = str(model or "").strip()
                if not name:
                    continue
                target = merged.setdefault(name, {})
                for column, value in fields.items():
                    if value is not None:
                        target[column] = value
    return merged


def syncOfficialPricesFromAdminSites() -> Dict[str, Any]:
    """定时任务入口：把 sub2api 主站快照里的官方价刷进 official_model_prices。"""
    summary: Dict[str, Any] = {
        "admin_sites": 0,
        "models": 0,
        "applied": 0,
        "errors": [],
    }
    adminRows = db_query_all("SELECT id, platform FROM admin_sites ORDER BY id ASC")
    sub2apiIds = [
        int(row["id"])
        for row in adminRows
        if admin_site_platform(row) == "sub2api"
    ]
    if not sub2apiIds:
        return summary
    placeholders = ",".join("?" for _ in sub2apiIds)
    stateRows = db_query_all(
        "SELECT admin_site_id, channels_json FROM admin_site_sync_state "
        f"WHERE admin_site_id IN ({placeholders})",
        tuple(sub2apiIds),
    )
    now = utc_now_iso()
    for row in stateRows:
        adminSiteId = int(row.get("admin_site_id") or 0)
        try:
            channels = json.loads(row.get("channels_json") or "[]")
        except (TypeError, ValueError):
            summary["errors"].append(
                f"admin_site={adminSiteId} channels_json 不是合法 JSON"
            )
            continue
        if not isinstance(channels, list):
            summary["errors"].append(
                f"admin_site={adminSiteId} channels_json 不是渠道列表"
            )
            continue
        summary["admin_sites"] += 1
        prices = collectSub2apiOfficialPrices(channels)
        summary["models"] += len(prices)
        for modelName, fields in sorted(prices.items()):
            hasTokenPrice = any(
                fields.get(column) is not None for column in _TOKEN_FIELD_MAP
            )
            callPrice = fields.get("price_per_call_usd")
            if hasTokenPrice:
                quotaType = "per_token"
                columns = {column: fields.get(column) for column in _TOKEN_FIELD_MAP}
                columns["price_per_call_usd"] = None
            elif callPrice is not None:
                quotaType = "per_call"
                columns = {column: None for column in _TOKEN_FIELD_MAP}
                columns["price_per_call_usd"] = callPrice
            else:
                continue
            try:
                billingRepo.upsertOfficialModelPriceSynced(
                    modelName, quotaType, columns, now
                )
                summary["applied"] += 1
            except Exception as exc:  # noqa: BLE001 - 单模型失败不拖垮整轮
                summary["errors"].append(f"{modelName}: {exc}")
    return summary
