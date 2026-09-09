"""sub2api usage-log client for billing audit (upstream side only).

计费核对 P2：sub2api 上游的消费日志读取。对应设计稿
``docs/billing-audit/billing-audit-方案.md`` 第 12 节 P2 条目；接口为
``GET /api/v1/usage``（Bearer access_token，``page`` 1 基 / ``limit`` 分页，
时间倒序，实测 limit 上限 500）。字段归一与
``integrations/newapi_logs.normalizeConsumeLog`` 对齐（同名键同语义），
让 service 层的匹配/判定按平台无感分支；登录态自愈
（access_token → refresh_token 轮换 → 密码兜底）复用
``integrations.sub2api`` 的统一执行器。本模块只读取，不写任何上游数据。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from backend.core.normalize import normalize_base_url
from backend.integrations.http import request_json
from backend.integrations.sub2api import (
    sub2api_token_headers,
    unwrap_sub2api_response,
    _fetch_sub2api_with_auth_fallback,
)

# usage 单页上限实测 500：单页越大，时间窗覆盖所需的翻页请求越少
USAGE_PAGE_SIZE = 500


def _intOrNone(value: Any) -> Optional[int]:
    if isinstance(value, (int, float)):
        return int(value)
    return None


def _firstFloat(*values: Any) -> Optional[float]:
    for value in values:
        if isinstance(value, (int, float)):
            return float(value)
    return None


def parseSub2apiUsageTime(value: Any) -> Optional[datetime]:
    """``created_at`` 是带微秒与时区偏移的 ISO 串（如
    ``2026-09-09T00:24:21.756151+08:00``）；解析失败返回 None。"""
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return datetime.fromisoformat(value.strip())
    except ValueError:
        return None


def sanitizeSub2apiUsageRaw(log: Dict[str, Any]) -> Dict[str, Any]:
    """给 upstream_other_json 落库用的证据快照：剥离密钥与个人信息。

    usage 原始行内嵌 ``api_key``（含明文 key）、``user``（邮箱/余额）等，
    与 AGENTS.md 密钥纪律冲突——落库证据只保留计费判定相关字段。
    """
    group = log.get("group") if isinstance(log.get("group"), dict) else {}
    return {
        "id": log.get("id"),
        "request_id": log.get("request_id"),
        "model": log.get("model"),
        "inbound_endpoint": log.get("inbound_endpoint"),
        "group_id": log.get("group_id"),
        "group_name": group.get("name"),
        "group_rate_multiplier": group.get("rate_multiplier"),
        "rate_multiplier": log.get("rate_multiplier"),
        "input_tokens": log.get("input_tokens"),
        "output_tokens": log.get("output_tokens"),
        "cache_read_tokens": log.get("cache_read_tokens"),
        "cache_creation_tokens": log.get("cache_creation_tokens"),
        "input_cost": log.get("input_cost"),
        "output_cost": log.get("output_cost"),
        "cache_read_cost": log.get("cache_read_cost"),
        "cache_creation_cost": log.get("cache_creation_cost"),
        "total_cost": log.get("total_cost"),
        "actual_cost": log.get("actual_cost"),
        "billing_mode": log.get("billing_mode"),
        "billing_type": log.get("billing_type"),
        "stream": log.get("stream"),
        "duration_ms": log.get("duration_ms"),
        "api_key_id": log.get("api_key_id"),
        "created_at": log.get("created_at"),
    }


def normalizeSub2apiUsageLog(log: Dict[str, Any]) -> Dict[str, Any]:
    """把一条 usage 记录归一成与 newapi_logs.normalizeConsumeLog 相同的键。

    sub2api 直接给美元金额（无 quota 概念）：``quota`` 固定 0、``usd`` 承载
    实扣美元；倍率证据放 ``group_ratio``（usage 行的 rate_multiplier = 该
    请求实际生效的分组倍率），model/completion 倍率上游不下发、置 None。
    ``other`` 存脱敏快照，供 upstream_other_json 落库与详情页展示。
    """
    actualUsd = _firstFloat(log.get("actual_cost"), log.get("total_cost"))
    rateMultiplier = _firstFloat(log.get("rate_multiplier"))
    group = log.get("group") if isinstance(log.get("group"), dict) else {}
    return {
        "id": log.get("id"),
        "created_at": log.get("created_at"),
        "created_iso": parseSub2apiUsageTime(log.get("created_at")),
        "model_name": str(log.get("model") or "").strip(),
        "quota": 0,
        "usd": actualUsd,
        "channel": None,
        "token_name": str(log.get("api_key_name") or "") or None,
        "api_key_id": log.get("api_key_id"),
        "prompt_tokens": int(log.get("input_tokens") or 0),
        "completion_tokens": int(log.get("output_tokens") or 0),
        "cache_read_tokens": _intOrNone(log.get("cache_read_tokens")),
        "cache_creation_tokens": _intOrNone(log.get("cache_creation_tokens")),
        "model_ratio": None,
        "group_ratio": rateMultiplier,
        "completion_ratio": None,
        "cache_ratio": None,
        "user_group": str(group.get("name") or "") or None,
        "other": sanitizeSub2apiUsageRaw(log),
    }


def _log_list_items(payload: Any) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, dict) or not isinstance(data.get("items"), list):
        return [], {}
    meta = {
        key: data[key]
        for key in ("total", "page", "page_size", "pages")
        if key in data
    }
    return [x for x in data["items"] if isinstance(x, dict)], meta


def fetchSub2apiUsageLogsByToken(
    base_url: str, access_token: str, page: int, pageSize: int
) -> Tuple[bool, List[Dict[str, Any]], Dict[str, Any], Optional[str]]:
    """上游账号侧消费日志（``GET /api/v1/usage``，时间倒序）。

    ``page`` 0 基（与 newapi_logs 的翻页口径一致），内部转 1 基。
    返回 (ok, logs, meta, error)，meta 带 total / pages。
    """
    token = (access_token or "").strip()
    if not token:
        return False, [], {}, "auth_token 为空"
    headers = sub2api_token_headers(token)
    ok, payload, error = request_json(
        f"{normalize_base_url(base_url)}/api/v1/usage"
        f"?page={max(0, int(page)) + 1}&limit={max(1, int(pageSize))}",
        headers=headers,
    )
    if not ok:
        return False, [], {}, error or "读取 sub2api 用量日志失败"
    success, _data, message = unwrap_sub2api_response(payload)
    if not success:
        return False, [], {}, message or "sub2api 用量日志响应失败"
    items, meta = _log_list_items(payload)
    return True, [normalizeSub2apiUsageLog(item) for item in items], meta, None


def fetchSub2apiUsageLogsWithAuth(
    site: Dict[str, Any], page: int, pageSize: int
) -> Tuple[bool, List[Dict[str, Any]], Dict[str, Any], Optional[str]]:
    """带登录态自愈的 usage 拉取：走 sub2api 统一执行器
    （access_token → refresh 轮换 → 密码兜底），轮换结果自动回写站点行。

    执行器的 fetch_by_token 只接收 (base_url, token)，页码经默认参数
    闭包传入；一次只取一页，窗口不够由 service 层翻页驱动。
    """
    ok, payload, error = _fetch_sub2api_with_auth_fallback(
        lambda base, token, _page=max(0, int(page)), _size=max(1, int(pageSize)):
        _usageByToken(base, token, _page, _size),
        str(site.get("base_url") or ""),
        username=str(site.get("login_username") or ""),
        password=str(site.get("login_password") or ""),
        auth_mode=str(site.get("auth_mode") or "password"),
        access_token=str(site.get("access_token") or ""),
        refresh_token=str(site.get("refresh_token") or ""),
        site_id=int(site.get("id") or 0),
    )
    if not ok:
        return False, [], {}, error or "读取 sub2api 用量日志失败"
    logs = payload.get("logs") if isinstance(payload, dict) else None
    if not isinstance(logs, list):
        return False, [], {}, "sub2api 用量日志响应格式异常"
    return True, logs, {}, None


def _usageByToken(
    base_url: str, access_token: str, page: int, pageSize: int
) -> Tuple[bool, Dict[str, Any], Optional[str]]:
    ok, logs, _meta, error = fetchSub2apiUsageLogsByToken(
        base_url, access_token, page, pageSize
    )
    if not ok:
        return False, {"logs": []}, error
    return True, {"logs": logs}, None
