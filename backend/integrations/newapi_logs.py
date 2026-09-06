"""NewAPI request-log clients for billing audit (main site + upstream).

对应设计稿 ``docs/billing-audit/billing-audit-方案.md``：主站走管理员
``GET /api/log/?type=2`` 拿全量消费日志（含 channel id 与 other 倍率快照），
上游走 ``GET /api/log/self?type=2`` 拿渠道账号侧的实际扣费日志。两类日志
的 ``other`` 字段键名随 NewAPI 版本有差异，统一在这里做容错适配；本模块
只负责读取与字段归一，匹配与判定在 ``services/billing_audit_service.py``。
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from backend.integrations.http import request_json
from backend.integrations.newapi import newapi_admin_target, newapi_browser_request

CONSUME_LOG_TYPE = 2


def _log_list_items(payload: Any) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """兼容 NewAPI 日志接口两种返回形态：data=[...] 与 data={items,total,...}。"""
    data = payload.get("data") if isinstance(payload, dict) else None
    meta: Dict[str, Any] = {}
    items: List[Dict[str, Any]] = []
    if isinstance(data, list):
        items = [x for x in data if isinstance(x, dict)]
    elif isinstance(data, dict):
        raw_items = data.get("items")
        if isinstance(raw_items, list):
            items = [x for x in raw_items if isinstance(x, dict)]
        for key in ("total", "page", "page_size"):
            if key in data:
                meta[key] = data[key]
    return items, meta


def parseLogOther(raw: Any) -> Dict[str, Any]:
    """``other`` 列是 JSON 字符串（部分版本直接是 dict），解析失败返回空 dict。"""
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str) and raw.strip():
        try:
            parsed = json.loads(raw)
            return parsed if isinstance(parsed, dict) else {}
        except ValueError:
            return {}
    return {}


def _firstNumber(source: Dict[str, Any], keys: Tuple[str, ...]) -> Optional[float]:
    for key in keys:
        value = source.get(key)
        if isinstance(value, (int, float)):
            return float(value)
        if isinstance(value, str):
            try:
                return float(value)
            except ValueError:
                continue
    return None


def normalizeConsumeLog(log: Dict[str, Any]) -> Dict[str, Any]:
    """把一条主站/上游日志归一成判定所需的字段；取不到的键保持 None。

    缓存 token 的键名在不同 NewAPI 版本里叫法不一（cache_tokens /
    cached_prompt_tokens / prompt_cache_hit_tokens 等），逐个尝试；主站与
    上游共用同一套适配，保证两侧字段语义对齐。
    """
    other = parseLogOther(log.get("other"))
    created_at = log.get("created_at")
    try:
        created_iso: Optional[datetime] = datetime.fromtimestamp(
            float(created_at), tz=timezone.utc
        )
    except (TypeError, ValueError, OSError, OverflowError):
        created_iso = None
    return {
        "id": log.get("id"),
        "created_at": log.get("created_at"),
        "created_iso": created_iso,
        "model_name": str(log.get("model_name") or "").strip(),
        "quota": int(log.get("quota") or 0),
        "channel": log.get("channel"),
        "token_name": str(log.get("token_name") or ""),
        "prompt_tokens": int(log.get("prompt_tokens") or 0),
        "completion_tokens": int(log.get("completion_tokens") or 0),
        "cache_read_tokens": _firstNumber(
            other,
            (
                "cache_tokens",
                "cached_prompt_tokens",
                "prompt_cache_hit_tokens",
                "cache_read_tokens",
            ),
        ),
        "cache_creation_tokens": _firstNumber(
            other,
            (
                "cache_creation_tokens",
                "cache_creation_input_tokens",
                "prompt_cache_miss_tokens",
            ),
        ),
        "model_ratio": _firstNumber(other, ("model_ratio",)),
        "group_ratio": _firstNumber(other, ("group_ratio",)),
        "completion_ratio": _firstNumber(other, ("completion_ratio",)),
        "cache_ratio": _firstNumber(other, ("cache_ratio",)),
        "user_group": str(other.get("user_group") or "") or None,
        "other": other,
    }


def _requireSuccess(payload: Any, fallbackError: str) -> Optional[str]:
    """NewAPI 业务层失败（success=false，如令牌过期）不能当成空页静默吞掉。"""
    if isinstance(payload, dict) and payload.get("success") is False:
        return str(payload.get("message") or fallbackError)
    return None


def fetchNewapiAdminConsumeLogs(
    site: Dict[str, Any], page: int, pageSize: int
) -> Tuple[bool, List[Dict[str, Any]], Dict[str, Any], Optional[str]]:
    """主站管理员消费日志（type=2），返回 (ok, logs, meta, error)。

    翻页从 p=0 起与既有渠道翻页口径一致：新版 NewAPI 把 p=0 钳到第 1 页，
    旧版 new-api/one-api 是 0 基——从 1 起在旧版上会永久跳过最新一页。
    """
    base, headers = newapi_admin_target(site)
    query = f"p={int(page)}&page_size={int(pageSize)}&type={CONSUME_LOG_TYPE}"
    ok, payload, error = request_json(f"{base}/api/log/?{query}", headers=headers)
    if not ok:
        return False, [], {}, error or "读取主站日志失败"
    businessError = _requireSuccess(payload, "主站返回 success=false")
    if businessError:
        return False, [], {}, businessError
    items, meta = _log_list_items(payload)
    return True, [normalizeConsumeLog(item) for item in items], meta, None


def fetchNewapiSelfConsumeLogs(
    upstreamSite: Dict[str, Any], page: int, pageSize: int
) -> Tuple[bool, List[Dict[str, Any]], Dict[str, Any], Optional[str]]:
    """上游渠道账号侧消费日志（type=2），走统一执行器（自带登录态自愈）。"""
    query = f"p={int(page)}&page_size={int(pageSize)}&type={CONSUME_LOG_TYPE}"
    ok, payload, error = newapi_browser_request(
        upstreamSite, "GET", "/api/log/self", query=query
    )
    if not ok:
        return False, [], {}, error or "读取上游日志失败"
    businessError = _requireSuccess(payload, "上游返回 success=false")
    if businessError:
        return False, [], {}, businessError
    items, meta = _log_list_items(payload)
    return True, [normalizeConsumeLog(item) for item in items], meta, None
