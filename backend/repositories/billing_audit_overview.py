"""Billing-audit overview aggregations (dashboard KPI breakdowns).

对应设计稿 ``docs/billing-audit/billing-audit-方案.md`` 第 6 节 overview 端点。
这里只做只读聚合（SQL + Python 汇总），行级 CRUD 与设置仍归
``repositories/billing_audit.py``；``applyReasonCodeCondition`` 是两个模块
共用的原因码过滤助手。
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from backend.core.time import APP_TIMEZONE
from backend.db.connection import db_query_all, db_query_one


def _topReasonCode(reasonMap: Dict[str, int]) -> Optional[str]:
    """Most frequent reason code; counts mix red and gray reasons (rows of
    either status carry reason_codes), so callers must not assume red-only."""
    if not reasonMap:
        return None
    return sorted(reasonMap.items(), key=lambda kv: -kv[1])[0][0]


def applyReasonCodeCondition(
    conditions: List[str],
    params: List[Any],
    reasonCode: Optional[str],
) -> Tuple[List[str], List[Any]]:
    """Filter by red/gray reason code: reason_codes_json is a JSON array, a
    quoted-LIKE catches any member.

    Codes are joined with "," (compact json.dumps), so quoting both sides
    avoids prefix collisions (``no_binding`` vs ``binding_x``). The whitelist
    allows only alphanumerics and ``_``; ``_`` is escaped because LIKE treats
    it as a single-char wildcard (relies on MySQL's default backslash escape,
    i.e. NO_BACKSLASH_ESCAPES must stay off).
    """
    code = (reasonCode or "").strip()
    if not code:
        return conditions, params
    if not code.replace("_", "").isalnum():
        raise ValueError("reason_code 格式不合法")
    conditions.append(
        "(reason_codes_json LIKE ? OR reason_codes_json LIKE ?)"
    )
    likeInner = f'%"{code.replace("_", chr(92) + "_")}"%'
    params.extend([likeInner, likeInner])
    return conditions, params


def overviewBillingChecks(
    adminSiteId: Optional[int],
    upstreamSiteId: Optional[int],
    channelId: Optional[int],
    model: Optional[str],
    startAt: Optional[str],
    endAt: Optional[str],
    bucketMinutes: int = 5,
    reasonCode: Optional[str] = None,
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
    conditions, params = applyReasonCodeCondition(conditions, params, reasonCode)
    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    row = db_query_one(
        f"""
        SELECT
          COUNT(*) AS total_count,
          SUM(status = 'ok') AS ok_count,
          SUM(status = 'mismatch') AS mismatch_count,
          SUM(status = 'unknown') AS unknown_count,
          COALESCE(SUM(main_usd), 0) AS main_usd_total,
          COALESCE(SUM(upstream_actual_usd), 0) AS upstream_usd_total
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
        SELECT reason_codes_json, upstream_site_id FROM billing_request_checks
        WHERE {' AND '.join(reasonConditions)}
        ORDER BY id DESC
        LIMIT 20000
        """,
        tuple(params),
    )
    reasons: Dict[str, int] = {}
    upstreamReasons: Dict[int, Dict[str, int]] = {}
    for reason_row in reason_rows:
        try:
            codes = json.loads(reason_row.get("reason_codes_json") or "[]")
        except ValueError:
            continue
        for code in codes if isinstance(codes, list) else []:
            code = str(code)
            reasons[code] = reasons.get(code, 0) + 1
            rowSiteId = reason_row.get("upstream_site_id")
            if rowSiteId is not None:
                siteKey = int(rowSiteId)
                siteReasons = upstreamReasons.setdefault(siteKey, {})
                siteReasons[code] = siteReasons.get(code, 0) + 1

    upstreams = db_query_all(
        f"""
        SELECT
          upstream_site_id,
          MAX(upstream_site_name) AS upstream_site_name,
          COUNT(*) AS total_count,
          SUM(status = 'ok') AS ok_count,
          SUM(status = 'mismatch') AS mismatch_count,
          SUM(status = 'unknown') AS unknown_count,
          COALESCE(SUM(main_usd), 0) AS main_usd_total,
          COALESCE(SUM(upstream_actual_usd), 0) AS upstream_usd_total
        FROM billing_request_checks {where}
        GROUP BY upstream_site_id
        ORDER BY mismatch_count DESC, total_count DESC
        LIMIT 20
        """,
        tuple(params),
    )
    safeBucket = min(1440, max(1, int(bucketMinutes)))
    buckets = db_query_all(
        f"""
        SELECT
          FLOOR(UNIX_TIMESTAMP(request_at) / ?) * ? AS bucket_epoch,
          COUNT(*) AS total_count,
          SUM(status = 'ok') AS ok_count,
          SUM(status = 'mismatch') AS mismatch_count,
          SUM(status = 'unknown') AS unknown_count,
          COALESCE(SUM(main_usd), 0) - COALESCE(SUM(upstream_actual_usd), 0) AS margin_usd
        FROM billing_request_checks {where}
        GROUP BY bucket_epoch
        ORDER BY bucket_epoch DESC
        LIMIT 400
        """,
        # SELECT 里的两个桶参数在 WHERE 条件参数之前绑定
        (safeBucket * 60, safeBucket * 60) + tuple(params),
    )
    # DESC + LIMIT 保住最新桶（老数据可截断），返回前翻回时间升序；
    # 脏 request_at 会让 UNIX_TIMESTAMP 产出 NULL 桶，直接跳过
    buckets.reverse()
    buckets = [b for b in buckets if b.get("bucket_epoch") is not None]

    def usdTotal(key: str) -> float:
        return round(float(row.get(key) or 0.0), 8)

    return {
        "total_count": int(row.get("total_count") or 0),
        "ok_count": int(row.get("ok_count") or 0),
        "mismatch_count": int(row.get("mismatch_count") or 0),
        "unknown_count": int(row.get("unknown_count") or 0),
        "main_usd_total": usdTotal("main_usd_total"),
        "upstream_usd_total": usdTotal("upstream_usd_total"),
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
        "upstream_breakdown": [
            {
                "upstream_site_id": (
                    int(u["upstream_site_id"]) if u.get("upstream_site_id") is not None else None
                ),
                "upstream_site_name": u.get("upstream_site_name") or None,
                "total_count": int(u.get("total_count") or 0),
                "ok_count": int(u.get("ok_count") or 0),
                "mismatch_count": int(u.get("mismatch_count") or 0),
                "unknown_count": int(u.get("unknown_count") or 0),
                "margin_usd": round(
                    float(u.get("main_usd_total") or 0.0)
                    - float(u.get("upstream_usd_total") or 0.0),
                    8,
                ),
                "top_reason_code": _topReasonCode(
                    upstreamReasons.get(
                        int(u["upstream_site_id"]) if u.get("upstream_site_id") is not None else -1,
                        {},
                    )
                ),
            }
            for u in upstreams
        ],
        "time_buckets": [
            {
                # epoch 由 Python 按 APP_TIMEZONE 格式化，不依赖 MySQL 会话时区；
                # 桶沿按 epoch 对齐（1440 分钟桶的起点是本地 08:00 而非自然日 00:00）
                "bucket_start_at": datetime.fromtimestamp(
                    int(b["bucket_epoch"]), tz=APP_TIMEZONE
                ).isoformat(timespec="seconds"),
                "total_count": int(b.get("total_count") or 0),
                "ok_count": int(b.get("ok_count") or 0),
                "mismatch_count": int(b.get("mismatch_count") or 0),
                "unknown_count": int(b.get("unknown_count") or 0),
                "margin_usd": round(float(b.get("margin_usd") or 0.0), 8),
            }
            for b in buckets
        ],
    }
