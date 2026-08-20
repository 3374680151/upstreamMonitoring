"""Transactional persistence for admin-site channel synchronisation."""

from __future__ import annotations

import hashlib
import json
from datetime import timedelta
from typing import Any, Optional

from backend.core.time import app_now, utc_now_iso
from backend.db import execute, execute_rowcount, query_all, query_one


def _positive_channel_id(value: Any) -> Optional[int]:
    if isinstance(value, bool):
        return None
    try:
        channel_id = int(value)
    except (TypeError, ValueError):
        return None
    return channel_id if channel_id > 0 else None


def stable_hash(value: Any) -> str:
    text = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class SyncRepository:
    """Own all SQL used by a complete main-site sync transaction."""

    def list_admin_sites(self) -> list[dict[str, Any]]:
        return query_all("SELECT * FROM admin_sites ORDER BY id")

    def get_admin_site(self, admin_site_id: int) -> Optional[dict[str, Any]]:
        return query_one("SELECT * FROM admin_sites WHERE id = ?", (int(admin_site_id),))

    def get_reconcile_mode(self, default: str = "disable") -> str:
        try:
            row = query_one(
                "SELECT value FROM app_settings WHERE name = ?",
                ("main_site_reconcile_mode",),
            )
        except Exception:
            return default
        value = str((row or {}).get("value") or default).strip().lower()
        return value if value in {"disable", "delete"} else default

    def previous_state(self, connection: Any, admin_site_id: int) -> Optional[dict[str, Any]]:
        return query_one(
            "SELECT channels_hash, groups_hash FROM admin_site_sync_state "
            "WHERE admin_site_id = ? FOR UPDATE",
            (int(admin_site_id),),
            connection=connection,
        )

    @staticmethod
    def _public_item(
        item: dict[str, Any], status: str, site_id: int | None = None, message: str = ""
    ) -> dict[str, Any]:
        result: dict[str, Any] = {
            "base_url": str(item.get("base_url") or ""),
            "name": str(item.get("name") or "").strip() or str(item.get("base_url") or ""),
            "channel_ids": list(item.get("channel_ids") or []),
            "status": status,
        }
        if site_id is not None:
            result["site_id"] = int(site_id)
        if message:
            result["message"] = message
        return result

    def import_candidate(
        self,
        connection: Any,
        admin_site_id: int,
        item: dict[str, Any],
        interval_minutes: int,
        platform: str = "newapi",
    ) -> dict[str, Any]:
        """Create/reuse a monitoring site and attach all source-channel links."""
        base_url = str(item.get("base_url") or "")
        name = str(item.get("name") or base_url)
        target_platform = str(platform or "newapi").strip().lower()
        if target_platform not in {"newapi", "sub2api"}:
            target_platform = "newapi"
        existing = query_one(
            "SELECT id, platform FROM sites "
            "WHERE base_url = ? OR TRIM(TRAILING '/' FROM base_url) = ? "
            "ORDER BY (base_url = ?) DESC LIMIT 1 FOR UPDATE",
            (base_url, base_url, base_url),
            connection=connection,
        )
        status = "existing"
        site_id: int
        if existing:
            site_id = int(existing.get("id") or 0)
        else:
            now = utc_now_iso()
            next_check_at = (
                app_now() + timedelta(minutes=max(1, int(interval_minutes)))
            ).isoformat(timespec="seconds")
            try:
                # Main-site discovery may identify either NewAPI or sub2api.
                # New rows must be browser-first so the explicit session-sync
                # flow can authenticate them; existing rows are never changed.
                site_id = execute(
                    "INSERT INTO sites "
                    "(name, base_url, platform, enabled, interval_minutes, login_enabled, "
                    "auth_mode, status, next_check_at, created_at, updated_at) "
                    "VALUES (?, ?, ?, 1, ?, 1, 'browser', 'unknown', ?, ?, ?)",
                    (
                        name[:255],
                        base_url,
                        target_platform,
                        max(1, int(interval_minutes)),
                        next_check_at,
                        now,
                        now,
                    ),
                    connection=connection,
                )
                status = "created"
            except Exception:
                refreshed = query_one(
                    "SELECT id, platform FROM sites "
                    "WHERE base_url = ? OR TRIM(TRAILING '/' FROM base_url) = ? LIMIT 1",
                    (base_url, base_url),
                    connection=connection,
                )
                if not refreshed:
                    raise
                site_id = int(refreshed.get("id") or 0)
                status = "existing"
        if site_id <= 0:
            return self._public_item(item, "conflict", message="监控站点 ID 无效")

        channel_names = item.get("channel_names")
        channel_names = channel_names if isinstance(channel_names, list) else []
        now = utc_now_iso()
        for index, raw_channel_id in enumerate(item.get("channel_ids") or []):
            channel_id = _positive_channel_id(raw_channel_id)
            if channel_id is None:
                continue
            channel_name = (
                str(channel_names[index] or "").strip() if index < len(channel_names) else ""
            ) or name
            execute(
                "INSERT INTO site_discovery_links "
                "(site_id, admin_site_id, channel_id, upstream_base_url, channel_name, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?) "
                "ON DUPLICATE KEY UPDATE upstream_base_url = VALUES(upstream_base_url), "
                "channel_name = VALUES(channel_name), updated_at = VALUES(updated_at)",
                (site_id, int(admin_site_id), channel_id, base_url, channel_name[:255], now, now),
                connection=connection,
            )
        return self._public_item(item, status, site_id=site_id)

    def reconcile_links(
        self,
        connection: Any,
        admin_site_id: int,
        candidates: list[dict[str, Any]],
        preferred_site_by_channel: Optional[dict[int, int]] = None,
    ) -> tuple[int, set[int]]:
        live_urls: dict[int, str] = {}
        for candidate in candidates or []:
            base_url = str(candidate.get("base_url") or "").strip().rstrip("/")
            for raw_id in candidate.get("channel_ids") or []:
                channel_id = _positive_channel_id(raw_id)
                if channel_id is not None:
                    live_urls[channel_id] = base_url
        preferred_site_by_channel = preferred_site_by_channel or {}
        rows = query_all(
            "SELECT id, site_id, channel_id, upstream_base_url FROM site_discovery_links WHERE admin_site_id = ?",
            (int(admin_site_id),),
            connection=connection,
        )
        stale: list[dict[str, Any]] = []
        affected: set[int] = set()
        live_by_channel: dict[int, list[dict[str, Any]]] = {}
        for row in rows:
            channel_id = _positive_channel_id(row.get("channel_id"))
            try:
                affected.add(int(row.get("site_id")))
            except (TypeError, ValueError):
                pass
            if channel_id is None or channel_id not in live_urls:
                stale.append(row)
                continue
            current_url = str(row.get("upstream_base_url") or "").strip().rstrip("/")
            live_url = live_urls.get(channel_id) or ""
            if live_url and current_url != live_url:
                stale.append(row)
            else:
                live_by_channel.setdefault(channel_id, []).append(row)
        for channel_id, channel_rows in live_by_channel.items():
            if len(channel_rows) < 2:
                continue
            preferred = preferred_site_by_channel.get(channel_id)
            canonical = next(
                (row for row in channel_rows if preferred is not None and int(row.get("site_id") or 0) == int(preferred)),
                None,
            ) or min(channel_rows, key=lambda row: int(row.get("id") or 0))
            canonical_id = int(canonical.get("id") or 0)
            stale.extend(row for row in channel_rows if int(row.get("id") or 0) != canonical_id)
        removed = 0
        for row in stale:
            removed += execute_rowcount(
                "DELETE FROM site_discovery_links WHERE id = ? AND admin_site_id = ?",
                (int(row.get("id") or 0), int(admin_site_id)),
                connection=connection,
            )
        return removed, affected

    def remove_stale_channel_data(
        self, connection: Any, admin_site_id: int, live_channel_ids: set[int]
    ) -> tuple[int, int]:
        removed_bindings = removed_keys = 0
        binding_rows = query_all(
            "SELECT channel_id FROM channel_upstream_bindings WHERE admin_site_id = ?",
            (int(admin_site_id),),
            connection=connection,
        )
        key_rows = query_all(
            "SELECT channel_id FROM admin_channel_keys WHERE admin_site_id = ?",
            (int(admin_site_id),),
            connection=connection,
        )
        for row in binding_rows:
            channel_id = _positive_channel_id(row.get("channel_id"))
            if channel_id is not None and channel_id in live_channel_ids:
                continue
            removed_bindings += execute_rowcount(
                "DELETE FROM channel_upstream_bindings WHERE admin_site_id = ? AND channel_id = ?",
                (int(admin_site_id), int(row.get("channel_id") or 0)),
                connection=connection,
            )
        for row in key_rows:
            channel_id = _positive_channel_id(row.get("channel_id"))
            if channel_id is not None and channel_id in live_channel_ids:
                continue
            removed_keys += execute_rowcount(
                "DELETE FROM admin_channel_keys WHERE admin_site_id = ? AND channel_id = ?",
                (int(admin_site_id), int(row.get("channel_id") or 0)),
                connection=connection,
            )
        return removed_bindings, removed_keys

    def apply_reconcile(
        self, connection: Any, admin_site_id: int, affected_site_ids: set[int], mode: str
    ) -> tuple[int, int, int]:
        linked_rows = query_all(
            "SELECT DISTINCT site_id FROM site_discovery_links WHERE admin_site_id = ?",
            (int(admin_site_id),),
            connection=connection,
        )
        for row in linked_rows:
            try:
                affected_site_ids.add(int(row.get("site_id")))
            except (TypeError, ValueError):
                pass
        disabled = reenabled = deleted = 0
        now = utc_now_iso()
        for site_id in sorted(affected_site_ids):
            site = query_one(
                "SELECT id, enabled, auto_disabled FROM sites WHERE id = ? FOR UPDATE",
                (int(site_id),),
                connection=connection,
            )
            if not site:
                continue
            remaining = query_one(
                "SELECT COUNT(*) AS count FROM site_discovery_links WHERE site_id = ?",
                (int(site_id),),
                connection=connection,
            )
            count = int((remaining or {}).get("count") or 0)
            if count > 0:
                if int(site.get("auto_disabled") or 0) == 1:
                    reenabled += execute_rowcount(
                        "UPDATE sites SET enabled = 1, auto_disabled = 0, updated_at = ? WHERE id = ?",
                        (now, int(site_id)),
                        connection=connection,
                    )
                continue
            if mode == "delete":
                deleted += execute_rowcount(
                    "DELETE FROM sites WHERE id = ?", (int(site_id),), connection=connection
                )
            elif int(site.get("enabled") or 0) == 1:
                disabled += execute_rowcount(
                    "UPDATE sites SET enabled = 0, auto_disabled = 1, updated_at = ? WHERE id = ?",
                    (now, int(site_id)),
                    connection=connection,
                )
        return disabled, reenabled, deleted

    def write_snapshot(
        self,
        connection: Any,
        admin_site_id: int,
        channels: list[dict[str, Any]],
        groups: dict[str, Any],
        previous: Optional[dict[str, Any]],
    ) -> dict[str, Any]:
        channels_json = json.dumps(channels, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        groups_json = json.dumps(groups, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        channels_hash = stable_hash(channels)
        groups_hash = stable_hash(groups)
        now = utc_now_iso()
        execute(
            "INSERT INTO admin_site_sync_state "
            "(admin_site_id, channels_json, groups_json, channels_hash, groups_hash, last_success_at, last_error, last_attempt_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, NULL, ?, ?) "
            "ON DUPLICATE KEY UPDATE channels_json = VALUES(channels_json), groups_json = VALUES(groups_json), "
            "channels_hash = VALUES(channels_hash), groups_hash = VALUES(groups_hash), last_success_at = VALUES(last_success_at), "
            "last_error = NULL, last_attempt_at = VALUES(last_attempt_at), updated_at = VALUES(updated_at)",
            (int(admin_site_id), channels_json, groups_json, channels_hash, groups_hash, now, now, now),
            connection=connection,
        )
        return {
            "channels_changed": not previous or previous.get("channels_hash") != channels_hash,
            "groups_changed": not previous or previous.get("groups_hash") != groups_hash,
        }

    def record_error(self, admin_site_id: int, message: str) -> None:
        now = utc_now_iso()
        try:
            execute(
                "INSERT INTO admin_site_sync_state "
                "(admin_site_id, channels_json, groups_json, channels_hash, groups_hash, last_success_at, last_error, last_attempt_at, updated_at) "
                "VALUES (?, '[]', '{}', ?, ?, NULL, ?, ?, ?) "
                "ON DUPLICATE KEY UPDATE last_error = VALUES(last_error), last_attempt_at = VALUES(last_attempt_at), updated_at = VALUES(updated_at)",
                (int(admin_site_id), stable_hash([]), stable_hash({}), str(message or "主站同步失败")[:4000], now, now),
            )
        except Exception:
            pass


__all__ = ["SyncRepository", "stable_hash"]
