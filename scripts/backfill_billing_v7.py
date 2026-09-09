"""One-time v7 backfill for matched billing-check rows.

v7 判定链上线前的 matched 行没有 upstream_list_cost_usd /
upstream_derived_group_ratio（列表倍率列显示「—」）。本脚本用行内已存的
证据补算并按 v7 口径重判，不重新匹配上游日志：

* NewAPI 上游：列价成本 = tokens加权 × 模型倍率 ÷ quota_per_unit，模型
  倍率优先取上游日志自记（upstream_other_json），缺失退回公示快照列；
* sub2api 上游：列价成本 = 日志 total_cost；
* 显示分组倍率（判定基准）：按请求时点的公示快照——用 changes 表的
  ratio_changed 记录把当前快照倍率滚回 request_at 之前最近一次的值；
  快照与历史都拿不到时退回日志自记（只影响展示，不参与暗改判定——
  日志记的是上游实际扣的数，拿它当基准永远比对一致，查不出暗改）。
* 判定：实扣 > 列价 × 显示倍率 + max(相对容差, 半个 quota 取整余量) →
  红「暗改倍率」；收 < 花 → 红「亏本」；算不出列价/显示倍率 → 灰
  「缺显示倍率」。旧红因（主站自洽/收花比/缺官方价）一并清除。

用法（仓库根目录）：.venv/bin/python scripts/backfill_billing_v7.py [--dry-run]
"""

from __future__ import annotations

import json
from typing import Any
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.db.connection import db_query_all  # noqa: E402
from backend.repositories import billing_audit as billingRepo  # noqa: E402
from backend.repositories.billing_audit import BILLING_CHECK_COLUMNS  # noqa: E402
from backend.repositories.sites import get_site_or_404, site_quota_per_unit  # noqa: E402
from backend.services.billing_audit_rules import (  # noqa: E402
    EPS,
    judgeRatioDrift,
    newapiListCostUsd,
    safeNumber,
)


def _parseOther(raw: Any) -> dict:
    if isinstance(raw, str) and raw.strip():
        try:
            parsed = json.loads(raw)
            return parsed if isinstance(parsed, dict) else {}
        except ValueError:
            return {}
    return {}


def _ratioChangesBySite(siteId: int) -> list[dict[str, Any]]:
    """该站点全部倍率变化记录（升序），供把快照倍率滚回请求时点。"""
    rows = db_query_all(
        "SELECT group_name, old_value, new_value, created_at FROM changes "
        "WHERE site_id = ? AND change_type = 'ratio_changed' ORDER BY id ASC",
        (int(siteId),),
    )
    parsed: list[dict[str, Any]] = []
    for row in rows:
        try:
            oldValue = json.loads(row.get("old_value") or "null")
            newValue = json.loads(row.get("new_value") or "null")
        except ValueError:
            continue
        if not isinstance(oldValue, dict) or not isinstance(newValue, dict):
            continue
        oldRatio, newRatio = oldValue.get("ratio"), newValue.get("ratio")
        if not isinstance(oldRatio, (int, float)) or not isinstance(newRatio, (int, float)):
            continue
        parsed.append({
            "group": str(row.get("group_name") or ""),
            "old": float(oldRatio),
            "new": float(newRatio),
            "at": str(row.get("created_at") or ""),
        })
    return parsed


def _displayedRatioAt(
    changes: list[dict[str, Any]],
    snapshotRatio: float | None,
    requestAt: str,
) -> float | None:
    """请求时点的公示倍率：从行里存的快照值沿 changes 逆推到 request_at 之前。

    changes 按时间升序，每条 = 「at 时刻某分组 从 old 变 new」。从核对时点的
    快照值逆着应用（at > requestAt 的记录：值变回 old）即可还原请求时点的
    显示值；当前值对不上某条记录的落点说明还原链断裂，返回 None。
    """
    if snapshotRatio is None:
        return None
    value = float(snapshotRatio)
    for change in reversed(changes):
        if change["at"] <= requestAt:
            break
        if abs(value - change["new"]) <= 1e-9:
            value = change["old"]
        else:
            # 快照值对不上这条记录的落点（可能变的是别的分组的同值倍率），
            # 无法确认还原方向——按「没有变化过」处理，直接用快照值。
            continue
    return value


def backfill(dryRun: bool = False) -> None:
    settings = billingRepo.getBillingAuditSettings()
    tol = float(settings["tolerance_percent"]) / 100.0
    quotaPerUnit = float(settings["quota_per_unit"] or 500000)

    rows = db_query_all(
        f"SELECT {BILLING_CHECK_COLUMNS} FROM billing_request_checks "
        "WHERE match_status = 'matched'"
    )
    print(f"matched 行共 {len(rows)} 条")

    qpuBySite: dict[int, float] = {}
    platformBySite: dict[int, str] = {}
    changesBySite: dict[int, list[dict[str, Any]]] = {}
    statusCount: dict[str, int] = {}
    missing = 0
    for row in rows:
        siteId = int(row.get("upstream_site_id") or 0)
        if siteId not in qpuBySite:
            siteRow = get_site_or_404(siteId)[0] if siteId else None
            qpuBySite[siteId] = float(site_quota_per_unit(siteRow) or quotaPerUnit)
            platformBySite[siteId] = str((siteRow or {}).get("platform") or "newapi").lower()
            changesBySite[siteId] = _ratioChangesBySite(siteId) if siteId else []
        qpu = qpuBySite[siteId]
        platform = platformBySite[siteId]

        other = _parseOther(row.get("upstream_other_json"))
        actualUsd = safeNumber(row.get("upstream_actual_usd"))
        logRatio = safeNumber(other.get("group_ratio") if platform != "sub2api" else other.get("rate_multiplier"))
        # 判定基准 = 请求时点公示倍率。行里存的 published_group_ratio 是
        # 核对时点解析到的分组倍率（老行也有），再用该站点 changes 记录把它
        # 滚回 request_at 之前；核对之后上游没改过价就是原值，改过则还原。
        displayed = _displayedRatioAt(
            changesBySite[siteId],
            safeNumber(row.get("published_group_ratio")),
            str(row.get("request_at") or ""),
        )
        if displayed is None:
            # 快照/还原链都不可用才退日志自记——只保证不误报，等于放弃暗改核查。
            displayed = logRatio

        if platform == "sub2api":
            listCost = safeNumber(other.get("total_cost"))
            rounding = 0.0
        else:
            # 列价成本用的模型倍率优先取日志自记：与显示分组倍率无交集，
            # 日志记的模型倍率就是上游实际按来计价的卡（暗改模型价时它同样
            # 会变，列价用核对时点快照同样会张冠李戴）。
            modelRatio = safeNumber(other.get("model_ratio")) or safeNumber(row.get("published_model_ratio"))
            completionRatio = safeNumber(other.get("completion_ratio")) or safeNumber(row.get("published_completion_ratio"))
            card: dict[str, Any] = {
                "model_ratio": modelRatio,
                "completion_ratio": completionRatio if completionRatio is not None else 1.0,
                "quota_type": 0,
            }
            if modelRatio is None:
                callPrice = safeNumber(other.get("model_price"))
                if callPrice is not None:
                    card.update({"model_ratio": 1.0, "model_price": callPrice, "quota_type": 1})
            listCost = newapiListCostUsd(
                {
                    "prompt_tokens": row.get("prompt_tokens"),
                    "completion_tokens": row.get("completion_tokens"),
                    "cache_read_tokens": row.get("cache_read_tokens"),
                    "cache_creation_tokens": row.get("cache_creation_tokens"),
                },
                card if modelRatio is not None or card.get("model_price") else None,
                safeNumber(other.get("cache_ratio")),
                qpu,
            )
            rounding = 0.5 / qpu

        patch: dict[str, Any] = {}
        red: list[str] = []
        gray: str | None = None
        if displayed is not None:
            # 写回请求时点的显示倍率，页面「真实 vs 显示」与判定基准一致
            patch["published_group_ratio"] = displayed
        if actualUsd is not None and listCost is not None and listCost > EPS:
            patch["upstream_list_cost_usd"] = listCost
            patch["upstream_derived_group_ratio"] = round(actualUsd / listCost, 10)
            driftRed, runnable = judgeRatioDrift(actualUsd, listCost, displayed, tol, rounding)
            if displayed is not None:
                patch["upstream_expected_usd"] = round(listCost * float(displayed), 10)
            if driftRed:
                red.append(driftRed)
            if not runnable and gray is None:
                gray = "ratio_field_missing"
        else:
            gray = "ratio_field_missing"

        mainUsd = safeNumber(row.get("main_usd")) or 0.0
        upstreamUsd = actualUsd or 0.0
        if mainUsd and upstreamUsd and mainUsd < upstreamUsd - EPS:
            red.append("negative_margin")

        status = "mismatch" if red else ("unknown" if gray else "ok")
        reasons = red + ([gray] if gray else [])
        statusCount[status] = statusCount.get(status, 0) + 1
        if listCost is None:
            missing += 1
        if dryRun:
            continue
        patch["status"] = status
        patch["reason_codes_json"] = json.dumps(reasons, ensure_ascii=False) if reasons else None
        billingRepo.updateBillingCheckResult(int(row["id"]), patch)

    print("回填后状态分布:", statusCount, f"，缺列价证据 {missing} 条（归灰）")
    if dryRun:
        print("（dry-run，未写库）")


if __name__ == "__main__":
    backfill(dryRun="--dry-run" in sys.argv)
