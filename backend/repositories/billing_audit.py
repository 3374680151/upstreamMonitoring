"""Billing-audit repository: settings, official prices, and check rows.

对应设计稿 ``docs/billing-audit/billing-audit-方案.md`` 第 5 节。设置存
``app_settings``（键前缀 ``billing_audit_``），游标按主站存
``billing_audit_cursor_{admin_site_id}``；表访问 SQL 全部收敛在本模块，
service 层不直接拼 SQL。
"""

from __future__ import annotations

import json
from datetime import timedelta
from typing import Any, Dict, List, Optional, Tuple

from backend.core.time import app_now, utc_now_iso
from backend.db.connection import (
    db_execute,
    db_execute_rowcount,
    db_query_all,
    db_query_one,
)

SETTINGS_PREFIX = "billing_audit_"

# 设计稿 5.3 节的默认值；updateBillingAuditSettings 里的钳位范围与此一致。
DEFAULT_SETTINGS: Dict[str, Any] = {
    "enabled": False,
    "interval_minutes": 5,
    "tolerance_percent": 1.0,
    "match_window_seconds": 300,
    "retention_days": 30,
    "quota_per_unit": 500000,
    "push_on_red": False,
}
SETTINGS_RANGES: Dict[str, Tuple[float, float]] = {
    "interval_minutes": (5, 1440),
    # 容差下限 0.1%：0 容差会让浮点噪声整表判红，不提供这个档位。
    "tolerance_percent": (0.1, 50.0),
    "match_window_seconds": (60, 3600),
    "retention_days": (0, 3650),
    "quota_per_unit": (1000, 100_000_000),
}
BOOLEAN_SETTINGS = {"enabled", "push_on_red"}


def _validateRange(name: str, value: float) -> float:
    """越界报错（契约：PUT settings 越界值返回 422），不做静默钳位。"""
    low, high = SETTINGS_RANGES[name]
    if not low <= value <= high:
        raise ValueError(f"设置项 {name} 超出范围（{low:g}–{high:g}）")
    return value


def _readSettingsRows() -> Dict[str, str]:
    rows = db_query_all("SELECT name, value FROM app_settings WHERE name LIKE ?", (SETTINGS_PREFIX + "%",))
    return {str(r["name"]): str(r.get("value") or "") for r in rows}


def getBillingAuditSettings() -> Dict[str, Any]:
    raw = _readSettingsRows()
    result: Dict[str, Any] = dict(DEFAULT_SETTINGS)
    for name, default in DEFAULT_SETTINGS.items():
        stored = raw.get(SETTINGS_PREFIX + name)
        if stored is None or stored == "":
            continue
        if name in BOOLEAN_SETTINGS:
            result[name] = stored.strip().lower() in {"1", "true", "yes", "on"}
        else:
            try:
                result[name] = _validateRange(name, float(stored))
            except ValueError:
                # 存量脏值不炸读取，回默认值；下次保存会被范围校验拦住。
                continue
    # 保留整型的设置项以整型返回，避免前端拿到 5.0 这类浮点展示。
    for int_name in ("interval_minutes", "match_window_seconds", "retention_days", "quota_per_unit"):
        result[int_name] = int(result[int_name])
    return result


def updateBillingAuditSettings(body: Dict[str, Any]) -> Dict[str, Any]:
    """整体保存设置：只接受已知键，越界值抛 ValueError（router 转 422）。"""
    now = utc_now_iso()
    merged = getBillingAuditSettings()
    for name in DEFAULT_SETTINGS:
        if name not in body:
            continue
        value = body.get(name)
        if name in BOOLEAN_SETTINGS:
            merged[name] = bool(value)
        else:
            try:
                merged[name] = _validateRange(name, float(value))
            except (TypeError, ValueError) as exc:
                if isinstance(exc, ValueError) and "超出范围" in str(exc):
                    raise
                raise ValueError(f"设置项 {name} 的值无效") from exc
    for int_name in ("interval_minutes", "match_window_seconds", "retention_days", "quota_per_unit"):
        merged[int_name] = int(merged[int_name])
    for name, value in merged.items():
        if name in BOOLEAN_SETTINGS:
            stored = "1" if value else "0"
        else:
            stored = str(value)
        db_execute(
            """
            INSERT INTO app_settings (name, value, updated_at) VALUES (?, ?, ?)
            ON DUPLICATE KEY UPDATE value = VALUES(value), updated_at = VALUES(updated_at)
            """,
            (SETTINGS_PREFIX + name, stored, now),
        )
    return merged


def getBillingCursor(adminSiteId: int) -> int:
    return getBillingMeta(f"cursor_{int(adminSiteId)}", 0)


def setBillingCursor(adminSiteId: int, cursor: int) -> None:
    setBillingMeta(f"cursor_{int(adminSiteId)}", str(int(cursor)))


def getBillingMeta(name: str, default: int = 0) -> int:
    row = db_query_one(
        "SELECT value FROM app_settings WHERE name = ?",
        (SETTINGS_PREFIX + name,),
    )
    try:
        return int(str((row or {}).get("value") or default))
    except ValueError:
        return default


def setBillingMeta(name: str, value: str) -> None:
    now = utc_now_iso()
    db_execute(
        """
        INSERT INTO app_settings (name, value, updated_at) VALUES (?, ?, ?)
        ON DUPLICATE KEY UPDATE value = VALUES(value), updated_at = VALUES(updated_at)
        """,
        (SETTINGS_PREFIX + name, str(value), now),
    )


# ---------------------------------------------------------------------------
# 官方价格表
# ---------------------------------------------------------------------------

def listOfficialModelPrices() -> List[Dict[str, Any]]:
    return db_query_all("SELECT * FROM official_model_prices ORDER BY model_name ASC")


def getOfficialModelPrice(modelName: str) -> Optional[Dict[str, Any]]:
    return db_query_one(
        "SELECT * FROM official_model_prices WHERE model_name = ?", (modelName,)
    )


def upsertOfficialModelPrice(body: Dict[str, Any]) -> Dict[str, Any]:
    modelName = str(body.get("model_name") or "").strip()
    if not modelName:
        raise ValueError("model_name 不能为空")
    quotaType = str(body.get("quota_type") or "per_token").strip()
    if quotaType not in {"per_token", "per_call"}:
        raise ValueError("quota_type 只支持 per_token / per_call")

    def number(field: str) -> Optional[float]:
        value = body.get(field)
        if value is None or value == "":
            return None
        try:
            parsed = float(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{field} 不是有效数字") from exc
        if parsed < 0:
            raise ValueError(f"{field} 不能为负数")
        return parsed

    now = utc_now_iso()
    db_execute(
        """
        INSERT INTO official_model_prices
        (model_name, quota_type, input_usd_per_m, cached_input_usd_per_m,
         cache_write_usd_per_m, output_usd_per_m, price_per_call_usd,
         source, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, 'manual', ?)
        ON DUPLICATE KEY UPDATE
          quota_type = VALUES(quota_type),
          input_usd_per_m = VALUES(input_usd_per_m),
          cached_input_usd_per_m = VALUES(cached_input_usd_per_m),
          cache_write_usd_per_m = VALUES(cache_write_usd_per_m),
          output_usd_per_m = VALUES(output_usd_per_m),
          price_per_call_usd = VALUES(price_per_call_usd),
          source = 'manual', updated_at = VALUES(updated_at)
        """,
        (
            modelName,
            quotaType,
            number("input_usd_per_m"),
            number("cached_input_usd_per_m"),
            number("cache_write_usd_per_m"),
            number("output_usd_per_m"),
            number("price_per_call_usd"),
            now,
        ),
    )
    row = getOfficialModelPrice(modelName)
    if not row:
        raise ValueError("价格保存失败")
    return row


def deleteOfficialModelPrice(modelName: str) -> bool:
    deleted = db_execute_rowcount(
        "DELETE FROM official_model_prices WHERE model_name = ?", (modelName,)
    )
    return deleted > 0


# ---------------------------------------------------------------------------
# 核对记录
# ---------------------------------------------------------------------------

BILLING_CHECK_COLUMNS = (
    "id, admin_site_id, channel_id, channel_name, upstream_site_id, "
    "upstream_site_name, main_log_id, request_at, model_name, prompt_tokens, "
    "completion_tokens, cache_read_tokens, cache_creation_tokens, main_quota, "
    "main_usd, main_model_ratio, main_group_ratio, main_completion_ratio, "
    "official_usd, official_input_usd_per_m, official_cached_input_usd_per_m, "
    "official_cache_write_usd_per_m, official_output_usd_per_m, "
    "upstream_expected_usd, upstream_actual_usd, upstream_log_id, "
    "upstream_model_ratio, upstream_group_ratio, upstream_completion_ratio, "
    "published_model_ratio, published_group_ratio, published_completion_ratio, "
    "match_status, status, reason_codes_json, main_other_json, "
    "upstream_other_json, checked_at, created_at, updated_at"
)


def insertBillingCheck(row: Dict[str, Any]) -> bool:
    """插入一条主站日志核对行；(admin_site_id, main_log_id) 冲突时忽略。

    首次插入的行 status=unknown 且 checked_at 为空，等本轮稍后的判定步骤
    （或下一轮）补齐结果。返回是否真的插入了新行。
    """
    now = utc_now_iso()
    inserted = db_execute_rowcount(
        """
        INSERT IGNORE INTO billing_request_checks (
            admin_site_id, channel_id, channel_name, upstream_site_id,
            upstream_site_name, main_log_id, request_at, model_name,
            prompt_tokens, completion_tokens, cache_read_tokens,
            cache_creation_tokens, main_quota, main_model_ratio,
            main_group_ratio, main_completion_ratio, match_status, status,
            reason_codes_json, main_other_json, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'no_binding', 'unknown', NULL, ?, ?, ?)
        """,
        (
            int(row["admin_site_id"]),
            row.get("channel_id"),
            row.get("channel_name"),
            row.get("upstream_site_id"),
            row.get("upstream_site_name"),
            int(row["main_log_id"]),
            row["request_at"],
            row["model_name"],
            int(row.get("prompt_tokens") or 0),
            int(row.get("completion_tokens") or 0),
            row.get("cache_read_tokens"),
            row.get("cache_creation_tokens"),
            int(row.get("main_quota") or 0),
            row.get("main_model_ratio"),
            row.get("main_group_ratio"),
            row.get("main_completion_ratio"),
            row.get("main_other_json"),
            now,
            now,
        ),
    )
    return inserted > 0


def listPendingBillingChecks(adminSiteId: int, limit: int) -> List[Dict[str, Any]]:
    return db_query_all(
        f"""
        SELECT {BILLING_CHECK_COLUMNS} FROM billing_request_checks
        WHERE admin_site_id = ? AND checked_at IS NULL
        ORDER BY request_at ASC, id ASC
        LIMIT ?
        """,
        (int(adminSiteId), int(limit)),
    )


def countPendingBillingChecks(adminSiteId: int) -> int:
    row = db_query_one(
        "SELECT COUNT(*) AS total FROM billing_request_checks WHERE admin_site_id = ? AND checked_at IS NULL",
        (int(adminSiteId),),
    )
    return int((row or {}).get("total") or 0)


def countBillingChecksByStatus(adminSiteId: int, status: str) -> int:
    row = db_query_one(
        "SELECT COUNT(*) AS total FROM billing_request_checks WHERE admin_site_id = ? AND status = ?",
        (int(adminSiteId), status),
    )
    return int((row or {}).get("total") or 0)


def listUsedUpstreamLogIds(adminSiteId: int) -> set:
    """已被历史核对占用的上游日志 id（一条上游日志只允许配一条主站记录）。"""
    rows = db_query_all(
        """
        SELECT upstream_log_id FROM billing_request_checks
        WHERE admin_site_id = ? AND upstream_log_id IS NOT NULL
        """,
        (int(adminSiteId),),
    )
    return {int(r["upstream_log_id"]) for r in rows}


def getBillingMetaString(name: str) -> Optional[str]:
    row = db_query_one(
        "SELECT value FROM app_settings WHERE name = ?",
        (SETTINGS_PREFIX + name,),
    )
    value = str((row or {}).get("value") or "")
    return value or None


def updateBillingCheckResult(rowId: int, patch: Dict[str, Any]) -> None:
    """把匹配与判定结果写回核对行；patch 只允许已知列，防拼写错误落库。"""
    allowed = {
        "channel_id", "channel_name", "upstream_site_id", "upstream_site_name",
        "main_usd",
        "official_usd", "official_input_usd_per_m", "official_cached_input_usd_per_m",
        "official_cache_write_usd_per_m", "official_output_usd_per_m",
        "upstream_expected_usd", "upstream_actual_usd", "upstream_log_id",
        "upstream_model_ratio", "upstream_group_ratio", "upstream_completion_ratio",
        "published_model_ratio", "published_group_ratio", "published_completion_ratio",
        "match_status", "status", "reason_codes_json",
        "main_other_json", "upstream_other_json", "checked_at",
    }
    fields: List[str] = []
    params: List[Any] = []
    for key, value in patch.items():
        if key not in allowed:
            continue
        fields.append(f"{key} = ?")
        params.append(value)
    if not fields:
        return
    fields.append("updated_at = ?")
    params.append(utc_now_iso())
    params.append(int(rowId))
    db_execute(
        f"UPDATE billing_request_checks SET {', '.join(fields)} WHERE id = ?",
        tuple(params),
    )


def listBillingChecksPayload(
    page: int,
    pageSize: int,
    statusFilter: Optional[str],
    adminSiteId: Optional[int],
    upstreamSiteId: Optional[int],
    channelId: Optional[int],
    model: Optional[str],
    startAt: Optional[str],
    endAt: Optional[str],
) -> Tuple[List[Dict[str, Any]], int]:
    conditions: List[str] = []
    params: List[Any] = []
    if statusFilter:
        conditions.append("status = ?")
        params.append(statusFilter)
    if adminSiteId:
        conditions.append("admin_site_id = ?")
        params.append(int(adminSiteId))
    if upstreamSiteId:
        conditions.append("upstream_site_id = ?")
        params.append(int(upstreamSiteId))
    if channelId:
        conditions.append("channel_id = ?")
        params.append(int(channelId))
    if model:
        conditions.append("model_name = ?")
        params.append(model)
    if startAt:
        conditions.append("request_at >= ?")
        params.append(startAt)
    if endAt:
        conditions.append("request_at <= ?")
        params.append(endAt)
    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    total_row = db_query_one(
        f"SELECT COUNT(*) AS total FROM billing_request_checks {where}", tuple(params)
    )
    total = int((total_row or {}).get("total") or 0)
    offset = max(0, (int(page) - 1)) * int(pageSize)
    rows = db_query_all(
        f"""
        SELECT {BILLING_CHECK_COLUMNS} FROM billing_request_checks {where}
        ORDER BY request_at DESC, id DESC
        LIMIT ? OFFSET ?
        """,
        tuple(params) + (int(pageSize), offset),
    )
    return rows, total


def getBillingCheckById(checkId: int) -> Optional[Dict[str, Any]]:
    return db_query_one(
        f"SELECT {BILLING_CHECK_COLUMNS} FROM billing_request_checks WHERE id = ?",
        (int(checkId),),
    )


def overviewBillingChecks(
    adminSiteId: Optional[int],
    upstreamSiteId: Optional[int],
    channelId: Optional[int],
    model: Optional[str],
    startAt: Optional[str],
    endAt: Optional[str],
) -> Dict[str, Any]:
    conditions: List[str] = []
    params: List[Any] = []
    if adminSiteId:
        conditions.append("admin_site_id = ?")
        params.append(int(adminSiteId))
    if upstreamSiteId:
        conditions.append("upstream_site_id = ?")
        params.append(int(upstreamSiteId))
    if channelId:
        conditions.append("channel_id = ?")
        params.append(int(channelId))
    if model:
        conditions.append("model_name = ?")
        params.append(model)
    if startAt:
        conditions.append("request_at >= ?")
        params.append(startAt)
    if endAt:
        conditions.append("request_at <= ?")
        params.append(endAt)
    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    row = db_query_one(
        f"""
        SELECT
          COUNT(*) AS total_count,
          SUM(status = 'ok') AS ok_count,
          SUM(status = 'mismatch') AS mismatch_count,
          SUM(status = 'unknown') AS unknown_count,
          COALESCE(SUM(main_usd), 0) AS main_usd_total,
          COALESCE(SUM(upstream_actual_usd), 0) AS upstream_usd_total,
          COALESCE(SUM(official_usd), 0) AS official_usd_total
        FROM billing_request_checks {where}
        """,
        tuple(params),
    ) or {}
    models = db_query_all(
        f"""
        SELECT model_name, COUNT(*) AS total_count,
               SUM(status = 'mismatch') AS mismatch_count
        FROM billing_request_checks {where}
        GROUP BY model_name
        ORDER BY mismatch_count DESC, total_count DESC
        LIMIT 20
        """,
        tuple(params),
    )
    # 原因码是 JSON 数组列，分布统计在 Python 里做；只取时间范围内的红/灰行，
    # 上限 20000 行防止全表扫内存。
    reasonConditions = conditions + ["reason_codes_json IS NOT NULL"]
    reason_rows = db_query_all(
        f"""
        SELECT reason_codes_json FROM billing_request_checks
        WHERE {' AND '.join(reasonConditions)}
        ORDER BY id DESC
        LIMIT 20000
        """,
        tuple(params),
    )
    reasons: Dict[str, int] = {}
    for reason_row in reason_rows:
        try:
            codes = json.loads(reason_row.get("reason_codes_json") or "[]")
        except ValueError:
            continue
        for code in codes if isinstance(codes, list) else []:
            reasons[str(code)] = reasons.get(str(code), 0) + 1

    def usdTotal(key: str) -> float:
        return round(float(row.get(key) or 0.0), 8)

    return {
        "total_count": int(row.get("total_count") or 0),
        "ok_count": int(row.get("ok_count") or 0),
        "mismatch_count": int(row.get("mismatch_count") or 0),
        "unknown_count": int(row.get("unknown_count") or 0),
        "main_usd_total": usdTotal("main_usd_total"),
        "upstream_usd_total": usdTotal("upstream_usd_total"),
        "official_usd_total": usdTotal("official_usd_total"),
        "margin_usd_total": round(usdTotal("main_usd_total") - usdTotal("upstream_usd_total"), 8),
        "reason_breakdown": [
            {"code": code, "count": count}
            for code, count in sorted(reasons.items(), key=lambda kv: -kv[1])
        ],
        "model_breakdown": [
            {
                "model_name": m.get("model_name"),
                "total_count": int(m.get("total_count") or 0),
                "mismatch_count": int(m.get("mismatch_count") or 0),
            }
            for m in models
        ],
    }


def pruneBillingChecks(retentionDays: int) -> int:
    """按保留天数删旧行（0 = 永久）；返回删除行数。"""
    if retentionDays <= 0:
        return 0
    cutoff = (app_now() - timedelta(days=int(retentionDays))).isoformat(timespec="seconds")
    return db_execute_rowcount(
        "DELETE FROM billing_request_checks WHERE request_at < ?", (cutoff,)
    )
