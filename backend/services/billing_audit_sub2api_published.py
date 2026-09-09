"""sub2api 公示分组倍率解析（billing audit 用）。

从监控快照 ``current_groups_json``（/api/v1/groups/available + /rates 口径）
读上游公示分组倍率，并按 日志自记分组名 → 渠道绑定分组 → 唯一分组 的顺序
定位请求所在分组。独立成文件避免 ``billing_audit_sub2api`` 判定模块与
repository 依赖搅在一起。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from backend.repositories.sites import site_groups_from_row

__all__ = [
    "resolveSub2apiPublishedRatio",
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


def resolveSub2apiPublishedRatio(
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
