"""Shared pure judgment rules for billing audit (platform-agnostic).

对应设计稿 ``docs/billing-audit/billing-audit-方案.md`` 3.2/3.3 节。只放
无 I/O 的判定纯函数，供 ``billing_audit_service``（NewAPI 上游）与
``billing_audit_sub2api``（sub2api 上游）共用；金额一律美元，容差为相对
偏差。落库与编排仍在 ``billing_audit_service``。
"""

from __future__ import annotations

from typing import Any, Dict, Optional

EPS = 1e-12


def safeNumber(value: Any) -> Optional[float]:
    """other 字段里的数值可能是字符串，统一容错转 float。"""
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return None
    return None


def relDiff(actual: float, expected: float) -> float:
    if abs(expected) <= EPS:
        return 0.0 if abs(actual) <= EPS else float("inf")
    return abs(actual - expected) / abs(expected)


def officialCost(priceRow: Dict[str, Any], tokens: Dict[str, Any]) -> Optional[float]:
    """官方成本；per_token 缺单价的分量按输入价兜底（缓存写入价缺省视为 0 档按输入价）。"""
    inputPrice = priceRow.get("input_usd_per_m")
    if str(priceRow.get("quota_type") or "per_token") == "per_call":
        callPrice = priceRow.get("price_per_call_usd")
        return float(callPrice) if isinstance(callPrice, (int, float)) else None
    if not isinstance(inputPrice, (int, float)):
        return None
    fallback = float(inputPrice)

    def component(field: str) -> float:
        value = priceRow.get(field)
        return float(value) if isinstance(value, (int, float)) else fallback

    cacheRead = float(tokens.get("cache_read_tokens") or 0)
    cacheWrite = float(tokens.get("cache_creation_tokens") or 0)
    cost = (
        float(tokens.get("prompt_tokens") or 0) * fallback
        + cacheRead * component("cached_input_usd_per_m")
        + cacheWrite * component("cache_write_usd_per_m")
        + float(tokens.get("completion_tokens") or 0) * component("output_usd_per_m")
    ) / 1_000_000.0
    return round(cost, 10)
