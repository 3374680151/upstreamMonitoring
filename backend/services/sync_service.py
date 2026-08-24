"""Admin-site channel sync service.

Extracted from ``backend.legacy_runtime`` so the FastAPI boundary can import
sync logic without pulling in the whole legacy runtime.  The legacy runtime
re-exports every public name below for backward compatibility.
"""

from __future__ import annotations

import json
import traceback
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from backend.core.config import DEFAULT_INTERVAL_MINUTES, SCAN_INTERVAL_SECONDS
from backend.core.state import (
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
    db_execute_rowcount,
    db_query_one,
    db_query_all,
    _q,
)
from backend.core.normalize import _positive_channel_id, _normalize_discovery_base_url
from backend.repositories.sites import (
    find_monitor_site_for_channel,
    normalize_admin_sync_channels,
    normalize_admin_sync_groups,
)
from backend.repositories.admin_sites import (
    get_cached_admin_channel_key,
    persist_admin_channel_key,
    get_admin_site_or_404,
    list_admin_sites_payload,
    admin_site_platform,
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
)
from backend.services.admin_site_service import (
    fetch_admin_site_channels,
    fetch_admin_site_groups,
)
from backend.services.monitoring_service import (
    RECONCILE_MODE_DELETE,
    get_main_site_reconcile_mode,
)


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
    with connection.cursor() as cursor:
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
                with connection.cursor() as cursor:
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
            with connection.cursor() as cursor:
                cursor.execute(
                    _q("DELETE FROM sites WHERE id = ?"),
                    (site_id,),
                )
                deleted += int(cursor.rowcount or 0)
        elif int(site.get("enabled") or 0) == 1:
            with connection.cursor() as cursor:
                cursor.execute(
                    _q(
                        "UPDATE sites SET enabled = 0, auto_disabled = 1, "
                        "updated_at = ? WHERE id = ?"
                    ),
                    (now, site_id),
                )
                disabled += int(cursor.rowcount or 0)
    return disabled, reenabled, deleted


def _sync_admin_site_snapshot_in_connection(
    connection: Any,
    admin: Dict[str, Any],
    channels: List[Dict[str, Any]],
    groups: Dict[str, Any],
    mode: str,
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
    if platform == "newapi":
        candidates = aggregate_newapi_channel_candidates(channels)
        for candidate in candidates:
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
    if platform == "newapi":
        disabled, reenabled, deleted = _apply_admin_site_channel_reconcile_in_connection(
            connection, admin_site_id, affected_site_ids, mode
        )

    with connection.cursor() as cursor:
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
        "imported": imported,
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


def _sync_one_admin_site(
    admin: Dict[str, Any],
    mode: str,
) -> Dict[str, Any]:
    admin_site_id = int(admin.get("id") or 0)
    try:
        ok, raw_channels, _channel_meta, channel_error = fetch_admin_site_channels(
            admin, ""
        )
        if not ok:
            raise RuntimeError(channel_error or "读取主站渠道失败")
        ok, raw_groups, group_error = fetch_admin_site_groups(admin)
        if not ok:
            raise RuntimeError(group_error or "读取主站分组失败")
        channels, channels_error = normalize_admin_sync_channels(raw_channels)
        if channels_error:
            raise RuntimeError(channels_error)
        groups, groups_error = normalize_admin_sync_groups(raw_groups)
        if groups_error:
            raise RuntimeError(groups_error)

        with db_connection() as connection:
            try:
                result = _sync_admin_site_snapshot_in_connection(
                    connection, admin, channels, groups, mode
                )
                connection.commit()
                print(
                    "[主站同步] "
                    f"admin_site_id={admin_site_id} "
                    f"channels={result.get('channels_count', 0)} "
                    f"groups={result.get('groups_count', 0)} "
                    f"imported={result.get('imported', 0)} "
                    f"conflicts={result.get('conflict_count', 0)} "
                    f"disabled={result.get('disabled', 0)} "
                    f"deleted={result.get('deleted', 0)}",
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
) -> List[Dict[str, Any]]:
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
    mode = get_main_site_reconcile_mode()
    results: List[Dict[str, Any]] = []
    for admin in admin_sites:
        if not isinstance(admin, dict):
            continue
        results.append(_sync_one_admin_site(admin, mode))

    results.append(
        {
            "status": "reconcile",
            "mode": mode,
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
) -> List[Dict[str, Any]]:
    """Synchronize one selected admin site, or all admin sites for API callers."""
    return _run_admin_site_sync(admin_site_id)


def persist_channel_binding_refreshed_auth(
    admin_site_id: int,
    channel_id: int,
    refreshed_auth: Any,
    *,
    expected_access_token: Optional[str] = None,
    expected_refresh_token: Optional[str] = None,
) -> None:
    """Keep a channel-level sub2api binding usable after access-token refresh."""
    if not isinstance(refreshed_auth, dict):
        return
    access_token = str(refreshed_auth.get("access_token") or "").strip()
    refresh_token = str(refreshed_auth.get("refresh_token") or "").strip()
    if not access_token and not refresh_token:
        return
    assignments = [
        "access_token = COALESCE(NULLIF(?, ''), access_token)",
        "refresh_token = COALESCE(NULLIF(?, ''), refresh_token)",
        "updated_at = ?",
    ]
    params: List[Any] = [access_token, refresh_token, utc_now_iso()]
    where = ["admin_site_id = ?", "channel_id = ?"]
    where_params: List[Any] = [int(admin_site_id), int(channel_id)]
    if expected_access_token is not None:
        where.append("COALESCE(access_token, '') = ?")
        where_params.append(str(expected_access_token or "").strip())
    if expected_refresh_token is not None:
        where.append("COALESCE(refresh_token, '') = ?")
        where_params.append(str(expected_refresh_token or "").strip())
    params.extend(where_params)
    db_execute_rowcount(
        f"UPDATE channel_upstream_bindings SET {', '.join(assignments)} WHERE {' AND '.join(where)}",
        tuple(params),
    )
