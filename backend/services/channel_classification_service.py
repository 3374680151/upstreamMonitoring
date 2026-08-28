"""渠道分类服务。

同步主站后，根据上游 API 返回的数据格式自动探测平台类型（NewAPI / sub2api），
然后按渠道 group 字段名匹配上游监控站点已有的分组倍率。

不读取需要用户令牌的接口（如 NewAPI /api/token/），仅靠公开分组数据和
渠道配置时的 group 字段做推断匹配。已在精确匹配（matched / matched_partial）
的渠道不会被覆盖。
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional, Tuple

from cachetools import TTLCache

from backend.integrations.http import (
    UpstreamHttpStatusError,
    send_upstream_request,
)
from backend.core.normalize import (
    _normalize_discovery_base_url,
    normalize_base_url,
    split_channel_groups,
)
from backend.core.time import utc_now_iso
from backend.db.connection import db_execute
from backend.integrations.newapi import fetch_newapi_groups
from backend.repositories.sites import find_monitor_site_for_channel, site_groups_from_row
from backend.services.channel_match_service import get_channel_upstream_binding, persist_channel_match
from backend.services.monitoring_service import detect_site


class ChannelClassificationService:
    """同步主站后的渠道分类与倍率推断。"""

    # 同域名平台探测结果缓存，避免同步时每个渠道重复发 HTTP 探测
    _PLATFORM_DETECT_CACHE: "TTLCache[str, str]" = TTLCache(maxsize=1024, ttl=3600)

    # ------------------------------------------------------------------
    # 平台探测：根据上游 API 返回的数据格式判断 NewAPI / sub2api
    # ------------------------------------------------------------------

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

    # ------------------------------------------------------------------
    # 渠道分类主入口
    # ------------------------------------------------------------------

    def classify_channels(
        self,
        admin_site_id: int,
        channels: List[Dict[str, Any]],
        *,
        admin_platform: Optional[str] = None,
    ) -> Dict[str, Any]:
        """同步主站后自动分类渠道。

        Args:
            admin_site_id: 主站 ID
            channels: 主站渠道列表（取自同步快照）
            admin_platform: 已废弃，保留参数仅为兼容旧调用，不再参与平台判断。
                渠道平台一律以监控站点 DB 记录为准，避免主站平台误导 sub2api 站点。
        """
        total = len(channels or [])
        classified = 0
        platform_counts: Dict[str, int] = {"newapi": 0, "sub2api": 0, "unknown": 0}
        matched_count = 0
        # 同批次按 base_url 缓存平台判断，确保同域名在这次同步中只真探一次
        batch_platform_cache: Dict[str, str] = {}

        for channel in channels or []:
            if not isinstance(channel, dict):
                continue
            channel_id = self._positive_channel_id(channel.get("id"))
            if channel_id is None:
                continue

            # 跳过已有精确匹配的渠道
            existing = get_channel_upstream_binding(admin_site_id, channel_id)
            if existing and existing.get("match_status") in ("matched", "matched_partial"):
                classified += 1
                continue

            base_url, _error = _normalize_discovery_base_url(channel.get("base_url"))
            if not base_url:
                platform_counts["unknown"] = platform_counts.get("unknown", 0) + 1
                continue

            # 找同 base_url 的上游监控站点
            monitor_site = find_monitor_site_for_channel(base_url)
            if not monitor_site:
                platform_counts["unknown"] = platform_counts.get("unknown", 0) + 1
                continue

            # 平台推断优先级（全部零 HTTP，除非站点没有平台记录）：
            # 1) 本批次缓存
            # 2) 监控站点 DB 记录的 platform（绝大多数渠道命中这条）
            # 3) 真正的 HTTP 探测（带类级 TTLCache）
            platform = self._resolve_platform(
                base_url, batch_platform_cache, monitor_site=monitor_site
            )
            platform_counts[platform] = platform_counts.get(platform, 0) + 1

            # 取监控站点的分组倍率数据
            upstream_groups = site_groups_from_row(monitor_site)
            if not upstream_groups:
                # 站点从未检测过才触发 detect_site；只要有任一分组缓存就跳过
                try:
                    detect_site(int(monitor_site["id"]))
                    monitor_site = find_monitor_site_for_channel(base_url) or monitor_site
                    upstream_groups = site_groups_from_row(monitor_site)
                except Exception as exc:  # noqa: BLE001
                    print(
                        f"[渠道分类] 自动检测 site#{monitor_site.get('id')} 失败：{exc}",
                        flush=True,
                    )
                if not upstream_groups:
                    continue

            # 取渠道的 group 字段（NewAPI 是逗号分隔字符串，sub2api 可能是数组）
            group_names = self._extract_group_names(channel, platform)
            if not group_names:
                continue

            # 按分组名匹配倍率
            matched_groups = self._match_groups_by_name(group_names, upstream_groups)
            status, message = self._build_match_status(matched_groups, base_url, platform)

            persist_channel_match(
                admin_site_id, channel_id, status, message, matched_groups,
            )
            matched_count += 1
            classified += 1

        result = {
            "total_channels": total,
            "classified": classified,
            "matched": matched_count,
            "platform_counts": platform_counts,
        }
        print(f"[渠道分类] admin_site_id={admin_site_id} {result}", flush=True)
        return result

    # ------------------------------------------------------------------
    # 辅助方法
    # ------------------------------------------------------------------

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

    def platforms_for_base_urls(
        self, base_urls: List[str]
    ) -> Dict[str, str]:
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
            print(f"[渠道分类] 修正 site#{site_id} platform -> {platform}", flush=True)
        except Exception as exc:  # noqa: BLE001
            print(f"[渠道分类] 修正 site#{site_id} platform 失败：{exc}", flush=True)

    def _positive_channel_id(self, value: Any) -> Optional[int]:
        if isinstance(value, bool):
            return None
        try:
            channel_id = int(value)
        except (TypeError, ValueError):
            return None
        return channel_id if channel_id > 0 else None

    def _extract_group_names(
        self,
        channel: Dict[str, Any],
        platform: str,
    ) -> List[str]:
        """从渠道数据中提取分组名。

        NewAPI: ``group`` 字段是逗号分隔字符串（如 "default,vip"）
        sub2api: ``groups`` 字段可能是对象数组，每个对象有 ``name``
        """
        if platform == "sub2api":
            groups = channel.get("groups")
            if isinstance(groups, list):
                names = [
                    str(item.get("name") or "").strip()
                    for item in groups
                    if isinstance(item, dict)
                ]
                return [name for name in names if name]
        # NewAPI 或 unknown：统一用 split_channel_groups
        return split_channel_groups(channel.get("group"))

    def _match_groups_by_name(
        self,
        group_names: List[str],
        upstream_groups: Dict[str, Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        """按分组名匹配上游倍率数据。

        匹配策略（优先级递降）：
        1. 精确匹配（大小写敏感）
        2. 大小写不敏感匹配
        3. 包含关系匹配（渠道分组名包含在上游分组名中，或反过来）
        """
        # 预建小写索引，用于大小写不敏感匹配
        lower_map: Dict[str, str] = {}
        for upstream_name in upstream_groups:
            lower_map[upstream_name.lower()] = upstream_name

        matched: List[Dict[str, Any]] = []
        for name in group_names:
            info = self._find_group_match(name, upstream_groups, lower_map)
            matched.append({
                "name": name,
                "ratio": (info or {}).get("ratio"),
                "ratio_type": (info or {}).get("ratio_type") or "text",
                "desc": (info or {}).get("desc") or "",
                "available_to_login": info is not None,
            })
        return matched

    def _find_group_match(
        self,
        name: str,
        upstream_groups: Dict[str, Dict[str, Any]],
        lower_map: Dict[str, str],
    ) -> Optional[Dict[str, Any]]:
        """逐步降级匹配分组名。"""
        if not name:
            return None
        # 1. 精确匹配
        if name in upstream_groups:
            return upstream_groups[name]
        # 2. 大小写不敏感匹配
        lower_name = name.lower()
        if lower_name in lower_map:
            return upstream_groups[lower_map[lower_name]]
        # 3. 包含关系匹配（双向）
        for upstream_name, info in upstream_groups.items():
            ul = upstream_name.lower()
            nl = lower_name
            if nl in ul or ul in nl:
                return info
        return None

    def _build_match_status(
        self,
        matched_groups: List[Dict[str, Any]],
        base_url: str,
        platform: str,
    ) -> Tuple[str, str]:
        """根据匹配结果生成状态和消息。"""
        found_count = sum(1 for item in matched_groups if item["available_to_login"])
        total = len(matched_groups)
        platform_label = {"newapi": "NewAPI", "sub2api": "sub2api"}.get(platform, "未知")

        if found_count == 0:
            return (
                "inferred_none",
                f"按分组名未匹配到上游分组（{platform_label} · {base_url}）",
            )
        if found_count < total:
            return (
                "inferred_partial",
                f"按分组名部分匹配（{platform_label} · {found_count}/{total}）",
            )
        return (
            "inferred",
            f"按渠道分组名匹配上游倍率（{platform_label} · 非精确匹配）",
        )
