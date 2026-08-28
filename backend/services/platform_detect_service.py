"""上游站点平台识别服务。

根据上游 API 的响应特征判定 NewAPI / sub2api 平台类型，供主站同步导入
过滤复用。识别结果带进程内缓存；探测命中时回写监控站点记录，下次零探测。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from cachetools import TTLCache

from backend.core.normalize import normalize_base_url
from backend.core.time import utc_now_iso
from backend.db.connection import db_execute
from backend.integrations.http import (
    UpstreamHttpStatusError,
    send_upstream_request,
)
from backend.integrations.newapi import fetch_newapi_groups
from backend.repositories.sites import find_monitor_site_for_channel


class PlatformDetectService:
    """上游站点平台类型（NewAPI / sub2api）识别。"""

    # 同域名平台探测结果缓存，避免同步时每个渠道重复发 HTTP 探测
    _PLATFORM_DETECT_CACHE: "TTLCache[str, str]" = TTLCache(maxsize=1024, ttl=3600)

    def detect_platform(self, base_url: str) -> str:
        """探测上游站点平台类型（带进程内缓存）。"""
        normalized = normalize_base_url(base_url)
        if not normalized:
            return "unknown"
        cached = self._PLATFORM_DETECT_CACHE.get(normalized)
        if cached:
            return cached
        result = self._detect_platform_no_cache(normalized)
        self._PLATFORM_DETECT_CACHE[normalized] = result
        return result

    def _detect_platform_no_cache(self, normalized: str) -> str:
        # 1. 试 NewAPI 公开分组接口
        ok, _payload, _error = fetch_newapi_groups(normalized)
        if ok:
            return "newapi"

        # 2. 试 sub2api 公开端点 /api/v1/auth/me
        if self._looks_like_sub2api(normalized):
            return "sub2api"

        return "unknown"

    def _looks_like_sub2api(self, base_url: str) -> bool:
        """检查 URL 是否像 sub2api 站点。

        sub2api 的 /api/v1/auth/me 不带 token 会返回 401，但响应体
        是 JSON 且包含 sub2api 风格的错误结构（detail / message 字段）。
        如果返回 404，说明不是 sub2api。
        """
        url = f"{base_url}/api/v1/auth/me"
        try:
            resp = send_upstream_request(url, timeout=5)
            # 能无认证访问说明确实是 sub2api（返回了数据）
            return resp.status == 200
        except UpstreamHttpStatusError as exc:
            # 401 = 端点存在但需要认证 → 是 sub2api
            # 404 = 端点不存在 → 不是 sub2api
            return exc.code in (401, 403)
        except Exception:
            return False

    def _resolve_platform(
        self,
        base_url: str,
        batch_cache: Dict[str, str],
        monitor_site: Optional[Dict[str, Any]] = None,
    ) -> str:
        """判定一个 base_url 的平台类型，结果写入批内缓存。

        优先级：批内缓存 → 监控站点 DB 的 platform → HTTP 探测
        （探测命中 newapi/sub2api 时回写站点记录，下次同步零探测）。
        调用方已查出监控站点时直接传入，避免重复查询。
        """
        cached = batch_cache.get(base_url)
        if cached:
            return cached
        if monitor_site is None:
            monitor_site = find_monitor_site_for_channel(base_url)
        site_platform = str((monitor_site or {}).get("platform") or "").strip().lower()
        if site_platform in ("newapi", "sub2api"):
            platform = site_platform
        else:
            platform = self.detect_platform(base_url)
            if platform in ("newapi", "sub2api") and monitor_site:
                self._update_site_platform(int(monitor_site["id"]), platform)
        batch_cache[base_url] = platform
        return platform

    def platforms_for_base_urls(self, base_urls: List[str]) -> Dict[str, str]:
        """批量判定 base_url 平台，供同步过滤复用同一套识别逻辑与缓存。"""
        batch_cache: Dict[str, str] = {}
        result: Dict[str, str] = {}
        for base_url in base_urls or []:
            normalized = str(base_url or "").strip()
            if not normalized or normalized in result:
                continue
            result[normalized] = self._resolve_platform(normalized, batch_cache)
        return result

    def _update_site_platform(self, site_id: int, platform: str) -> None:
        """修正监控站点的平台类型。"""
        try:
            db_execute(
                "UPDATE sites SET platform = ?, updated_at = ? WHERE id = ?",
                (platform, utc_now_iso(), site_id),
            )
            print(f"[平台识别] 修正 site#{site_id} platform -> {platform}", flush=True)
        except Exception as exc:  # noqa: BLE001
            print(f"[平台识别] 修正 site#{site_id} platform 失败：{exc}", flush=True)
