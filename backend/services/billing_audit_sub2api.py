"""sub2api-upstream judgment for billing audit (v7 design).

对应设计稿 ``docs/billing-audit/倍率真实性验证-设计.md`` 验证一。sub2api
usage 日志自带美元金额（无 quota 概念）：

* 列价成本 = 日志 ``total_cost``（上游自家价卡算出的倍率前金额，来自
  computeTokenBreakdown 的 per-type 单价求和）；
* 实扣 = ``actual_cost``（= total_cost × rate_multiplier）；
* 真实分组倍率 = 实扣 ÷ 列价成本，与显示分组倍率（日志自记
  rate_multiplier，缺失时退回监控快照公示倍率）对比判暗改——纯金额口径，
  与 NewAPI 上游统一走 ``judgeRatioDrift`` 收口。

主站侧判定（亏本兜底）与落库编排由 ``billing_audit_service._auditRow``
统一收口，本模块只负责上游侧。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from backend.repositories.sites import site_groups_from_row
from backend.services.billing_audit_rules import (
    EPS,
    judgeRatioDrift,
)
from backend.services.billing_audit_sub2api_published import (
    resolveSub2apiPublishedRatio,
    sub2apiPublishedGroupRatios,
)

__all__ = [
    "judgeSub2apiUpstream",
    "sub2apiPublishedGroupRatios",
]


def judgeSub2apiUpstream(
    row: Dict[str, Any],
    upLog: Dict[str, Any],
    bindingGroups: List[Any],
    publishedRatios: Dict[str, float],
    tol: float,
) -> Tuple[Dict[str, Any], List[str], Optional[str]]:
    """sub2api 上游侧判定：返回 (patch, redReasons, grayReason)。

    ``patch`` 含 upstream_actual/list_cost/derived_group_ratio 与显示倍率
    对照字段；red/gray 只覆盖上游暗改这一项判定，亏本兜底在 service 收口。
    ``tol`` 为旧契约保留（当前判定是绝对差带，不使用相对容差）。
    """
    patch: Dict[str, Any] = {}
    red: List[str] = []
    gray: Optional[str] = None

    actualUsd = upLog.get("usd")
    other = upLog.get("other") or {}
    listUsd = other.get("total_cost")
    patch["upstream_actual_usd"] = (
        round(float(actualUsd), 10) if isinstance(actualUsd, (int, float)) else None
    )
    patch["upstream_list_cost_usd"] = (
        round(float(listUsd), 10) if isinstance(listUsd, (int, float)) else None
    )
    patch["upstream_model_ratio"] = None
    patch["upstream_completion_ratio"] = None
    # 显示分组倍率（v7 数据源表）：监控快照公示倍率优先；日志自记
    # rate_multiplier 是上游实际扣的数，当基准会漏掉暗改，只作兜底。
    groupName, publishedRatio = resolveSub2apiPublishedRatio(
        upLog, bindingGroups, publishedRatios
    )
    patch["published_model_ratio"] = None
    patch["published_completion_ratio"] = None
    patch["published_group_ratio"] = (
        publishedRatio if publishedRatio is not None
        else (float(logRatio) if isinstance(logRatio, (int, float)) else None)
    )

    derivedRatio: Optional[float] = None
    if (
        isinstance(actualUsd, (int, float))
        and isinstance(listUsd, (int, float))
        and float(listUsd) > EPS
    ):
        derivedRatio = float(actualUsd) / float(listUsd)
        patch["upstream_derived_group_ratio"] = round(derivedRatio, 10)

    driftRed, driftRunnable = judgeRatioDrift(
        float(actualUsd) if isinstance(actualUsd, (int, float)) else 0.0,
        float(listUsd) if isinstance(listUsd, (int, float)) else 0.0,
        patch["published_group_ratio"],
        tol,
        # sub2api 金额是浮点美元，无 quota 取整余量。
        roundingAllowanceUsd=0.0,
    )
    if driftRed:
        red.append(driftRed)
    if not driftRunnable and gray is None:
        gray = "ratio_field_missing"

    expectedUsd = patch.get("upstream_list_cost_usd")
    shownRatio = patch["published_group_ratio"]
    if expectedUsd is not None and shownRatio is not None:
        patch["upstream_expected_usd"] = round(expectedUsd * float(shownRatio), 10)
    return patch, red, gray
