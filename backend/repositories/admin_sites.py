"""Repository layer for admin sites, channel upstream bindings, and
the NewAPI channel-key cache.

Three classes live here, all backed by the shared database adapter.  Once the
integrations layer is fully migrated the service layer should be the only
caller of these methods.

Design rules:

* Every method returns a plain ``dict`` / list of ``dict`` (no ORM models).
* Every method that mutates state is wrapped in a transaction when more
  than one row changes (the legacy handler did not do this, which is
  part of the tech debt this layer pays down).
* All SQL is **only** inside this file.  Nothing else in the codebase should
  query the admin-site tables directly.
"""

from __future__ import annotations

import json
from typing import Any, Iterable, Optional

from backend.db import (
    execute,
    execute_rowcount,
    normalize_base_url,
    query_all,
    query_one,
    transaction,
    utc_now_iso,
)


def _row_value(value: Any) -> Any:
    """Normalise MySQL row values for pydantic consumption."""
    if value is None:
        return None
    if isinstance(value, (str, int, float, bool)):
        return value
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


def _row_to_dict(row: Any) -> dict[str, Any]:
    """Convert a DB row into a JSON-serialisable dict."""
    if row is None:
        return {}
    return {key: _row_value(val) for key, val in row.items()}


def _row_to_dicts(rows: Iterable[Any]) -> list[dict[str, Any]]:
    return [_row_to_dict(r) for r in rows]


# ---------------------------------------------------------------------------
# AdminSiteRepository
# ---------------------------------------------------------------------------


ADMIN_SITE_MUTABLE_COLUMNS: frozenset[str] = frozenset(
    {
        "name",
        "base_url",
        "access_token",
        "access_user_id",
        "login_username",
        "login_password",
        "sub2api_access_token",
        "sub2api_refresh_token",
        "sub2api_access_expires_at",
        "browser_access_token",
        "browser_refresh_cookie",
        "browser_session_id",
        "browser_access_expires_at",
        "browser_login_last_error",
        "browser_login_last_check_at",
        "security_proof",
        "security_proof_verified_at",
        "key_sync_enabled",
        "key_sync_interval_minutes",
        "key_sync_next_at",
        "key_sync_last_error",
        "key_sync_backoff_until",
        "key_sync_failure_count",
    }
)


class AdminSiteRepository:
    """All SQL touching the ``admin_sites`` table."""

    def list(self) -> list[dict[str, Any]]:
        rows = query_all("SELECT * FROM admin_sites ORDER BY id DESC")
        return _row_to_dicts(rows)

    def list_due_key_syncs(self, now_iso: str) -> list[dict[str, Any]]:
        """Return NewAPI admin sites whose protected-key sync is due.

        Scheduling state belongs to the admin-site repository.  The worker
        should not need to know the table schema or build SQL itself.
        """
        rows = query_all(
            """
            SELECT * FROM admin_sites
            WHERE platform = 'newapi' AND key_sync_enabled = 1
              AND (key_sync_next_at IS NULL OR key_sync_next_at <= ?)
              AND (key_sync_backoff_until IS NULL OR key_sync_backoff_until <= ?)
            ORDER BY COALESCE(key_sync_next_at, '') ASC, id ASC
            """,
            (str(now_iso), str(now_iso)),
        )
        return _row_to_dicts(rows)

    def mark_key_sync_success(
        self, admin_site_id: int, now_iso: str, next_at: str | None
    ) -> int:
        return execute_rowcount(
            """
            UPDATE admin_sites
            SET key_sync_last_at = ?, key_sync_next_at = ?,
                key_sync_last_error = NULL, key_sync_backoff_until = NULL,
                key_sync_failure_count = 0, updated_at = ?
            WHERE id = ?
            """,
            (
                str(now_iso),
                str(next_at) if next_at else None,
                str(now_iso),
                int(admin_site_id),
            ),
        )

    def mark_key_sync_failure(
        self,
        admin_site_id: int,
        now_iso: str,
        next_at: str,
        message: str,
        failure_count: int,
    ) -> int:
        return execute_rowcount(
            """
            UPDATE admin_sites
            SET key_sync_last_at = ?, key_sync_next_at = ?,
                key_sync_last_error = ?, key_sync_backoff_until = ?,
                key_sync_failure_count = ?, updated_at = ?
            WHERE id = ?
            """,
            (
                str(now_iso),
                str(next_at),
                str(message),
                str(next_at),
                max(0, int(failure_count)),
                str(now_iso),
                int(admin_site_id),
            ),
        )

    def record_key_refresh_error(
        self, admin_site_id: int, message: str, now_iso: str
    ) -> int:
        """Record an immediate post-2FA key fetch failure without backoff."""
        return execute_rowcount(
            """
            UPDATE admin_sites
            SET key_sync_last_at = ?, key_sync_last_error = ?, updated_at = ?
            WHERE id = ?
            """,
            (str(now_iso), str(message), str(now_iso), int(admin_site_id)),
        )

    def get(self, admin_site_id: int) -> Optional[dict[str, Any]]:
        row = query_one(
            "SELECT * FROM admin_sites WHERE id = ?", (int(admin_site_id),)
        )
        return _row_to_dict(row) if row else None

    def persist_sub2api_auth(
        self, admin_site_id: int, auth: dict[str, Any], now: str
    ) -> int:
        """Persist a rotated sub2api management session."""
        return execute_rowcount(
            """
            UPDATE admin_sites
            SET sub2api_access_token = ?, sub2api_refresh_token = ?,
                sub2api_access_expires_at = ?, browser_login_last_error = NULL,
                browser_login_last_check_at = ?, updated_at = ?
            WHERE id = ? AND platform = 'sub2api'
            """,
            (
                str(auth.get("access_token") or "").strip(),
                str(auth.get("refresh_token") or "").strip(),
                int(auth.get("access_expires_at") or 0),
                str(now),
                str(now),
                int(admin_site_id),
            ),
        )

    def persist_sub2api_error(
        self, admin_site_id: int, message: str, now: str
    ) -> int:
        return execute_rowcount(
            """
            UPDATE admin_sites
            SET browser_login_last_error = ?, browser_login_last_check_at = ?,
                updated_at = ?
            WHERE id = ? AND platform = 'sub2api'
            """,
            (str(message), str(now), str(now), int(admin_site_id)),
        )

    def persist_newapi_browser_session(
        self,
        admin_site_id: int,
        session: dict[str, Any],
        now: str,
        *,
        expected_access_token: str | None = None,
        expected_refresh_cookie: str | None = None,
        expected_session_id: str | None = None,
    ) -> int:
        """Persist an admin NewAPI browser session without touching PAT fields.

        The expected browser fields form an optimistic-concurrency cursor. A
        refresh token/cookie bundle is rotated by the upstream, so an older
        in-flight request must never overwrite a bundle written by a newer
        request in another process.
        """
        try:
            expires_at = int(session.get("browser_access_expires_at") or 0)
        except (TypeError, ValueError):
            expires_at = 0
        params: list[Any] = [
            str(session.get("access_token") or "").strip(),
            str(
                session.get("browser_refresh_cookie")
                or session.get("refresh_cookie")
                or ""
            ).strip(),
            str(
                session.get("browser_session_id")
                or session.get("session_id")
                or ""
            ).strip(),
            expires_at,
            str(now),
            str(now),
            int(admin_site_id),
        ]
        where = ["id = ?", "platform = 'newapi'"]
        if expected_access_token is not None:
            where.append("COALESCE(browser_access_token, '') = ?")
            params.append(str(expected_access_token or "").strip())
        if expected_refresh_cookie is not None:
            where.append("COALESCE(browser_refresh_cookie, '') = ?")
            params.append(str(expected_refresh_cookie or "").strip())
        if expected_session_id is not None:
            where.append("COALESCE(browser_session_id, '') = ?")
            params.append(str(expected_session_id or "").strip())
        return execute_rowcount(
            """
            UPDATE admin_sites
            SET browser_access_token = ?, browser_refresh_cookie = ?,
                browser_session_id = ?, browser_access_expires_at = ?,
                browser_login_last_error = NULL, browser_login_last_check_at = ?,
                updated_at = ?
            WHERE """ + " AND ".join(where),
            tuple(params),
        )

    def persist_newapi_browser_error(
        self,
        admin_site_id: int,
        message: str,
        now: str,
        *,
        expected_access_token: str | None = None,
        expected_refresh_cookie: str | None = None,
        expected_session_id: str | None = None,
    ) -> int:
        params: list[Any] = [str(message), str(now), str(now), int(admin_site_id)]
        where = ["id = ?", "platform = 'newapi'"]
        if expected_access_token is not None:
            where.append("COALESCE(browser_access_token, '') = ?")
            params.append(str(expected_access_token or "").strip())
        if expected_refresh_cookie is not None:
            where.append("COALESCE(browser_refresh_cookie, '') = ?")
            params.append(str(expected_refresh_cookie or "").strip())
        if expected_session_id is not None:
            where.append("COALESCE(browser_session_id, '') = ?")
            params.append(str(expected_session_id or "").strip())
        return execute_rowcount(
            """
            UPDATE admin_sites
            SET browser_login_last_error = ?, browser_login_last_check_at = ?,
                updated_at = ?
            WHERE """ + " AND ".join(where),
            tuple(params),
        )

    def update_security_proof(
        self, admin_site_id: int, proof: str, verified_at: str,
        *, next_at: str | None = None, backoff_until: str | None = None
    ) -> int:
        return execute_rowcount(
            """
            UPDATE admin_sites
            SET security_proof = ?, security_proof_verified_at = ?,
                key_sync_next_at = CASE
                    WHEN key_sync_enabled = 1 AND ? IS NOT NULL THEN ?
                    ELSE key_sync_next_at
                END,
                key_sync_last_error = NULL,
                key_sync_backoff_until = CASE
                    WHEN key_sync_enabled = 1 THEN ?
                    ELSE NULL
                END,
                key_sync_failure_count = 0,
                updated_at = ?
            WHERE id = ? AND platform = 'newapi'
            """,
            (
                str(proof or "").strip(),
                str(verified_at),
                next_at,
                next_at,
                backoff_until,
                str(verified_at),
                int(admin_site_id),
            ),
        )

    def clear_security_proof(self, admin_site_id: int, now: str) -> int:
        return execute_rowcount(
            """
            UPDATE admin_sites
            SET security_proof = NULL, security_proof_verified_at = NULL,
                updated_at = ?
            WHERE id = ? AND platform = 'newapi'
            """,
            (str(now), int(admin_site_id)),
        )

    def create(self, fields: dict[str, Any]) -> int:
        """Insert an admin site row.

        ``fields`` must already contain the column whitelist — the caller
        is responsible for stripping unknown keys.  This is intentional:
        the service layer has the schema knowledge, the repository just
        maps dict → SQL.
        """
        if not fields.get("name") or not fields.get("base_url"):
            raise ValueError("name and base_url are required")
        now = utc_now_iso()
        columns = ["name", "platform", "base_url"]
        values: list[Any] = [
            str(fields.get("name") or "").strip(),
            str(fields.get("platform") or "newapi").strip().lower(),
            normalize_base_url(str(fields.get("base_url") or "")),
        ]
        for col in (
            "access_token",
            "access_user_id",
            "login_username",
            "login_password",
            "sub2api_access_token",
            "sub2api_refresh_token",
            "sub2api_access_expires_at",
        ):
            if col in fields:
                columns.append(col)
                value = fields.get(col)
                values.append(
                    value
                    if col == "sub2api_access_expires_at"
                    else (value or "")
                )

        if "key_sync_enabled" in fields:
            columns.append("key_sync_enabled")
            key_sync_enabled = bool(fields.get("key_sync_enabled"))
            values.append(1 if key_sync_enabled else 0)
            if key_sync_enabled:
                columns.append("key_sync_next_at")
                values.append(fields.get("key_sync_next_at") or now)
        if "key_sync_interval_minutes" in fields:
            columns.append("key_sync_interval_minutes")
            try:
                values.append(
                    max(5, min(1440, int(fields.get("key_sync_interval_minutes") or 5)))
                )
            except (TypeError, ValueError):
                values.append(5)
        for col in ("browser_login_last_check_at", "browser_login_last_error"):
            if col in fields:
                columns.append(col)
                values.append(fields.get(col) or "")

        columns.extend(["created_at", "updated_at"])
        values.extend([now, now])

        placeholders = ", ".join(["?"] * len(columns))
        column_list = ", ".join(columns)
        new_id = execute(
            f"INSERT INTO admin_sites ({column_list}) VALUES ({placeholders})",
            tuple(values),
        )
        return int(new_id)

    def update(self, admin_site_id: int, fields: dict[str, Any]) -> int:
        """Whitelist-only UPDATE.  Returns affected row count.

        Unknown columns in ``fields`` are silently dropped — the legacy
        handler was more permissive and that's how platform leaks
        slipped in.  Hard fail on the platform column to surface the
        409 contract.

        ``None`` values in ``fields`` are written as SQL NULL (so the
        caller can clear optional browser-session columns without
        falling back to an empty string).
        """
        if "platform" in fields:
            raise ValueError("platform 不可修改")

        updates: list[str] = []
        params: list[Any] = []
        for column, value in fields.items():
            if column not in ADMIN_SITE_MUTABLE_COLUMNS:
                continue
            if column == "base_url" and value:
                value = normalize_base_url(str(value))
            elif column == "key_sync_enabled":
                value = 1 if value else 0
            elif column == "key_sync_interval_minutes" and value is not None:
                try:
                    value = max(5, min(1440, int(value)))
                except (TypeError, ValueError):
                    value = 5
            updates.append(f"{column} = ?")
            params.append(value)

        if not updates:
            return 0

        updates.append("updated_at = ?")
        params.append(utc_now_iso())
        params.append(int(admin_site_id))

        return execute_rowcount(
            f"UPDATE admin_sites SET {', '.join(updates)} WHERE id = ?",
            tuple(params),
        )

    def delete(self, admin_site_id: int) -> None:
        """Atomic delete: bindings → key cache → admin_site.

        This is intentionally one transaction so a failed delete cannot leave
        orphan bindings or key-cache rows.
        """
        with transaction() as conn:
            execute(
                "DELETE FROM channel_upstream_bindings WHERE admin_site_id = ?",
                (int(admin_site_id),),
                connection=conn,
            )
            execute(
                "DELETE FROM admin_channel_keys WHERE admin_site_id = ?",
                (int(admin_site_id),),
                connection=conn,
            )
            execute(
                "DELETE FROM admin_sites WHERE id = ?",
                (int(admin_site_id),),
                connection=conn,
            )


# ---------------------------------------------------------------------------
# ChannelKeyCacheRepository
# ---------------------------------------------------------------------------


class ChannelKeyCacheRepository:
    """Cache of plaintext NewAPI channel keys (read from upstream)."""

    def get(self, admin_site_id: int, channel_id: int) -> str:
        """Return the cached plaintext key, or empty string.

        Masked values are normalised to empty so callers never accidentally
        push a ``sk-****-****`` back to the upstream API.
        """
        row = query_one(
            "SELECT channel_key FROM admin_channel_keys WHERE admin_site_id = ? AND channel_id = ?",
            (int(admin_site_id), int(channel_id)),
        )
        if not row:
            return ""
        key = str(row.get("channel_key") or "").strip()
        return "" if _channel_key_is_masked(key) else key

    def fetched_at_map(self, admin_site_id: int) -> dict[int, str]:
        """Return fetch timestamps used to choose the next refresh candidate."""
        rows = query_all(
            """
            SELECT channel_id, fetched_at
            FROM admin_channel_keys
            WHERE admin_site_id = ?
            """,
            (int(admin_site_id),),
        )
        result: dict[int, str] = {}
        for row in rows:
            try:
                channel_id = int(row.get("channel_id") or 0)
            except (TypeError, ValueError):
                continue
            if channel_id > 0:
                result[channel_id] = str(row.get("fetched_at") or "")
        return result

    def upsert(self, admin_site_id: int, channel_id: int, key: str) -> None:
        """Persist a plaintext key.  Masked keys clear the row instead."""
        clean = str(key or "").strip()
        if not clean:
            self.clear(admin_site_id, channel_id)
            return
        if _channel_key_is_masked(clean):
            self.clear(admin_site_id, channel_id)
            return
        now = utc_now_iso()
        execute(
            """
            INSERT INTO admin_channel_keys
                (admin_site_id, channel_id, channel_key, fetched_at, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON DUPLICATE KEY UPDATE
                channel_key = VALUES(channel_key),
                fetched_at = VALUES(fetched_at),
                updated_at = VALUES(updated_at)
            """,
            (int(admin_site_id), int(channel_id), clean, now, now, now),
        )

    def clear(self, admin_site_id: int, channel_id: int) -> None:
        execute(
            "DELETE FROM admin_channel_keys WHERE admin_site_id = ? AND channel_id = ?",
            (int(admin_site_id), int(channel_id)),
        )

    def clear_many(self, admin_site_id: int, channel_ids: Iterable[int]) -> None:
        ids = [int(cid) for cid in channel_ids]
        if not ids:
            return
        placeholders = ", ".join(["?"] * len(ids))
        execute(
            f"DELETE FROM admin_channel_keys WHERE admin_site_id = ? AND channel_id IN ({placeholders})",
            tuple([int(admin_site_id), *ids]),
        )


# ---------------------------------------------------------------------------
# ChannelUpstreamBindingRepository
# ---------------------------------------------------------------------------


def _channel_key_is_masked(value: Any) -> bool:
    """Match NewAPI's masked key shape: contains ``****`` or is empty / ``-``."""
    text = str(value or "").strip()
    return not text or "****" in text or text == "-"


class ChannelUpstreamBindingRepository:
    """All SQL touching the ``channel_upstream_bindings`` table."""

    def list_by_site(self, admin_site_id: int) -> list[dict[str, Any]]:
        rows = query_all(
            "SELECT * FROM channel_upstream_bindings WHERE admin_site_id = ?",
            (int(admin_site_id),),
        )
        return _row_to_dicts(rows)

    def get(self, admin_site_id: int, channel_id: int) -> Optional[dict[str, Any]]:
        row = query_one(
            "SELECT * FROM channel_upstream_bindings WHERE admin_site_id = ? AND channel_id = ?",
            (int(admin_site_id), int(channel_id)),
        )
        return _row_to_dict(row) if row else None

    def delete(self, admin_site_id: int, channel_id: int) -> None:
        """Remove a binding.  Idempotent."""
        execute(
            "DELETE FROM channel_upstream_bindings WHERE admin_site_id = ? AND channel_id = ?",
            (int(admin_site_id), int(channel_id)),
        )

    def delete_many(self, admin_site_id: int, channel_ids: Iterable[int]) -> None:
        ids = [int(cid) for cid in channel_ids]
        if not ids:
            return
        placeholders = ", ".join(["?"] * len(ids))
        execute(
            f"DELETE FROM channel_upstream_bindings WHERE admin_site_id = ? AND channel_id IN ({placeholders})",
            tuple([int(admin_site_id), *ids]),
        )

    def upsert(
        self,
        admin_site_id: int,
        channel_id: int,
        fields: dict[str, Any],
        match_status: str = "unmatched",
        match_message: str = "待匹配",
    ) -> dict[str, Any]:
        """Insert or update a binding row.

        ``fields`` is the validated merge from ``save_channel_upstream_binding``
        (the merge rules in the legacy handler are migrated into the service
        layer).  This method just writes what it's given.
        """
        now = utc_now_iso()
        matched_groups_json = fields.get("matched_groups_json")
        if matched_groups_json is not None and not isinstance(matched_groups_json, str):
            matched_groups_json = json.dumps(matched_groups_json, ensure_ascii=False)

        columns = [
            "admin_site_id",
            "channel_id",
            "upstream_base_url",
            "upstream_platform",
            "auth_mode",
            "login_username",
            "login_password",
            "access_token",
            "access_user_id",
            "refresh_token",
            "channel_key",
            "match_status",
            "match_message",
            "matched_groups_json",
            "matched_at",
            "created_at",
            "updated_at",
        ]
        values: list[Any] = [
            int(admin_site_id),
            int(channel_id),
            str(fields.get("upstream_base_url") or ""),
            str(fields.get("upstream_platform") or "newapi"),
            str(fields.get("auth_mode") or "token"),
            str(fields.get("login_username") or ""),
            str(fields.get("login_password") or ""),
            str(fields.get("access_token") or ""),
            str(fields.get("access_user_id") or ""),
            str(fields.get("refresh_token") or ""),
            str(fields.get("channel_key") or ""),
            match_status,
            match_message,
            matched_groups_json,
            fields.get("matched_at") or None,
            now,
            now,
        ]
        placeholders = ", ".join(["?"] * len(columns))
        column_list = ", ".join(columns)
        update_clause = ", ".join(
            f"{col} = VALUES({col})"
            for col in columns
            if col not in ("admin_site_id", "channel_id", "created_at")
        )
        execute(
            f"""
            INSERT INTO channel_upstream_bindings ({column_list})
            VALUES ({placeholders})
            ON DUPLICATE KEY UPDATE {update_clause}
            """,
            tuple(values),
        )
        row = self.get(admin_site_id, channel_id)
        return row or {}

    def update_match_result(
        self,
        admin_site_id: int,
        channel_id: int,
        match_status: str,
        match_message: str,
        matched_groups: list[dict[str, Any]],
    ) -> None:
        """Write the result of a match attempt without touching credentials."""
        execute(
            """
            UPDATE channel_upstream_bindings
            SET match_status = ?,
                match_message = ?,
                matched_groups_json = ?,
                matched_at = ?,
                updated_at = ?
            WHERE admin_site_id = ? AND channel_id = ?
            """,
            (
                match_status,
                match_message,
                json.dumps(matched_groups, ensure_ascii=False),
                utc_now_iso(),
                utc_now_iso(),
                int(admin_site_id),
                int(channel_id),
            ),
        )

    def persist_match_result(
        self,
        admin_site_id: int,
        channel_id: int,
        match_status: str,
        match_message: str,
        matched_groups: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Persist matching state while retaining stale successful groups.

        Network and security failures must never erase useful last-known
        group mappings.  This is the repository equivalent of the former
        monolith's channel match persistence rule.
        """
        stale_statuses = {
            "error",
            "refresh_error",
            "needs_key_verification",
            "missing_key",
        }
        now = utc_now_iso()
        with transaction() as conn:
            row = self.get_for_update(admin_site_id, channel_id, conn)
            if not row:
                execute(
                    """
                    INSERT INTO channel_upstream_bindings
                    (admin_site_id, channel_id, created_at, updated_at)
                    VALUES (?, ?, ?, ?)
                    ON DUPLICATE KEY UPDATE channel_id = VALUES(channel_id)
                    """,
                    (int(admin_site_id), int(channel_id), now, now),
                    connection=conn,
                )
                row = self.get_for_update(admin_site_id, channel_id, conn) or {}
            try:
                previous = json.loads(row.get("matched_groups_json") or "[]")
            except (TypeError, ValueError):
                previous = []
            if not isinstance(previous, list):
                previous = []
            if match_status in stale_statuses and not matched_groups and previous:
                execute(
                    """
                    UPDATE channel_upstream_bindings
                    SET match_status = ?, match_message = ?, updated_at = ?
                    WHERE admin_site_id = ? AND channel_id = ?
                    """,
                    (
                        str(match_status),
                        str(match_message),
                        now,
                        int(admin_site_id),
                        int(channel_id),
                    ),
                    connection=conn,
                )
                return [item for item in previous if isinstance(item, dict)]

            effective = [dict(item) for item in matched_groups if isinstance(item, dict)]
            if match_status == "matched_partial" and previous:
                previous_by_name = {
                    str(item.get("name") or ""): item
                    for item in previous
                    if isinstance(item, dict) and item.get("name")
                }
                for item in effective:
                    prior = previous_by_name.get(str(item.get("name") or ""))
                    if prior and item.get("ratio") in (None, ""):
                        item["ratio"] = prior.get("ratio")
                        if prior.get("ratio_type"):
                            item["ratio_type"] = prior.get("ratio_type")
            execute(
                """
                UPDATE channel_upstream_bindings
                SET match_status = ?, match_message = ?, matched_groups_json = ?,
                    matched_at = ?, updated_at = ?
                WHERE admin_site_id = ? AND channel_id = ?
                """,
                (
                    str(match_status),
                    str(match_message),
                    json.dumps(effective, ensure_ascii=False),
                    now,
                    now,
                    int(admin_site_id),
                    int(channel_id),
                ),
                connection=conn,
            )
            return effective

    def persist_refreshed_auth(
        self,
        admin_site_id: int,
        channel_id: int,
        auth: dict[str, Any],
        *,
        expected_access_token: str | None = None,
        expected_refresh_token: str | None = None,
    ) -> int:
        """CAS-persist a rotated sub2api binding session.

        A stale in-flight refresh must not overwrite a newer manual edit or a
        subsequently rotated refresh token.
        """
        if not isinstance(auth, dict):
            return 0
        access_token = str(auth.get("access_token") or "").strip()
        refresh_token = str(auth.get("refresh_token") or "").strip()
        if not access_token and not refresh_token:
            return 0
        assignments = [
            "access_token = COALESCE(NULLIF(?, ''), access_token)",
            "refresh_token = COALESCE(NULLIF(?, ''), refresh_token)",
            "updated_at = ?",
        ]
        params: list[Any] = [access_token, refresh_token, utc_now_iso()]
        where = [
            "admin_site_id = ?",
            "channel_id = ?",
            "upstream_platform = 'sub2api'",
        ]
        where_params: list[Any] = [int(admin_site_id), int(channel_id)]
        if expected_access_token is not None:
            where.append("COALESCE(access_token, '') = ?")
            where_params.append(str(expected_access_token or "").strip())
        if expected_refresh_token is not None:
            where.append("COALESCE(refresh_token, '') = ?")
            where_params.append(str(expected_refresh_token or "").strip())
        params.extend(where_params)
        return execute_rowcount(
            f"UPDATE channel_upstream_bindings SET {', '.join(assignments)} WHERE {' AND '.join(where)}",
            tuple(params),
        )

    def get_for_update(
        self, admin_site_id: int, channel_id: int, connection: Any
    ) -> Optional[dict[str, Any]]:
        row = query_one(
            """
            SELECT * FROM channel_upstream_bindings
            WHERE admin_site_id = ? AND channel_id = ? FOR UPDATE
            """,
            (int(admin_site_id), int(channel_id)),
            connection=connection,
        )
        return _row_to_dict(row) if row else None
