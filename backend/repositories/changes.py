"""Snapshot and change-history persistence."""

from __future__ import annotations

import json
from typing import Any, Iterable

from backend.db import execute, query_all, query_one, transaction, utc_now_iso


class ChangeRepository:
    def list(self, limit: int = 100) -> list[dict[str, Any]]:
        return query_all(
            "SELECT * FROM changes ORDER BY id DESC LIMIT ?",
            (limit,),
        )

    def list_for_site(self, site_id: int, limit: int = 100) -> list[dict[str, Any]]:
        return query_all(
            """
            SELECT * FROM changes
            WHERE site_id = ?
            ORDER BY id DESC
            LIMIT ?
            """,
            (site_id, limit),
        )

    def snapshots_for_site(self, site_id: int) -> list[dict[str, Any]]:
        return query_all(
            """
            SELECT * FROM snapshots
            WHERE site_id = ?
            ORDER BY id DESC
            LIMIT 100
            """,
            (site_id,),
        )

    def latest_success_snapshot(self, site_id: int) -> dict[str, Any] | None:
        return query_one(
            """
            SELECT * FROM snapshots
            WHERE site_id = ? AND status = 'success'
            ORDER BY id DESC LIMIT 1
            """,
            (int(site_id),),
        )

    def record_check(
        self,
        *,
        site_id: int,
        checked_at: str,
        source: str,
        payload: Any,
        groups: dict[str, Any] | None,
        status: str,
        error_message: str | None,
        site_updates: dict[str, Any],
        changes: Iterable[dict[str, Any]] = (),
    ) -> None:
        """Persist snapshot, changes, and site status atomically."""
        groups_json = (
            json.dumps(groups, ensure_ascii=False, sort_keys=True)
            if groups is not None
            else None
        )
        raw_json = json.dumps(payload, ensure_ascii=False)
        snapshot_hash = None
        if groups is not None:
            import hashlib

            snapshot_hash = hashlib.sha256(
                json.dumps(groups, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
            ).hexdigest()
        with transaction() as conn:
            if status == "success":
                execute(
                    """
                    INSERT INTO snapshots
                    (site_id, status, source, groups_json, raw_json, hash,
                     error_message, checked_at)
                    VALUES (?, 'success', ?, ?, ?, ?, NULL, ?)
                    """,
                    (site_id, source, groups_json, raw_json, snapshot_hash, checked_at),
                    connection=conn,
                )
            else:
                execute(
                    """
                    INSERT INTO snapshots
                    (site_id, status, source, raw_json, error_message, checked_at, hash)
                    VALUES (?, 'failed', ?, ?, ?, ?, NULL)
                    """,
                    (site_id, source, raw_json, error_message, checked_at),
                    connection=conn,
                )
            for change in changes:
                execute(
                    """
                    INSERT INTO changes
                    (site_id, change_type, group_name, old_value, new_value,
                     change_percent, message, created_at, acknowledged)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0)
                    """,
                    (
                        site_id,
                        change.get("change_type"),
                        change.get("group_name"),
                        json.dumps(change.get("old_value"), ensure_ascii=False)
                        if change.get("old_value") is not None else None,
                        json.dumps(change.get("new_value"), ensure_ascii=False)
                        if change.get("new_value") is not None else None,
                        change.get("change_percent"),
                        change.get("message") or "",
                        checked_at,
                    ),
                    connection=conn,
                )
            from backend.repositories.sites import SiteRepository

            SiteRepository().update_fields(site_id, site_updates, connection=conn)
