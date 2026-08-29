"""Admin-site channel sync service.

Extracted from ``backend.legacy_runtime`` so the FastAPI boundary can import
sync logic without pulling in the whole legacy runtime.  The legacy runtime
re-exports every public name below for backward compatibility.
"""

from __future__ import annotations

import json
import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from pymysql.cursors import DictCursor
from backend.core.config import DEFAULT_INTERVAL_MINUTES, SCAN_INTERVAL_SECONDS
from backend.core.state import (
    ADMIN_KEY_REFRESH_BATCHES,
    ADMIN_KEY_REFRESH_BATCH_LOCK,
    ADMIN_KEY_SYNC_PROOF_BATCH_SIZE,
    STOP_EVENT,
    MAIN_CHANNEL_KEY_REQUEST_LOCK,
    MAIN_CHANNEL_KEY_LAST_REQUEST_AT,
    MAIN_CHANNEL_KEY_RATE_LIMIT_UNTIL,
    MAIN_CHANNEL_KEY_MIN_INTERVAL_SECONDS,
    MAIN_CHANNEL_KEY_RATE_LIMIT_COOLDOWN_SECONDS,
    MAIN_CHANNEL_KEY_CACHE,
    MAIN_CHANNEL_KEY_CACHE_TTL_SECONDS,
)
from backend.core.time import utc_now_iso, app_now, stable_hash, next_check_iso
from backend.db.connection import (
    db_connection,
    db_execute,
    db_query_one,
    db_query_all,
    _q,
)
from backend.core.normalize import (
    _normalize_discovery_base_url,
    _positive_channel_id,
    normalize_base_url,
)
from backend.repositories.sites import (
    find_monitor_site_for_channel,
    normalize_admin_sync_channels,
    normalize_admin_sync_groups,
)
from backend.repositories.admin_sites import (
    RECONCILE_MODE_DELETE,
    get_cached_admin_channel_key,
    persist_admin_channel_key,
    get_admin_site_or_404,
    list_admin_sites_payload,
    admin_site_platform,
    admin_site_reconcile_mode,
    admin_site_sync_all_channels,
)
from backend.services.channel_match_service import (
    match_channel_upstream_binding,
    persist_channel_match,
)
from backend.services.discovery_service import (
    _import_discovered_site_item,
    _reconcile_site_discovery_links_in_connection,
)
from backend.integrations.newapi import (
    aggregate_newapi_channel_candidates,
    fetch_newapi_channel_key,
    set_newapi_channel_key_fast_mode,
)
from backend.services.admin_site_service import (
    fetch_admin_site_channels,
    fetch_admin_site_groups,
)
from backend.services.platform_detect_service import PlatformDetectService

# 每次同步可选的范围：all=全部渠道；recognized=仅平台识别为 NewAPI/sub2api
# 的渠道（并把本地已关联但平台不符的站点直接删除）；selected=勾选的渠道
# （platform 识别同样必须命中，未勾选渠道的本地站点保持不动）。
SYNC_SCOPES = ("all", "recognized", "selected")
SYNCABLE_PLATFORMS = ("newapi", "sub2api")


def record_admin_site_sync_error(admin_site_id: int, message: str) -> None:
    """Keep the last failed attempt without replacing the last good snapshot."""
    now = utc_now_iso()
    safe_message = str(message or "主站同步失败")[:4000]
    try:
        db_execute(
            """
            INSERT INTO admin_site_sync_state
            (admin_site_id, channels_json, groups_json, channels_hash,
             groups_hash, last_success_at, last_error, last_attempt_at,
             updated_at)
            VALUES (?, '[]', '{}', ?, ?, NULL, ?, ?, ?)
            ON DUPLICATE KEY UPDATE
              last_error = VALUES(last_error),
              last_attempt_at = VALUES(last_attempt_at),
              updated_at = VALUES(updated_at)
            """,
            (
                int(admin_site_id),
                stable_hash([]),
                stable_hash({}),
                safe_message,
                now,
                now,
            ),
        )
    except Exception:
        # The original upstream error is more useful than a secondary status
        # table failure, and neither failure is allowed to trigger cleanup.
        pass


def _delete_stale_admin_channel_data_in_connection(
    connection: Any,
    admin_site_id: int,
    live_channel_ids: set,
) -> Tuple[int, int]:
    """Remove cached channel data only after a complete channel read."""
    binding_rows = db_query_all(
        "SELECT channel_id FROM channel_upstream_bindings WHERE admin_site_id = ?",
        (int(admin_site_id),),
        connection=connection,
    )
    key_rows = db_query_all(
        "SELECT channel_id FROM admin_channel_keys WHERE admin_site_id = ?",
        (int(admin_site_id),),
        connection=connection,
    )
    removed_bindings = 0
    removed_keys = 0
    with connection.cursor(DictCursor) as cursor:
        for row in binding_rows:
            channel_id = _positive_channel_id(row.get("channel_id"))
            if channel_id is not None and channel_id in live_channel_ids:
                continue
            cursor.execute(
                _q(
                    "DELETE FROM channel_upstream_bindings "
                    "WHERE admin_site_id = ? AND channel_id = ?"
                ),
                (int(admin_site_id), int(row.get("channel_id") or 0)),
            )
            removed_bindings += int(cursor.rowcount or 0)
        for row in key_rows:
            channel_id = _positive_channel_id(row.get("channel_id"))
            if channel_id is not None and channel_id in live_channel_ids:
                continue
            cursor.execute(
                _q(
                    "DELETE FROM admin_channel_keys "
                    "WHERE admin_site_id = ? AND channel_id = ?"
                ),
                (int(admin_site_id), int(row.get("channel_id") or 0)),
            )
            removed_keys += int(cursor.rowcount or 0)
    return removed_bindings, removed_keys


def _apply_admin_site_channel_reconcile_in_connection(
    connection: Any,
    admin_site_id: int,
    affected_site_ids: set,
    mode: str,
) -> Tuple[int, int, int]:
    """Apply local site state only after stale source links are removed."""
    linked_rows = db_query_all(
        "SELECT DISTINCT site_id FROM site_discovery_links "
        "WHERE admin_site_id = ?",
        (int(admin_site_id),),
        connection=connection,
    )
    for row in linked_rows:
        try:
            affected_site_ids.add(int(row.get("site_id")))
        except (TypeError, ValueError):
            continue

    disabled = 0
    reenabled = 0
    deleted = 0
    now = utc_now_iso()
    for raw_site_id in sorted(affected_site_ids):
        try:
            site_id = int(raw_site_id)
        except (TypeError, ValueError):
            continue
        site = db_query_one(
            "SELECT id, enabled, auto_disabled FROM sites WHERE id = ? FOR UPDATE",
            (site_id,),
            connection=connection,
        )
        if not site:
            continue
        remaining = db_query_one(
            "SELECT COUNT(*) AS count FROM site_discovery_links WHERE site_id = ?",
            (site_id,),
            connection=connection,
        )
        remaining_count = int((remaining or {}).get("count") or 0)
        if remaining_count > 0:
            if int(site.get("auto_disabled") or 0) == 1:
                with connection.cursor(DictCursor) as cursor:
                    cursor.execute(
                        _q(
                            "UPDATE sites SET enabled = 1, auto_disabled = 0, "
                            "updated_at = ? WHERE id = ?"
                        ),
                        (now, site_id),
                    )
                    reenabled += int(cursor.rowcount or 0)
            continue
        if mode == RECONCILE_MODE_DELETE:
            with connection.cursor(DictCursor) as cursor:
                cursor.execute(
                    _q("DELETE FROM sites WHERE id = ?"),
                    (site_id,),
                )
                deleted += int(cursor.rowcount or 0)
        elif int(site.get("enabled") or 0) == 1:
            with connection.cursor(DictCursor) as cursor:
                cursor.execute(
                    _q(
                        "UPDATE sites SET enabled = 0, auto_disabled = 1, "
                        "updated_at = ? WHERE id = ?"
                    ),
                    (now, site_id),
                )
                disabled += int(cursor.rowcount or 0)
    return disabled, reenabled, deleted


def _normalize_sync_scope(
    scope: Optional[str], selected_channel_ids: Optional[List[Any]]
) -> Tuple[Optional[str], List[int]]:
    """Validate the per-run sync scope; ValueError maps to a 400 in the router.

    ``None`` 表示未显式选择：由各主站行的 sync_all_channels 决定默认范围。
    """
    if scope is None:
        channel_ids: List[int] = []
        for raw in selected_channel_ids or []:
            channel_id = _positive_channel_id(raw)
            if channel_id is not None:
                channel_ids.append(channel_id)
        return None, channel_ids
    scope = str(scope).strip().lower()
    if scope not in SYNC_SCOPES:
        raise ValueError(f"同步范围无效：{scope}")
    channel_ids = []
    for raw in selected_channel_ids or []:
        channel_id = _positive_channel_id(raw)
        if channel_id is not None:
            channel_ids.append(channel_id)
    if scope == "selected" and not channel_ids:
        raise ValueError("勾选渠道同步需要至少勾选一个渠道")
    return scope, channel_ids


def _delete_platform_mismatch_site_links_in_connection(
    connection: Any,
    admin_site_id: int,
    mismatch_urls: set,
) -> Tuple[int, int]:
    """recognized 模式对账加强：删除本地已关联但平台不符的监控站点。

    只摘除该主站来源的发现链接；站点若仍被其他主站的渠道关联则仅摘链，
    无剩余关联时物理删除站点。平台判定复用本次同步的识别结果（mismatch_urls），
    不在此处二次探测，避免把探测失败的 unknown 误判成不符。返回 (摘链数, 删站数)。
    """
    normalized_urls = {
        normalized
        for normalized in (
            normalize_base_url(str(url or "")) for url in mismatch_urls
        )
        if normalized
    }
    if not normalized_urls:
        return 0, 0
    rows = db_query_all(
        "SELECT id, site_id, upstream_base_url FROM site_discovery_links "
        "WHERE admin_site_id = ?",
        (int(admin_site_id),),
        connection=connection,
    )
    removed_links = 0
    mismatch_site_ids: set = set()
    with connection.cursor(DictCursor) as cursor:
        for row in rows:
            current = normalize_base_url(str(row.get("upstream_base_url") or ""))
            if not current or current not in normalized_urls:
                continue
            cursor.execute(
                _q("DELETE FROM site_discovery_links WHERE id = ?"),
                (int(row.get("id") or 0),),
            )
            removed_links += int(cursor.rowcount or 0)
            site_id = _positive_channel_id(row.get("site_id"))
            if site_id is not None:
                mismatch_site_ids.add(site_id)
    deleted_sites = 0
    for site_id in sorted(mismatch_site_ids):
        remaining = db_query_one(
            "SELECT COUNT(*) AS count FROM site_discovery_links WHERE site_id = ?",
            (site_id,),
            connection=connection,
        )
        if int((remaining or {}).get("count") or 0) > 0:
            continue
        site = db_query_one(
            "SELECT name, base_url FROM sites WHERE id = ?",
            (site_id,),
            connection=connection,
        )
        with connection.cursor(DictCursor) as cursor:
            cursor.execute(_q("DELETE FROM sites WHERE id = ?"), (site_id,))
            deleted = int(cursor.rowcount or 0)
        if deleted:
            deleted_sites += 1
            print(
                f"[主站同步] admin_site_id={admin_site_id} 平台不符删除站点 "
                f"#{site_id} {(site or {}).get('name') or ''} "
                f"{(site or {}).get('base_url') or ''}",
                flush=True,
            )
    return removed_links, deleted_sites


def _sync_admin_site_snapshot_in_connection(
    connection: Any,
    admin: Dict[str, Any],
    channels: List[Dict[str, Any]],
    groups: Dict[str, Any],
    mode: str,
    scope: str = "all",
    selected_channel_ids: Optional[List[int]] = None,
) -> Dict[str, Any]:
    """Write one complete admin-site snapshot and reconcile its local links."""
    admin_site_id = int(admin.get("id") or 0)
    platform = admin_site_platform(admin)
    if admin_site_id <= 0:
        raise ValueError("管理站点 ID 无效")
    if platform == "newapi":
        for channel in channels:
            base_url, url_error = _normalize_discovery_base_url(
                channel.get("base_url")
            )
            if url_error or not base_url:
                raise ValueError(
                    f"渠道 {channel.get('id')} 缺少有效 Base URL，拒绝执行同步清理"
                )
            channel["base_url"] = base_url

    channels_json = json.dumps(
        channels, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    groups_json = json.dumps(
        groups, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    channels_hash = stable_hash(channels)
    groups_hash = stable_hash(groups)
    now = utc_now_iso()
    previous = db_query_one(
        "SELECT channels_hash, groups_hash FROM admin_site_sync_state "
        "WHERE admin_site_id = ? FOR UPDATE",
        (admin_site_id,),
        connection=connection,
    )

    candidates: List[Dict[str, Any]] = []
    imported = 0
    conflicts: List[Dict[str, Any]] = []
    removed_links = 0
    affected_site_ids: set = set()
    preferred_site_by_channel: Dict[int, int] = {}
    excluded_candidates = 0
    excluded_channels = 0
    mismatch_urls: set = set()
    if platform == "newapi":
        candidates = aggregate_newapi_channel_candidates(channels)
        import_candidates = candidates
        selected_ids = set(selected_channel_ids or [])
        if scope in ("recognized", "selected"):
            classifier = PlatformDetectService()
            platform_by_url = classifier.platforms_for_base_urls(
                [str(item.get("base_url") or "") for item in candidates]
            )

            def _candidate_platform(item: Dict[str, Any]) -> str:
                return platform_by_url.get(
                    str(item.get("base_url") or ""), "unknown"
                )

            def _holds_selected_channel(item: Dict[str, Any]) -> bool:
                return any(
                    _positive_channel_id(raw) in selected_ids
                    for raw in (item.get("channel_ids") or [])
                )

        if scope == "recognized":
            # 只导入识别为 NewAPI/sub2api 的渠道；被过滤掉的候选（平台不符）
            # 记入 mismatch_urls，对账阶段把本地已关联站点一并删除。
            import_candidates = [
                item
                for item in candidates
                if _candidate_platform(item) in SYNCABLE_PLATFORMS
            ]
            mismatch_urls = {
                str(item.get("base_url") or "")
                for item in candidates
                if _candidate_platform(item) not in SYNCABLE_PLATFORMS
            }
            excluded_candidates = len(candidates) - len(import_candidates)
            imported_channel_count = sum(
                len(item.get("channel_ids") or []) for item in import_candidates
            )
            excluded_channels = (
                sum(len(item.get("channel_ids") or []) for item in candidates)
                - imported_channel_count
            )
            if excluded_candidates:
                print(
                    f"[主站同步] admin_site_id={admin_site_id} "
                    f"识别过滤：导入 {len(import_candidates)} 个上游站点，"
                    f"跳过 {excluded_candidates} 个站点 / {excluded_channels} 个渠道",
                    flush=True,
                )
        elif scope == "selected":
            # 勾选模式：只导入勾选渠道所属且平台可同步的候选；未勾选渠道的
            # 本地站点与链接保持不动，不参与对账排除。
            import_candidates = [
                item
                for item in candidates
                if _holds_selected_channel(item)
                and _candidate_platform(item) in SYNCABLE_PLATFORMS
            ]
        for candidate in import_candidates:
            result = _import_discovered_site_item(
                connection,
                admin_site_id,
                candidate,
                DEFAULT_INTERVAL_MINUTES,
                allow_existing_platform=True,
            )
            if not isinstance(result, dict):
                raise RuntimeError(
                    "创建或关联监控站点失败"
                )
            if result.get("status") == "conflict":
                # Keep the complete upstream snapshot even if a candidate has
                # an import limitation unrelated to the main-site read.
                conflicts.append(
                    {
                        "base_url": result.get("base_url") or candidate.get("base_url"),
                        "name": result.get("name") or candidate.get("name"),
                        "channel_ids": list(
                            result.get("channel_ids")
                            or candidate.get("channel_ids")
                            or []
                        ),
                        "site_id": result.get("site_id"),
                        "message": result.get("message") or "监控站点平台冲突",
                    }
                )
                continue
            result_site_id = _positive_channel_id(result.get("site_id"))
            if result_site_id is not None:
                for raw_channel_id in candidate.get("channel_ids") or []:
                    channel_id = _positive_channel_id(raw_channel_id)
                    if channel_id is not None:
                        preferred_site_by_channel[channel_id] = result_site_id
            if result.get("status") == "created":
                imported += 1
        removed_links, affected_site_ids = (
            _reconcile_site_discovery_links_in_connection(
                connection,
                admin_site_id,
                candidates,
                preferred_site_by_channel,
            )
        )

    live_channel_ids = {
        int(channel["id"]) for channel in channels if _positive_channel_id(channel.get("id"))
    }
    removed_bindings, removed_keys = (
        _delete_stale_admin_channel_data_in_connection(
            connection, admin_site_id, live_channel_ids
        )
    )
    disabled = reenabled = deleted = 0
    platform_removed_links = 0
    platform_deleted = 0
    if platform == "newapi":
        disabled, reenabled, deleted = _apply_admin_site_channel_reconcile_in_connection(
            connection, admin_site_id, affected_site_ids, mode
        )
        if mismatch_urls:
            (
                platform_removed_links,
                platform_deleted,
            ) = _delete_platform_mismatch_site_links_in_connection(
                connection, admin_site_id, mismatch_urls
            )

    with connection.cursor(DictCursor) as cursor:
        cursor.execute(
            _q(
                """
                INSERT INTO admin_site_sync_state
                (admin_site_id, channels_json, groups_json, channels_hash,
                 groups_hash, last_success_at, last_error, last_attempt_at,
                 updated_at)
                VALUES (?, ?, ?, ?, ?, ?, NULL, ?, ?)
                ON DUPLICATE KEY UPDATE
                  channels_json = VALUES(channels_json),
                  groups_json = VALUES(groups_json),
                  channels_hash = VALUES(channels_hash),
                  groups_hash = VALUES(groups_hash),
                  last_success_at = VALUES(last_success_at),
                  last_error = NULL,
                  last_attempt_at = VALUES(last_attempt_at),
                  updated_at = VALUES(updated_at)
                """
            ),
            (
                admin_site_id,
                channels_json,
                groups_json,
                channels_hash,
                groups_hash,
                now,
                now,
                now,
            ),
        )
    return {
        "admin_site_id": admin_site_id,
        "platform": platform,
        "status": "synced",
        "scope": scope,
        "imported": imported,
        "excluded_candidates": excluded_candidates,
        "excluded_channels": excluded_channels,
        "platform_removed_links": platform_removed_links,
        "platform_deleted": platform_deleted,
        "channels_count": len(channels),
        "groups_count": len(groups),
        "channels_changed": not previous
        or previous.get("channels_hash") != channels_hash,
        "groups_changed": not previous
        or previous.get("groups_hash") != groups_hash,
        "conflicts": conflicts,
        "conflict_count": len(conflicts),
        "removed_links": removed_links,
        "removed_bindings": removed_bindings,
        "removed_keys": removed_keys,
        "disabled": disabled,
        "reenabled": reenabled,
        "deleted": deleted,
    }


def refresh_next_admin_site_channel_key(admin: Dict[str, Any]) -> Dict[str, Any]:
    """Refresh one key, prioritizing channels not covered by the current proof batch."""
    admin_site_id = int(admin.get("id") or 0)
    ok, raw_channels, _meta, error = fetch_admin_site_channels(admin, "")
    if not ok:
        return {"success": False, "message": error or "读取主站渠道失败"}
    channels, normalize_error = normalize_admin_sync_channels(raw_channels)
    if normalize_error:
        return {"success": False, "message": normalize_error}
    fetched_rows = db_query_all(
        "SELECT channel_id, fetched_at FROM admin_channel_keys WHERE admin_site_id = ?",
        (admin_site_id,),
    )
    fetched_at = {int(row["channel_id"]): str(row.get("fetched_at") or "") for row in fetched_rows}
    candidates: List[int] = []
    for channel in channels:
        try:
            channel_id = int(channel.get("id") or 0)
        except (TypeError, ValueError):
            continue
        if channel_id > 0:
            candidates.append(channel_id)
    if not candidates:
        return {
            "success": True,
            "batch_remaining": 0,
            "message": "主站暂无可更新 key 的渠道",
        }
    proof_verified_at = str(admin.get("security_proof_verified_at") or "")
    batch_candidates = (
        [
            value
            for value in candidates
            if not fetched_at.get(value) or fetched_at[value] < proof_verified_at
        ]
        if proof_verified_at
        else []
    )
    selection = batch_candidates or candidates
    channel_id = min(
        selection,
        key=lambda value: (
            bool(fetched_at.get(value)),
            fetched_at.get(value, ""),
            value,
        ),
    )
    previous = get_cached_admin_channel_key(admin_site_id, channel_id)
    key_ok, key, key_error = fetch_newapi_channel_key(admin, channel_id, force_refresh=True)
    if not key_ok:
        return {
            "success": False,
            "channel_id": channel_id,
            "message": key_error or "读取渠道 key 失败",
        }
    changed = key != previous
    if changed:
        try:
            match_channel_upstream_binding(admin, channel_id, force_refresh=False)
        except Exception:
            pass
    return {
        "success": True,
        "channel_id": channel_id,
        "changed": changed,
        "batch_remaining": max(0, len(batch_candidates) - 1),
        "message": "渠道 key 已更新并重新匹配" if changed else "渠道 key 无变化",
    }


def run_due_admin_key_syncs(now: Optional[datetime] = None) -> None:
    current = now or app_now()
    now_iso = current.isoformat(timespec="seconds")
    admins = db_query_all(
        """
        SELECT * FROM admin_sites
        WHERE platform = 'newapi' AND key_sync_enabled = 1
          AND (key_sync_next_at IS NULL OR key_sync_next_at <= ?)
          AND (key_sync_backoff_until IS NULL OR key_sync_backoff_until <= ?)
        ORDER BY COALESCE(key_sync_next_at, '') ASC, id ASC
        """,
        (now_iso, now_iso),
    )
    for admin in admins:
        result = refresh_next_admin_site_channel_key(admin)
        attempts = 1
        while (
            result.get("success")
            and int(result.get("batch_remaining") or 0) > 0
            and attempts < ADMIN_KEY_SYNC_PROOF_BATCH_SIZE
        ):
            result = refresh_next_admin_site_channel_key(admin)
            attempts += 1
        interval = max(5, min(1440, int(admin.get("key_sync_interval_minutes") or 5)))
        if result.get("success"):
            batch_remaining = int(result.get("batch_remaining") or 0)
            next_at = (
                current + (
                    timedelta(seconds=SCAN_INTERVAL_SECONDS)
                    if batch_remaining > 0
                    else timedelta(minutes=interval)
                )
            ).isoformat(timespec="seconds")
            db_execute(
                """
                UPDATE admin_sites SET key_sync_last_at = ?, key_sync_next_at = ?,
                    key_sync_last_error = NULL, key_sync_backoff_until = NULL,
                    key_sync_failure_count = 0, updated_at = ? WHERE id = ?
                """,
                (now_iso, next_at, now_iso, int(admin["id"])),
            )
            continue
        message = str(result.get("message") or "渠道 key 自动更新失败")
        failures = int(admin.get("key_sync_failure_count") or 0) + 1
        if "429" in message or "限流" in message:
            delay_minutes = (1, 2, 5, 15, 30)[min(failures - 1, 4)]
        elif any(marker in message for marker in ("安全验证", "2FA", "proof")):
            delay_minutes = 30
        else:
            delay_minutes = min(30, max(interval, 5))
        backoff_until = (current + timedelta(minutes=delay_minutes)).isoformat(timespec="seconds")
        db_execute(
            """
            UPDATE admin_sites SET key_sync_last_at = ?, key_sync_next_at = ?,
                key_sync_last_error = ?, key_sync_backoff_until = ?,
                key_sync_failure_count = ?, updated_at = ? WHERE id = ?
            """,
            (now_iso, backoff_until, message, backoff_until, failures, now_iso, int(admin["id"])),
        )


# ---------------------------------------------------------------------------
# 全量渠道 key / 倍率刷新批次（手动触发 / 2FA 验证后自动触发）
# ---------------------------------------------------------------------------
# 一次性 daemon 线程 + ThreadPool 并发刷新：
# - mode="key"  ：每个渠道并发「读 key（快速门控 0.25s 错峰 + 429 冷却保护）
#                 → 重新匹配上游分组倍率」；仅 NewAPI。
# - mode="ratio"：只重新匹配每个渠道的上游分组倍率（复用已保存 key，不发
#                 2FA 保护的 key 接口请求）；NewAPI / sub2api 均可。
# proof 失效时批次暂停（status="paused"），重新验证 2FA 后再次 trigger 即可。
# 进度由 /api/admin/sites 序列化轮询：key 批次 → key_refresh，倍率批次 →
# ratio_refresh。

# 单个渠道遇到限流时在 worker 内原地重试的最大次数
_ADMIN_KEY_REFRESH_MAX_RETRIES_PER_CHANNEL = 3
# 批次内并发工作线程数
_ADMIN_KEY_REFRESH_CONCURRENCY = 4

# proof 失效 / 需要 2FA 的消息标记（key 读取与匹配共用）
_ADMIN_PROOF_MARKERS = (
    "安全验证",
    "2FA",
    "proof",
    "Session",
    "needs_key_verification",
)


def _admin_key_refresh_batch_progress(batch: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "status": str(batch.get("status") or ""),
        "mode": str(batch.get("mode") or "key"),
        "total": int(batch.get("total") or 0),
        "done": int(batch.get("done") or 0),
        "failed": int(batch.get("failed") or 0),
        "message": batch.get("message"),
        "started_at": batch.get("started_at"),
        "updated_at": batch.get("updated_at"),
    }


def admin_site_key_refresh_progress(
    admin_site_id: int, mode: str = "key"
) -> Optional[Dict[str, Any]]:
    """读取某个主站的刷新批次进度（纯内存读，无批次时返回 None）。"""
    with ADMIN_KEY_REFRESH_BATCH_LOCK:
        batch = ADMIN_KEY_REFRESH_BATCHES.get(int(admin_site_id))
        if not batch or str(batch.get("mode") or "key") != mode:
            return None
        return _admin_key_refresh_batch_progress(batch)


def _admin_batch_is_proof_failure(message: str) -> bool:
    return any(marker in message for marker in _ADMIN_PROOF_MARKERS)


def trigger_admin_site_full_key_refresh(
    admin: Dict[str, Any], mode: str = "key"
) -> Dict[str, Any]:
    """为主站启动（或恢复）一次全量刷新批次。

    mode="key"   仅 NewAPI：并发刷新全部渠道 key + 倍率绑定。
    mode="ratio" 全平台：并发重新匹配全部渠道的上游分组倍率。
    立即返回进度；实际刷新在后台 daemon 线程里并发执行。
    暂停中的批次（2FA 过期）会恢复续跑。
    """
    admin_site_id = int(admin.get("id") or 0)
    if not admin_site_id:
        return {"success": False, "message": "主站不存在"}
    mode = "ratio" if mode == "ratio" else "key"
    platform = str(admin.get("platform") or "newapi").strip().lower()
    if mode == "key" and platform != "newapi":
        return {"success": False, "message": "仅 NewAPI 主站支持全量刷新渠道 key"}

    with ADMIN_KEY_REFRESH_BATCH_LOCK:
        existing = ADMIN_KEY_REFRESH_BATCHES.get(admin_site_id)
        if existing and existing.get("status") == "running":
            return {
                "success": True,
                "already_running": True,
                "progress": _admin_key_refresh_batch_progress(existing),
            }

    ok, raw_channels, _meta, error = fetch_admin_site_channels(admin, "")
    if not ok:
        return {"success": False, "message": error or "读取主站渠道失败"}
    channels, normalize_error = normalize_admin_sync_channels(raw_channels)
    if normalize_error:
        return {"success": False, "message": normalize_error}
    channel_ids: List[int] = []
    for channel in channels:
        channel_id = _positive_channel_id(channel.get("id"))
        if channel_id:
            channel_ids.append(channel_id)
    if not channel_ids:
        return {"success": False, "message": "主站暂无可刷新的渠道"}

    now_iso = utc_now_iso()
    with ADMIN_KEY_REFRESH_BATCH_LOCK:
        existing = ADMIN_KEY_REFRESH_BATCHES.get(admin_site_id)
        if existing and existing.get("status") == "running":
            # 并发触发：保留先到的批次
            return {
                "success": True,
                "already_running": True,
                "progress": _admin_key_refresh_batch_progress(existing),
            }
        if (
            existing
            and existing.get("status") == "paused"
            and str(existing.get("mode") or "key") == mode
            and list(existing.get("channel_ids") or []) == channel_ids
        ):
            # 断点续跑：保留 done / failed 计数（仅同 mode 才续跑）
            existing["status"] = "running"
            existing["message"] = None
            existing["updated_at"] = now_iso
            batch = existing
        else:
            batch = {
                "admin_site_id": admin_site_id,
                "mode": mode,
                "status": "running",
                "channel_ids": channel_ids,
                "remaining": list(channel_ids),
                "total": len(channel_ids),
                "done": 0,
                "failed": 0,
                "message": None,
                "started_at": now_iso,
                "updated_at": now_iso,
            }
            ADMIN_KEY_REFRESH_BATCHES[admin_site_id] = batch

    threading.Thread(
        target=_run_admin_key_refresh_batch,
        args=(admin_site_id,),
        daemon=True,
        name=f"admin-{mode}-refresh-{admin_site_id}",
    ).start()
    return {"success": True, "progress": _admin_key_refresh_batch_progress(batch)}


def _process_admin_refresh_channel(
    site: Dict[str, Any], channel_id: int, mode: str
) -> Tuple[str, Optional[str]]:
    """批次 worker：处理单个渠道，返回 (结果, 消息)。

    结果取值："ok" / "failed" / "proof"。
    """
    if mode == "key":
        key_ok, _key, key_error = None, "", None
        message = ""
        for _attempt in range(_ADMIN_KEY_REFRESH_MAX_RETRIES_PER_CHANNEL):
            key_ok, _key, key_error = fetch_newapi_channel_key(
                site, channel_id, force_refresh=True
            )
            message = str(key_error or "")
            if key_ok:
                break
            if _admin_batch_is_proof_failure(message):
                return "proof", message
            if "429" not in message and "限流" not in message:
                break
            # 限流：等冷却结束再原地重试（其他 worker 同样会被门控挡住）
            time.sleep(MAIN_CHANNEL_KEY_RATE_LIMIT_COOLDOWN_SECONDS + 1.0)
        if not key_ok:
            return "failed", message or "读取渠道 key 失败"

    # 重新匹配上游分组倍率（key 模式下 key 刚落缓存；ratio 模式复用已存 key）
    try:
        match_ok, match_payload, match_error = match_channel_upstream_binding(
            site, channel_id, force_refresh=False
        )
    except Exception as exc:  # noqa: BLE001 - 单渠道异常不拖垮批次
        traceback.print_exc()
        return "failed", f"匹配上游倍率异常：{exc}"
    if not match_ok:
        message = str(match_error or "")
        if not message and isinstance(match_payload, dict):
            message = str(match_payload.get("match_message") or "")
        if _admin_batch_is_proof_failure(message) or (
            isinstance(match_payload, dict)
            and match_payload.get("match_status") == "needs_key_verification"
        ):
            return "proof", message
        return "failed", message or "匹配上游倍率失败"
    return "ok", None


def _run_admin_key_refresh_batch(admin_site_id: int) -> None:
    from backend.core.state import (
        NEWAPI_MATCH_GROUPS_CACHE,
        NEWAPI_MATCH_GROUPS_LOCK,
    )

    with ADMIN_KEY_REFRESH_BATCH_LOCK:
        batch = ADMIN_KEY_REFRESH_BATCHES.get(admin_site_id)
        if not batch or batch.get("status") != "running":
            return
        mode = str(batch.get("mode") or "key")
        channel_ids = list(batch.get("channel_ids") or [])

    site = db_query_one("SELECT * FROM admin_sites WHERE id = ?", (admin_site_id,))
    if not site:
        with ADMIN_KEY_REFRESH_BATCH_LOCK:
            batch = ADMIN_KEY_REFRESH_BATCHES.get(admin_site_id)
            if batch:
                batch["status"] = "failed"
                batch["message"] = "主站已删除"
                batch["updated_at"] = utc_now_iso()
        return
    if STOP_EVENT.is_set():
        return

    # 批次开始：清空上游分组 30s TTL 缓存，保证本轮匹配拿到最新倍率；
    # 之后同上游多渠道仍共享一次分组请求（缓存回填）。
    with NEWAPI_MATCH_GROUPS_LOCK:
        NEWAPI_MATCH_GROUPS_CACHE.clear()
    fast_mode_enabled = mode == "key"
    if fast_mode_enabled:
        set_newapi_channel_key_fast_mode(site, True)
    try:
        with ThreadPoolExecutor(
            max_workers=_ADMIN_KEY_REFRESH_CONCURRENCY,
            thread_name_prefix=f"admin-{mode}-refresh-{admin_site_id}",
        ) as pool:
            future_map = {
                pool.submit(_process_admin_refresh_channel, site, cid, mode): cid
                for cid in channel_ids
            }
            for future in as_completed(future_map):
                channel_id = future_map[future]
                try:
                    result, message = future.result()
                except Exception as exc:  # noqa: BLE001
                    result, message = "failed", f"渠道 #{channel_id} 处理异常：{exc}"
                with ADMIN_KEY_REFRESH_BATCH_LOCK:
                    batch = ADMIN_KEY_REFRESH_BATCHES.get(admin_site_id)
                    if not batch or batch.get("status") != "running":
                        # 批次被外部暂停（proof）或重置：尽快排空线程池后退出
                        for pending in future_map:
                            pending.cancel()
                        continue
                    batch["updated_at"] = utc_now_iso()
                    batch["remaining"] = [
                        cid
                        for cid in (batch.get("remaining") or [])
                        if cid != channel_id
                    ]
                    if result == "ok":
                        batch["done"] = int(batch.get("done") or 0) + 1
                    elif result == "proof":
                        batch["status"] = "paused"
                        batch["message"] = (
                            "需要重新验证 2FA：请在主站编辑弹窗完成安全验证后重新触发"
                        )
                        for pending in future_map:
                            pending.cancel()
                    else:
                        batch["failed"] = int(batch.get("failed") or 0) + 1
                        batch["message"] = (
                            f"渠道 #{channel_id} 失败：{str(message or '')[:200]}"
                        )
    finally:
        if fast_mode_enabled:
            set_newapi_channel_key_fast_mode(site, False)
        with ADMIN_KEY_REFRESH_BATCH_LOCK:
            batch = ADMIN_KEY_REFRESH_BATCHES.get(admin_site_id)
            if batch and batch.get("status") == "running":
                batch["status"] = "done"
                batch["message"] = None
                batch["updated_at"] = utc_now_iso()


def _sync_one_admin_site(
    admin: Dict[str, Any],
    mode: str,
    scope: str = "all",
    selected_channel_ids: Optional[List[int]] = None,
) -> Dict[str, Any]:
    admin_site_id = int(admin.get("id") or 0)
    try:
        # 单主站内并行拉取 channels / groups（两个独立HTTP，不等前一个再发下一个）
        def _fetch_channels_task() -> Tuple[Any, Any, Any, Any]:
            return fetch_admin_site_channels(admin, "")
        def _fetch_groups_task() -> Tuple[Any, Any, Any]:
            return fetch_admin_site_groups(admin)
        with ThreadPoolExecutor(max_workers=2) as inner_pool:
            fut_channels = inner_pool.submit(_fetch_channels_task)
            fut_groups = inner_pool.submit(_fetch_groups_task)
            ok_ch, raw_channels, _channel_meta, channel_error = fut_channels.result()
            ok_gr, raw_groups, group_error = fut_groups.result()
        if not ok_ch:
            raise RuntimeError(channel_error or "读取主站渠道失败")
        if not ok_gr:
            raise RuntimeError(group_error or "读取主站分组失败")
        # 拿到原始数据后，channels/groups 的 normalize 也是CPU纯函数，也并行
        def _norm_channels() -> Tuple[Any, Any]:
            return normalize_admin_sync_channels(raw_channels)
        def _norm_groups() -> Tuple[Any, Any]:
            return normalize_admin_sync_groups(raw_groups)
        with ThreadPoolExecutor(max_workers=2) as inner_pool:
            (channels, channels_error), (groups, groups_error) = inner_pool.map(
                lambda f: f(), [_norm_channels, _norm_groups]
            )
        if channels_error:
            raise RuntimeError(channels_error)
        if groups_error:
            raise RuntimeError(groups_error)

        with db_connection() as connection:
            try:
                result = _sync_admin_site_snapshot_in_connection(
                    connection, admin, channels, groups, mode, scope,
                    selected_channel_ids,
                )
                connection.commit()
                print(
                    "[主站同步] "
                    f"admin_site_id={admin_site_id} "
                    f"scope={scope} "
                    f"channels={result.get('channels_count', 0)} "
                    f"groups={result.get('groups_count', 0)} "
                    f"imported={result.get('imported', 0)} "
                    f"conflicts={result.get('conflict_count', 0)} "
                    f"disabled={result.get('disabled', 0)} "
                    f"deleted={result.get('deleted', 0)} "
                    f"platform_deleted={result.get('platform_deleted', 0)}",
                    flush=True,
                )
                return result
            except Exception:
                try:
                    connection.rollback()
                except Exception:
                    pass
                raise
    except Exception as exc:
        message = str(exc) or "主站同步失败"
        record_admin_site_sync_error(admin_site_id, message)
        print(
            f"[主站同步失败] admin_site_id={admin_site_id} message={message}",
            flush=True,
        )
        return {
            "admin_site_id": admin_site_id,
            "platform": admin_site_platform(admin),
            "status": "sync_failed",
            "message": message,
            "imported": 0,
            "disabled": 0,
            "reenabled": 0,
            "deleted": 0,
        }


def _run_admin_site_sync(
    admin_site_id: Optional[int] = None,
    scope: Optional[str] = None,
    channel_ids: Optional[List[Any]] = None,
) -> List[Dict[str, Any]]:
    scope, selected_channel_ids = _normalize_sync_scope(scope, channel_ids)
    try:
        if admin_site_id is None:
            admin_sites = db_query_all("SELECT * FROM admin_sites ORDER BY id")
        else:
            admin_sites = db_query_all(
                "SELECT * FROM admin_sites WHERE id = ?", (int(admin_site_id),)
            )
    except Exception as exc:
        return [{"status": "sync_failed", "message": str(exc) or "读取主站列表失败"}]

    if admin_site_id is not None and not admin_sites:
        return [
            {
                "admin_site_id": int(admin_site_id),
                "status": "sync_failed",
                "message": "管理站点不存在",
                "imported": 0,
                "disabled": 0,
                "reenabled": 0,
                "deleted": 0,
            }
        ]
    results: List[Dict[str, Any]] = []
    # 多主站并发同步，默认线程池大小为主站数量（上限8），避免串行等每个主站的HTTP
    # 对账模式一律按主站行配置（admin_sites.reconcile_mode）；范围请求显式指定时
    # 全局生效，未指定（scope=None）时按主站行 sync_all_channels 决定默认范围
    def _sync_args(admin: Dict[str, Any]) -> tuple:
        admin_scope = scope or (
            "all" if admin_site_sync_all_channels(admin) else "recognized"
        )
        admin_selected = selected_channel_ids if admin_scope == "selected" else None
        return (
            admin,
            admin_site_reconcile_mode(admin),
            admin_scope,
            admin_selected,
        )

    valid_admins = [a for a in admin_sites if isinstance(a, dict)]
    max_workers = max(1, min(8, len(valid_admins)))
    if max_workers <= 1:
        for admin in valid_admins:
            results.append(_sync_one_admin_site(*_sync_args(admin)))
    else:
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            future_map = {
                pool.submit(_sync_one_admin_site, *_sync_args(admin)): int(
                    admin.get("id") or 0
                )
                for admin in valid_admins
            }
            for future in as_completed(future_map):
                try:
                    results.append(future.result())
                except Exception as exc:  # noqa: BLE001
                    results.append({
                        "admin_site_id": future_map.get(future),
                        "status": "sync_failed",
                        "message": str(exc) or "主站同步异常",
                        "imported": 0,
                        "disabled": 0,
                        "reenabled": 0,
                        "deleted": 0,
                    })

    results.append(
        {
            "status": "reconcile",
            "channels_changed": any(
                bool(item.get("channels_changed"))
                for item in results
                if isinstance(item, dict)
            ),
            "groups_changed": any(
                bool(item.get("groups_changed"))
                for item in results
                if isinstance(item, dict)
            ),
            "disabled": sum(
                int(item.get("disabled") or 0)
                for item in results
                if isinstance(item, dict)
            ),
            "reenabled": sum(
                int(item.get("reenabled") or 0)
                for item in results
                if isinstance(item, dict)
            ),
            "deleted": sum(
                int(item.get("deleted") or 0)
                for item in results
                if isinstance(item, dict)
            ),
        }
    )
    return results


def auto_sync_admin_site_channels_to_sites(
    admin_site_id: Optional[int] = None,
    scope: Optional[str] = None,
    channel_ids: Optional[List[Any]] = None,
) -> List[Dict[str, Any]]:
    """Synchronize one selected admin site, or all admin sites for API callers.

    ``scope`` picks the per-run import range (all / recognized / selected);
    ``channel_ids`` is required when scope is "selected". ``None`` falls back
    to each admin site's own sync_all_channels config.
    """
    return _run_admin_site_sync(admin_site_id, scope, channel_ids)

