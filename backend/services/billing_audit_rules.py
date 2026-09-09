"""Shared pure judgment rules for billing audit (platform-agnostic).

对应设计稿 ``docs/billing-audit/倍率真实性验证-设计.md``（v7）。只放
无 I/O 的判定纯函数，供 ``billing_audit_service``（NewAPI 上游）与
``billing_audit_sub2api``（sub2api 上游）共用；金额一律美元，容差为
相对偏差 + 取整余量。落库与编排仍在 ``billing_audit_service``。
"""

from __future__ import annotations

from typing import Any, Dict, Optional, Tuple

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


def newapiListCostUsd(
    tokens: Dict[str, Any],
    modelPub: Optional[Dict[str, Any]],
    cacheRatioHint: Optional[float],
    quotaPerUnit: float,
) -> Optional[float]:
    """NewAPI 上游列价成本（v7 设计稿）：按上游显示的模型倍率把四类 tokens
    折成 quota 再除充值比例，得到「分组倍率乘之前的钱」（美元）。

    tokens 用实际请求构成；缓存读从输入中扣除后按缓存折扣（未知取 1）单独计，
    避免双算；缺 model_ratio / quota_type=1 按次价缺失时算不了，返回 None。
    """
    if not modelPub or "model_ratio" not in modelPub:
        return None
    modelRatio = float(modelPub["model_ratio"])
    if modelRatio <= 0:
        return None
    if float(modelPub.get("quota_type") or 0) == 1:
        callPrice = modelPub.get("model_price")
        return round(float(callPrice) / quotaPerUnit, 10) if callPrice else None
    completionRatio = float(modelPub.get("completion_ratio") or 1.0)
    cacheRatio = float(cacheRatioHint) if cacheRatioHint is not None else 1.0
    prompt = float(tokens.get("prompt_tokens") or 0)
    cacheRead = min(float(tokens.get("cache_read_tokens") or 0), prompt)
    cacheWrite = float(tokens.get("cache_creation_tokens") or 0)
    uncachedInput = max(0.0, prompt - cacheRead)
    weighted = (
        uncachedInput
        + cacheWrite
        + cacheRead * cacheRatio
        + float(tokens.get("completion_tokens") or 0) * completionRatio
    )
    return round(weighted * modelRatio / quotaPerUnit, 10)


def judgeRatioDrift(
    actualUsd: float,
    listCostUsd: float,
    displayedRatio: Optional[float],
    tol: float,
    roundingAllowanceUsd: float = 0.0,
) -> Tuple[Optional[str], bool]:
    """分组倍率暗改判定（v7 设计稿验证一）：应扣 = 列价成本 × 显示分组倍率，
    实扣比应扣多出容带 → 红「暗改倍率」；一致或便宜 → 不报。

    容带 = max(应扣 × 相对容差 tol, 取整余量)。取整余量覆盖 NewAPI quota
    整数化的舍入误差（±0.5 quota）：微小请求上舍入会把「真实倍率」推高
    超过 1%（实测 220 号行：45/49.5 = ×0.909 vs 显示 ×0.9），不能只看
    相对偏差。显示倍率或列价成本缺失时返回 (None, False)，由调用方归灰。
    """
    if listCostUsd <= 0 or displayedRatio is None:
        return None, False
    expectedUsd = listCostUsd * float(displayedRatio)
    band = max(abs(expectedUsd) * tol, roundingAllowanceUsd)
    if actualUsd - expectedUsd > band:
        return "upstream_ratio_drift", True
    return None, True

