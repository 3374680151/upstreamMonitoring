"""Pure NewAPI administrator protocol implementation.

This module contains only HTTP protocol details and response normalization. It
does not read or write the local database. Browser-session protected key reads
are intentionally outside this module and are injected by ``clients.py``.
"""

from __future__ import annotations

import json
from typing import Any, Callable, Optional
from urllib.parse import quote, urlparse

from backend.integrations.transport import (
    newapi_auth_headers,
    normalize_base_url,
    request_json,
)


def _dict_payload(payload: Any) -> dict[str, Any]:
    return payload if isinstance(payload, dict) else {"raw": payload}


def _success_error(payload: Any, fallback: str) -> Optional[str]:
    if not isinstance(payload, dict):
        return f"{fallback}响应异常"
    if payload.get("success"):
        return None
    return str(payload.get("message") or f"{fallback} success=false")


def _newapi_channel_list_items(payload: Any) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Normalize both NewAPI list shapes: ``data=[...]`` and ``data.items``."""
    data = payload.get("data") if isinstance(payload, dict) else None
    items: list[dict[str, Any]] = []
    meta: dict[str, Any] = {}
    if isinstance(data, list):
        items = [item for item in data if isinstance(item, dict)]
    elif isinstance(data, dict):
        raw_items = data.get("items")
        if isinstance(raw_items, list):
            items = [item for item in raw_items if isinstance(item, dict)]
        for key in ("total", "page", "page_size"):
            if key in data:
                meta[key] = data[key]
    return items, meta


def parse_channel_list(payload: Any) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Public protocol boundary for normalizing a channel-list response."""
    return _newapi_channel_list_items(payload)


def parse_groups_payload(payload: Any) -> dict[str, dict[str, Any]]:
    """Normalize the NewAPI group map while preserving numeric/text ratios."""
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, dict):
        return {}
    normalized: dict[str, dict[str, Any]] = {}
    for raw_name in sorted(data.keys(), key=lambda value: str(value)):
        name = str(raw_name)
        info = data.get(raw_name)
        if not isinstance(info, dict):
            info = {}
        ratio = info.get("ratio")
        if isinstance(ratio, (int, float)):
            ratio_value: Any = float(ratio)
            ratio_type = "number"
        elif isinstance(ratio, str):
            text = ratio.strip()
            try:
                ratio_value = float(text)
                ratio_type = "number"
            except ValueError:
                ratio_value = text
                ratio_type = "text"
        else:
            ratio_value = ratio
            ratio_type = "text"
        normalized[name] = {
            "ratio": ratio_value,
            "ratio_type": ratio_type,
            "desc": info.get("desc", ""),
        }
    return normalized


def validate_admin_site_base_url(value: Any) -> tuple[str, Optional[str]]:
    """Validate a management-site origin before any upstream request."""
    normalized = normalize_base_url(str(value or ""))
    if not normalized:
        return "", "请填写主站 Base URL"
    parsed = urlparse(normalized)
    try:
        parsed.port
    except (TypeError, ValueError):
        return "", "主站 Base URL 无效"
    if (
        parsed.scheme.lower() not in {"http", "https"}
        or not parsed.netloc
        or not parsed.hostname
        or parsed.username
        or parsed.password
    ):
        return "", "主站 Base URL 必须是 http/https 地址且不能包含账号密码"
    return normalized, None


def auth_failure_message(payload: Any, error: Optional[str] = None) -> str:
    """Keep NewAPI auth failures actionable without echoing credentials."""
    if isinstance(payload, dict):
        raw_message = str(payload.get("message") or payload.get("error") or "").strip()
        if not raw_message and isinstance(payload.get("raw"), str):
            try:
                raw_json = json.loads(payload["raw"])
            except (TypeError, ValueError):
                raw_json = {}
            if isinstance(raw_json, dict):
                raw_message = str(
                    raw_json.get("message") or raw_json.get("error") or ""
                ).strip()
    else:
        raw_message = ""
    raw_message = raw_message or str(error or "").strip()
    text = f"{raw_message} {error or ''}".lower()
    if "invalid access token" in text or "access token invalid" in text:
        return "令牌无效或已失效，请重新生成并录入普通用户系统访问令牌"
    if "invalid username" in text or "invalid password" in text or "password incorrect" in text:
        return "用户名或密码错误"
    if "require_2fa" in text or "2fa" in text or "two-factor" in text:
        return "需要 2FA 验证码"
    if "connection reset by peer" in text:
        return "上游重置了 Python 连接，已尝试兼容传输；如仍失败请改用浏览器登录态"
    return raw_message or "上游认证失败"


def aggregate_channel_candidates(channels: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Group channels by a safe, normalized upstream URL for discovery."""
    from urllib.parse import urlparse

    grouped: dict[str, dict[str, Any]] = {}
    for channel in channels or []:
        if not isinstance(channel, dict):
            continue
        try:
            channel_id = int(channel.get("id"))
        except (TypeError, ValueError):
            continue
        if channel_id <= 0:
            continue
        base_url = normalize_base_url(str(channel.get("base_url") or ""))
        if not base_url:
            continue
        try:
            parsed = urlparse(base_url)
            parsed.port
        except (TypeError, ValueError):
            continue
        if (
            parsed.scheme.lower() not in {"http", "https"}
            or not parsed.netloc
            or not parsed.hostname
            or parsed.username
            or parsed.password
        ):
            continue
        item = grouped.setdefault(
            base_url,
            {"base_url": base_url, "name": "", "channel_ids": [], "channel_names": []},
        )
        channel_name = str(channel.get("name") or "").strip()
        if channel_name and not item["name"]:
            item["name"] = channel_name
        if channel_id not in item["channel_ids"]:
            item["channel_ids"].append(channel_id)
            item["channel_names"].append(channel_name)
        elif channel_name:
            index = item["channel_ids"].index(channel_id)
            if not item["channel_names"][index]:
                item["channel_names"][index] = channel_name
    result: list[dict[str, Any]] = []
    for item in grouped.values():
        candidate = dict(item)
        candidate["name"] = candidate["name"] or candidate["base_url"]
        candidate["channel_count"] = len(candidate["channel_ids"])
        result.append(candidate)
    return result


class NewApiAdminProtocol:
    """HTTP-only NewAPI admin client.

    ``key_reader`` is an explicit escape hatch for the protected key endpoint;
    it may be supplied by a session-aware adapter but is never used by normal
    channel CRUD operations.
    """

    def __init__(
        self,
        base_url: str = "",
        access_token: str = "",
        access_user_id: str = "",
        site: Optional[dict[str, Any]] = None,
        key_reader: Optional[Callable[[dict[str, Any], int, bool], tuple[bool, str, Optional[str]]]] = None,
    ) -> None:
        source = dict(site or {})
        self.base_url = normalize_base_url(source.get("base_url") or base_url or "")
        self.access_token = str(source.get("access_token") or access_token or "")
        self.access_user_id = str(source.get("access_user_id") or access_user_id or "")
        self.site = {
            **source,
            "base_url": self.base_url,
            "access_token": self.access_token,
            "access_user_id": self.access_user_id,
        }
        self.key_reader = key_reader

    def _headers(self) -> dict[str, str]:
        headers = newapi_auth_headers(self.access_token, self.access_user_id)
        proof = str(self.site.get("security_proof") or "").strip()
        if proof:
            headers["X-Security-Proof"] = proof
        return headers

    def _request(
        self,
        path: str,
        *,
        method: str = "GET",
        payload: Any = None,
    ) -> tuple[bool, Any, Optional[str]]:
        return request_json(
            f"{self.base_url}{path}",
            headers=self._headers(),
            payload=payload,
            method=method,
            admin=True,
        )

    def list_channels(
        self, page: int = 0, page_size: int = 100, keyword: str = ""
    ) -> tuple[bool, dict[str, Any], Optional[str]]:
        keyword = str(keyword or "").strip()
        if keyword:
            path = f"/api/channel/search?keyword={quote(keyword, safe='')}"
        else:
            path = f"/api/channel/?p={int(page)}&page_size={int(page_size)}"
        ok, payload, error = self._request(path)
        if not ok:
            return False, _dict_payload(payload), error or "读取渠道列表失败"
        response_error = _success_error(payload, "渠道列表")
        if response_error:
            return False, _dict_payload(payload), response_error
        return True, _dict_payload(payload), None

    def list_all_channels(
        self, page_size: int = 100, max_pages: int = 50
    ) -> tuple[bool, list[dict[str, Any]], Optional[str]]:
        all_items: list[dict[str, Any]] = []
        expected_total: Optional[int] = None
        for page in range(max(1, int(max_pages))):
            ok, payload, error = self.list_channels(page, page_size)
            if not ok:
                return False, [], error or "读取 NewAPI 渠道分页失败"
            items, meta = _newapi_channel_list_items(payload)
            raw_data = payload.get("data") if isinstance(payload, dict) else None
            if not isinstance(raw_data, list) and not (
                isinstance(raw_data, dict) and isinstance(raw_data.get("items"), list)
            ):
                return False, [], "NewAPI 渠道响应格式无效，拒绝返回截断数据"
            raw_items = raw_data if isinstance(raw_data, list) else raw_data.get("items") or []
            if any(not isinstance(item, dict) for item in raw_items):
                return False, [], "NewAPI 渠道响应包含无效项，拒绝返回截断数据"
            if meta.get("total") is not None:
                try:
                    expected_total = max(0, int(meta.get("total") or 0))
                except (TypeError, ValueError):
                    return False, [], "NewAPI 渠道总数无效，拒绝返回截断数据"
            all_items.extend(items)
            if expected_total is not None:
                if len(all_items) >= expected_total:
                    return True, all_items, None
                if not items:
                    return False, [], "NewAPI 渠道分页提前结束，拒绝返回截断数据"
            elif len(items) < int(page_size):
                return True, all_items, None
        return False, [], f"NewAPI 渠道超过最大分页页数 {max_pages}，拒绝返回截断数据"

    def get_channel(self, channel_id: int) -> tuple[bool, dict[str, Any], Optional[str]]:
        ok, payload, error = self._request(f"/api/channel/{int(channel_id)}")
        if not ok:
            return False, _dict_payload(payload), error or "读取渠道详情失败"
        response_error = _success_error(payload, "渠道详情")
        if response_error:
            return False, _dict_payload(payload), response_error
        return True, _dict_payload(payload), None

    def create_channel(
        self, body: dict[str, Any]
    ) -> tuple[bool, dict[str, Any], Optional[str]]:
        envelope = body if "channel" in body and "mode" in body else {"mode": "single", "channel": body}
        ok, payload, error = self._request("/api/channel/", method="POST", payload=envelope)
        if not ok:
            return False, _dict_payload(payload), error or "创建渠道失败"
        response_error = _success_error(payload, "创建渠道")
        if response_error:
            return False, _dict_payload(payload), response_error
        return True, _dict_payload(payload), None

    def set_channel_status(
        self, channel_id: int, status: Any
    ) -> tuple[bool, dict[str, Any], Optional[str]]:
        try:
            wanted = int(status)
        except (TypeError, ValueError):
            return False, {}, f"状态值无效：{status!r}"
        if wanted not in (1, 2):
            return False, {}, f"只支持启用(1)/停用(2)，收到 {wanted}（3=自动停用由上游自行置位）"
        ok, payload, error = self._request(
            f"/api/channel/{int(channel_id)}/status",
            method="POST",
            payload={"status": wanted},
        )
        if not ok:
            return False, _dict_payload(payload), error or "切换渠道状态失败"
        response_error = _success_error(payload, "切换状态")
        if response_error:
            return False, _dict_payload(payload), response_error
        return True, _dict_payload(payload), None

    def update_channel(
        self, channel_id: int, patch: dict[str, Any]
    ) -> tuple[bool, dict[str, Any], Optional[str]]:
        ok, detail, error = self.get_channel(channel_id)
        if not ok:
            return False, detail, error
        current = detail.get("data") if isinstance(detail, dict) else None
        if not isinstance(current, dict):
            return False, detail, "渠道详情缺少 data，无法合并更新"
        allowed = {
            "name", "status", "weight", "priority", "group", "groups", "models",
            "base_url", "key", "type", "model_mapping", "tag", "test_model",
            "auto_ban", "other", "setting", "settings", "param_override",
            "status_code_mapping", "header_override", "remark", "openai_organization",
        }
        merged = dict(current)
        for field, value in patch.items():
            if field in allowed:
                merged[field] = value
        merged["id"] = int(channel_id)
        for field in (
            "balance", "balance_updated_time", "used_quota", "created_time",
            "test_time", "response_time", "other_info", "channel_info",
        ):
            merged.pop(field, None)
        status_requested = merged.pop("status", None) if "status" in patch else None
        merged.pop("status", None)
        if not str(merged.get("key") or "").strip():
            merged.pop("key", None)
        other_fields = {key for key in patch if key in allowed and key != "status"}
        status_payload: Optional[dict[str, Any]] = None
        if status_requested is not None:
            ok, status_payload, error = self.set_channel_status(channel_id, status_requested)
            if not ok:
                return False, status_payload or {}, error
            if not other_fields:
                return True, status_payload or {}, None
        ok, payload, error = self._request("/api/channel/", method="PUT", payload=merged)
        if not ok:
            return False, _dict_payload(payload), error or "更新渠道失败"
        response_error = _success_error(payload, "更新渠道")
        if response_error:
            return False, _dict_payload(payload), response_error
        return True, _dict_payload(payload), None

    def delete_channel(
        self, channel_id: int
    ) -> tuple[bool, dict[str, Any], Optional[str]]:
        ok, payload, error = self._request(f"/api/channel/{int(channel_id)}", method="DELETE")
        if not ok:
            return False, _dict_payload(payload), error or "删除渠道失败"
        response_error = _success_error(payload, "删除渠道")
        if response_error:
            return False, _dict_payload(payload), response_error
        return True, _dict_payload(payload), None

    def batch_channel(
        self, action: str, ids: list[int], params: dict[str, Any]
    ) -> tuple[bool, dict[str, Any], Optional[str]]:
        actions = {"enable", "disable", "delete", "set_group", "set_tag", "add_tag"}
        action = str(action or "").strip()
        if action not in actions:
            return False, {}, f"不支持的批量操作：{action or '(空)'}"
        if not isinstance(ids, list) or not ids:
            return False, {}, "未选择任何渠道"
        group_value = str(params.get("group") or "").strip()
        tag_value = str(params.get("tag") or "").strip()
        if action == "set_group" and not group_value:
            return False, {}, "请提供要设置的分组名"
        if action in {"set_tag", "add_tag"} and not tag_value:
            return False, {}, "请提供要设置的标签"
        results: list[dict[str, Any]] = []
        ok_count = 0
        for raw_id in ids:
            try:
                channel_id = int(raw_id)
            except (TypeError, ValueError):
                results.append({"id": raw_id, "ok": False, "message": "无效渠道 ID"})
                continue
            if action == "delete":
                ok, _payload, error = self.delete_channel(channel_id)
            else:
                patch: dict[str, Any] = {}
                if action == "enable":
                    patch["status"] = 1
                elif action == "disable":
                    patch["status"] = 2
                elif action == "set_group":
                    patch["group"] = group_value
                else:
                    patch["tag"] = tag_value
                ok, _payload, error = self.update_channel(channel_id, patch)
            if ok:
                ok_count += 1
            results.append({"id": channel_id, "ok": ok, "message": None if ok else error})
        return True, {
            "action": action,
            "ok_count": ok_count,
            "fail_count": len(results) - ok_count,
            "total": len(results),
            "results": results,
        }, None

    def test_channel(
        self, channel_id: int
    ) -> tuple[bool, dict[str, Any], Optional[str]]:
        ok, payload, error = self._request(f"/api/channel/test/{int(channel_id)}")
        if not ok:
            return False, _dict_payload(payload), error or "测试渠道失败"
        return True, _dict_payload(payload), None

    def list_groups(self) -> tuple[bool, dict[str, Any], Optional[str]]:
        errors: list[str] = []
        for path in ("/api/user/self/groups", "/api/user/groups"):
            ok, payload, error = self._request(path)
            if ok and isinstance(payload, dict) and payload.get("success"):
                return True, {"success": True, "data": parse_groups_payload(payload)}, None
            errors.append(f"{path}: {auth_failure_message(payload, error or 'success=false')}")
        return False, {"errors": errors}, "访问令牌分组采集失败：" + "；".join(errors)

    def test_connection(self) -> tuple[bool, dict[str, Any], Optional[str]]:
        ok, payload, error = self.list_groups()
        if not ok:
            return False, payload, error
        return True, {"groups_count": len(payload.get("data") or {})}, None

    def get_channel_key(
        self, channel_id: int, force_refresh: bool = False
    ) -> tuple[bool, str, Optional[str]]:
        if self.key_reader is None:
            return False, "", "NewAPI 渠道 key 读取需要注入 session adapter"
        return self.key_reader(self.site, int(channel_id), bool(force_refresh))

    def channel_candidates(
        self,
        keyword: str = "",
        enricher: Optional[Callable[[list[dict[str, Any]]], list[dict[str, Any]]]] = None,
    ) -> tuple[bool, list[dict[str, Any]], dict[str, Any], Optional[str]]:
        ok, channels, error = self.list_all_channels()
        if not ok:
            return False, [], {"source_channel_total": 0}, error
        candidates = aggregate_channel_candidates(channels)
        if enricher is not None:
            candidates = enricher(candidates)
        lowered = str(keyword or "").strip().casefold()
        if lowered:
            candidates = [
                item
                for item in candidates
                if lowered
                in " ".join(
                    [
                        str(item.get("base_url") or ""),
                        str(item.get("name") or ""),
                        " ".join(str(name or "") for name in item.get("channel_names") or []),
                    ]
                ).casefold()
            ]
        return True, candidates, {"total": len(candidates), "source_channel_total": len(channels)}, None


__all__ = [
    "NewApiAdminProtocol",
    "aggregate_channel_candidates",
    "auth_failure_message",
    "parse_channel_list",
    "parse_groups_payload",
    "validate_admin_site_base_url",
]
