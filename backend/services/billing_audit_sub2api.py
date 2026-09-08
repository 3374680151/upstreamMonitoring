"""sub2api-upstream judgment for billing audit (P2).

对应设计稿 ``docs/billing-audit/billing-audit-方案.md`` 第 12 节 P2 条目。
sub2api 的 usage 日志直接给美元金额与请求实际生效的分组倍率
（rate_multiplier），没有 NewAPI 式的 quota/model_ratio——所以：

* ``upstream_actual_usd`` = usage 行 ``actual_cost``（实扣美元，无需
  quota_per_unit 换算）；
* 暗改证据 = 日志自记 rate_multiplier 与我方监控快照的分组公示倍率
  （``current_groups_json``，来自 /api/v1/groups/rates）逐条对比；
  自记缺失时退回金额对比（``total_cost`` × 公示倍率 vs 实扣）；
* 公示倍率取不到时整行归灰 ``ratio_field_missing``，绝不误报红。

主站侧判定（计费自洽 / 加价倍率关系 / 亏本）与官方价灰度由
``billing_audit_service._auditRow`` 统一收口，本模块只负责上游侧。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from backend.repositories.sites import site_groups_from_row
from backend.services.billing_audit_rules import EPS, relDiff

__all__ = [
    "judgeSub2apiUpstream",
    "sub2apiPublishedGroupRatios",
]


def sub2apiPublishedGroupRatios(site: Optional[Dict[str, Any]]) -> Dict[str, float]:
    """上游公示分组倍率：监控快照 ``current_groups_json``（sub2api 的 ratio
    即用户侧实际生效的 rate_multiplier）。"""
    groups = site_groups_from_row(site or {})
    ratios: Dict[str, float] = {}
    for name, info in groups.items():
        if isinstance(info, dict) and isinstance(info.get("ratio"), (int, float)):
            ratios[str(name)] = float(info["ratio"])
    return ratios


def _resolveSub2apiGroupRatio(
    upLog: Dict[str, Any], bindingGroups: List[Any], published: Dict[str, float]
) -> Tuple[Optional[str], Optional[float]]:
    """定位请求所在分组的公示倍率：优先日志自记分组名，其次绑定分组兜底。"""
    candidates: List[str] = []
    userGroup = upLog.get("user_group")
    if isinstance(userGroup, str) and userGroup.strip():
        candidates.append(userGroup.strip())
    for entry in bindingGroups or []:
        if isinstance(entry, dict):
            name = entry.get("group") or entry.get("name")
            if isinstance(name, str):
                candidates.append(name)
        elif isinstance(entry, str):
            candidates.append(entry)
    for name in candidates:
        if name in published:
            return name, published[name]
    if len(published) == 1:
        onlyName = next(iter(published))
        return onlyName, published[onlyName]
    return None, None


def judgeSub2apiUpstream(
    row: Dict[str, Any],
    upLog: Dict[str, Any],
    bindingGroups: List[Any],
    publishedRatios: Dict[str, float],
    tol: float,
) -> Tuple[Dict[str, Any], List[str], Optional[str]]:
    """sub2api 上游侧判定：返回 (patch, redReasons, grayReason)。

    ``patch`` 含 upstream_actual/expected_usd 与三组倍率对照字段；
    red/gray 只覆盖上游暗改这一项判定，其余判定在 service 收口。
    """
    patch: Dict[str, Any] = {}
    red: List[str] = []
    gray: Optional[str] = None

    actualUsd = upLog.get("usd")
    appliedRatio = upLog.get("group_ratio")
    other = upLog.get("other") or {}
    listUsd = other.get("total_cost")
    patch["upstream_actual_usd"] = (
        round(float(actualUsd), 10) if isinstance(actualUsd, (int, float)) else None
    )
    patch["upstream_model_ratio"] = None
    patch["upstream_completion_ratio"] = None
    patch["upstream_group_ratio"] = appliedRatio

    groupName, publishedRatio = _resolveSub2apiGroupRatio(
        upLog, bindingGroups, publishedRatios
    )
    patch["published_model_ratio"] = None
    patch["published_completion_ratio"] = None
    patch["published_group_ratio"] = publishedRatio

    driftRunnable = False
    # 证据优先：日志自记 rate_multiplier vs 公示快照。
    if appliedRatio is not None and publishedRatio is not None:
        driftRunnable = True
        if relDiff(float(appliedRatio), float(publishedRatio)) > tol:
            red.append("upstream_ratio_drift")
    elif (
        publishedRatio is not None
        and isinstance(listUsd, (int, float))
        and isinstance(actualUsd, (int, float))
    ):
        # 自记倍率缺失时按金额对比：期望 = 倍率前列价 × 公示倍率。
        driftRunnable = True
        expectedUsd = round(float(listUsd) * float(publishedRatio), 10)
        patch["upstream_expected_usd"] = expectedUsd
        if float(actualUsd) > EPS and relDiff(float(actualUsd), expectedUsd) > tol:
            red.append("upstream_ratio_drift")
    elif publishedRatio is not None and isinstance(actualUsd, (int, float)):
        # 列价缺失（账务修正等）：倍率证据与公示都有但列价缺——不重复判红。
        driftRunnable = False
    if not driftRunnable and gray is None:
        gray = "ratio_field_missing"
    return patch, red, gray
