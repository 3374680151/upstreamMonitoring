"""Persistence for discovery provenance and imported monitoring sites."""

from __future__ import annotations

from datetime import timedelta
from typing import Any
from urllib.parse import urlparse

from backend.core.time import app_now
from backend.db import execute, normalize_base_url, query_all, query_one, transaction, utc_now_iso


class DiscoveryRepository:
    @staticmethod
    def _normalized_url(value: Any) -> str:
        text = normalize_base_url(str(value or ""))
        try:
            parsed = urlparse(text)
            parsed.port
        except (TypeError, ValueError):
            return ""
        if (
            parsed.scheme.lower() not in {"http", "https"}
            or not parsed.netloc
            or not parsed.hostname
            or parsed.username
            or parsed.password
        ):
            return ""
        return text

    @staticmethod
    def _safe_display_url(value: Any) -> str:
        text = normalize_base_url(str(value or ""))
        try:
            parsed = urlparse(text)
            parsed.port
        except (TypeError, ValueError):
            return ""
        if parsed.username or parsed.password:
            if not parsed.hostname:
                return ""
            try:
                port = f":{parsed.port}" if parsed.port else ""
            except (TypeError, ValueError):
                port = ""
            return f"{parsed.scheme.lower()}://{parsed.hostname}{port}{parsed.path or ''}".rstrip("/")
        return text[:512]

    def enrich_candidates(
        self, candidates: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """Attach only redacted local status fields to discovery rows."""
        rows = query_all(
            """
            SELECT id, base_url, platform, status, enabled,
                   auth_mode, session_sync_status
            FROM sites
            WHERE platform = 'newapi'
            """
        )
        by_url: dict[str, dict[str, Any]] = {}
        for row in rows:
            normalized = self._normalized_url(row.get("base_url"))
            if normalized:
                by_url.setdefault(normalized, row)

        safe_keys = (
            "base_url",
            "name",
            "channel_ids",
            "channel_names",
            "channel_count",
        )
        enriched: list[dict[str, Any]] = []
        for candidate in candidates or []:
            if not isinstance(candidate, dict):
                continue
            safe = {key: candidate[key] for key in safe_keys if key in candidate}
            normalized = self._normalized_url(safe.get("base_url"))
            if normalized:
                safe["base_url"] = normalized
            elif "base_url" in safe:
                safe["base_url"] = self._safe_display_url(safe.get("base_url"))
            row = by_url.get(normalized) if normalized else None
            if row:
                safe["existing_site_id"] = row.get("id")
                # Authentication mode and sync state are non-secret metadata.
                # Preserve them so the discovery UI can retry an existing
                # browser-mode site without ever receiving its credentials.
                auth_mode = str(row.get("auth_mode") or "token").strip().lower()
                if auth_mode not in {"browser", "password", "token"}:
                    auth_mode = "token"
                sync_status = str(
                    row.get("session_sync_status") or "not_requested"
                ).strip().lower()
                safe["existing_site_auth_mode"] = auth_mode
                safe["existing_site_enabled"] = bool(row.get("enabled", True))
                safe["existing_site_session_sync_status"] = sync_status
                safe["existing_site_status"] = (
                    sync_status
                    if auth_mode == "browser" and sync_status != "not_requested"
                    else row.get("status") or "unknown"
                )
            else:
                safe["existing_site_id"] = None
                safe["existing_site_status"] = None
                safe["existing_site_auth_mode"] = None
                safe["existing_site_enabled"] = None
                safe["existing_site_session_sync_status"] = None
            safe["importable"] = True
            enriched.append(safe)
        return enriched

    def list_links(self, site_id: int) -> list[dict[str, Any]]:
        rows = query_all(
            """
            SELECT l.site_id, l.admin_site_id, a.name AS admin_site_name,
                   l.channel_id, l.channel_name, l.upstream_base_url,
                   l.created_at, l.updated_at
            FROM site_discovery_links l
            JOIN admin_sites a ON a.id = l.admin_site_id
            WHERE l.site_id = ?
            ORDER BY a.name, l.channel_name, l.channel_id
            """,
            (int(site_id),),
        )
        allowed = (
            "site_id",
            "admin_site_id",
            "admin_site_name",
            "channel_id",
            "channel_name",
            "upstream_base_url",
            "created_at",
            "updated_at",
        )
        return [{key: row.get(key) for key in allowed} for row in rows]

    def import_item(
        self,
        admin_site_id: int,
        item: dict[str, Any],
        interval_minutes: int,
    ) -> tuple[str, int | None, str]:
        """Create or reuse one monitoring site and persist its provenance.

        The site record and every source-channel link share one transaction.
        Existing sites retain their configuration and credentials; importing a
        candidate only adds or refreshes its provenance rows.
        """
        base_url = str(item["base_url"])
        name = str(item["name"])
        channel_ids = [int(value) for value in item.get("channel_ids") or []]
        channel_names = item.get("channel_names")
        channel_names = channel_names if isinstance(channel_names, list) else []

        with transaction() as conn:
            existing = query_one(
                """
                SELECT id, platform FROM sites
                WHERE base_url = ? OR TRIM(TRAILING '/' FROM base_url) = ?
                ORDER BY (base_url = ?) DESC
                LIMIT 1 FOR UPDATE
                """,
                (base_url, base_url, base_url),
                connection=conn,
            )
            if existing:
                platform = str(existing.get("platform") or "newapi").strip().lower()
                if platform != "newapi":
                    return "conflict", int(existing["id"]), "同 URL 已存在非 NewAPI 监控站点"
                site_id = int(existing["id"])
                item_status = "existing"
            else:
                now = utc_now_iso()
                next_check_at = (
                    app_now() + timedelta(minutes=max(1, int(interval_minutes)))
                ).isoformat(timespec="seconds")
                try:
                    # A discovered site starts in browser-first mode.  The
                    # browser bridge is an explicit, user-triggered flow;
                    # until it completes the site remains a harmless
                    # ``unknown`` row and the normal auth service can report
                    # ``no_session`` without exposing credentials.
                    site_id = execute(
                        """
                        INSERT INTO sites
                        (name, base_url, platform, enabled, interval_minutes,
                         login_enabled, auth_mode, status, next_check_at,
                         created_at, updated_at)
                        VALUES (?, ?, 'newapi', 1, ?, 1, 'browser', 'unknown', ?, ?, ?)
                        """,
                        (
                            name[:255],
                            base_url,
                            int(interval_minutes),
                            next_check_at,
                            now,
                            now,
                        ),
                        connection=conn,
                    )
                    if not site_id:
                        return "conflict", None, "创建监控站点失败"
                    item_status = "created"
                except Exception:
                    # A concurrent import may have won the unique base_url
                    # race. Reuse that NewAPI site, but do not mask unrelated
                    # rows as a successful import.
                    refreshed = query_one(
                        """
                        SELECT id, platform FROM sites
                        WHERE base_url = ? OR TRIM(TRAILING '/' FROM base_url) = ?
                        LIMIT 1
                        """,
                        (base_url, base_url),
                        connection=conn,
                    )
                    if not refreshed:
                        raise
                    platform = str(refreshed.get("platform") or "newapi").strip().lower()
                    if platform != "newapi":
                        return "conflict", int(refreshed["id"]), "同 URL 已存在非 NewAPI 监控站点"
                    site_id = int(refreshed["id"])
                    item_status = "existing"

            now = utc_now_iso()
            for index, channel_id in enumerate(channel_ids):
                channel_name = (
                    str(channel_names[index] or "").strip()
                    if index < len(channel_names)
                    else ""
                ) or name
                execute(
                    """
                    INSERT INTO site_discovery_links
                    (site_id, admin_site_id, channel_id, upstream_base_url,
                     channel_name, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    ON DUPLICATE KEY UPDATE
                      upstream_base_url = VALUES(upstream_base_url),
                      channel_name = VALUES(channel_name),
                      updated_at = VALUES(updated_at)
                    """,
                    (
                        site_id,
                        int(admin_site_id),
                        channel_id,
                        base_url,
                        channel_name[:255],
                        now,
                        now,
                    ),
                    connection=conn,
                )
        return item_status, site_id, ""
