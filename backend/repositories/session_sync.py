"""Persistence for browser-session synchronisation requests."""

from __future__ import annotations

from typing import Any

from backend.db import execute, execute_rowcount, query_one, transaction


class SessionSyncRepository:
    """Own SQL for request lifecycle and its local target state."""

    def get(self, request_id: str, *, connection: Any = None) -> dict[str, Any] | None:
        return query_one(
            "SELECT * FROM browser_session_sync_requests WHERE id = ?",
            (str(request_id),),
            connection=connection,
        )

    def get_for_target(
        self, target_kind: str, target_id: int, request_id: str
    ) -> dict[str, Any] | None:
        if target_kind == "site":
            return query_one(
                """
                SELECT * FROM browser_session_sync_requests
                WHERE id = ? AND site_id = ? AND admin_site_id IS NULL
                """,
                (str(request_id), int(target_id)),
            )
        if target_kind == "admin_site":
            return query_one(
                """
                SELECT * FROM browser_session_sync_requests
                WHERE id = ? AND admin_site_id = ? AND site_id IS NULL
                """,
                (str(request_id), int(target_id)),
            )
        return None

    def claim_pending(self, request_id: str, now: str) -> dict[str, Any] | None:
        """Atomically move one pending request into upstream validation.

        Secret comparison and expiry policy stay in the service layer.  This
        method owns the conditional state transition so a second completion
        process cannot claim the same one-time request.
        """
        with transaction() as conn:
            row = self.get(str(request_id), connection=conn)
            if not row or str(row.get("status") or "") != "pending":
                return None
            changed = execute_rowcount(
                """
                UPDATE browser_session_sync_requests
                SET status = 'validating', updated_at = ?, consumed_at = ?
                WHERE id = ? AND status = 'pending'
                """,
                (str(now), str(now), str(request_id)),
                connection=conn,
            )
            if changed <= 0:
                return None
            if row.get("site_id") is not None:
                execute_rowcount(
                    """
                    UPDATE sites
                    SET session_sync_status = 'validating',
                        session_sync_error = NULL, updated_at = ?
                    WHERE id = ?
                    """,
                    (str(now), int(row["site_id"])),
                    connection=conn,
                )
            return {**row, "status": "validating", "updated_at": now, "consumed_at": now}

    def create_request(
        self,
        *,
        target_kind: str,
        target_id: int,
        platform: str,
        target_origin: str,
        request_id: str,
        secret_hash: str,
        expires_at: str,
        now: str,
    ) -> None:
        if target_kind not in {"site", "admin_site"}:
            raise ValueError("invalid session sync target")
        target_column = "site_id" if target_kind == "site" else "admin_site_id"
        other_target_clause = (
            "admin_site_id IS NULL" if target_kind == "site" else "site_id IS NULL"
        )
        site_id = int(target_id) if target_kind == "site" else None
        admin_site_id = int(target_id) if target_kind == "admin_site" else None
        with transaction() as conn:
            execute_rowcount(
                f"""
                UPDATE browser_session_sync_requests
                SET status = 'expired', error_code = 'REPLACED',
                    error_message = '已创建新的同步请求', updated_at = ?
                WHERE status IN ('pending', 'validating') AND {target_column} = ?
                  AND {other_target_clause}
                """,
                (now, int(target_id)),
                connection=conn,
            )
            execute(
                """
                INSERT INTO browser_session_sync_requests
                (id, site_id, admin_site_id, platform, target_origin, secret_hash,
                 status, error_code, error_message, expires_at, created_at, updated_at,
                 consumed_at)
                VALUES (?, ?, ?, ?, ?, ?, 'pending', NULL, NULL, ?, ?, ?, NULL)
                """,
                (
                    str(request_id),
                    site_id,
                    admin_site_id,
                    str(platform),
                    str(target_origin),
                    str(secret_hash),
                    str(expires_at),
                    str(now),
                    str(now),
                ),
                connection=conn,
            )
            if target_kind == "site":
                execute_rowcount(
                    """
                    UPDATE sites
                    SET session_sync_status = 'pending', session_sync_error = NULL,
                        updated_at = ?
                    WHERE id = ?
                    """,
                    (now, int(target_id)),
                    connection=conn,
                )
            else:
                execute_rowcount(
                    """
                    UPDATE admin_sites
                    SET browser_login_last_error = NULL,
                        browser_login_last_check_at = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (now, now, int(target_id)),
                    connection=conn,
                )

    def finish_request(
        self,
        request_id: str,
        status: str,
        error_code: str = "",
        error_message: str = "",
        now: str = "",
    ) -> bool:
        """Finish one active request and update its active local target."""
        with transaction() as conn:
            row = query_one(
                """
                SELECT id, site_id, admin_site_id, platform, target_origin, status
                FROM browser_session_sync_requests
                WHERE id = ?
                """,
                (str(request_id),),
                connection=conn,
            )
            if not row:
                return False
            changed = execute_rowcount(
                """
                UPDATE browser_session_sync_requests
                SET status = ?, error_code = ?, error_message = ?, updated_at = ?
                WHERE id = ? AND status IN ('pending', 'validating')
                """,
                (
                    str(status),
                    str(error_code or "") or None,
                    str(error_message or "") or None,
                    str(now),
                    str(request_id),
                ),
                connection=conn,
            )
            if changed <= 0:
                return False

            target_origin = str(row.get("target_origin") or "")
            platform = str(row.get("platform") or "").strip().lower()
            if row.get("site_id") is not None and platform == "sub2api":
                active = query_one(
                    """
                    SELECT id FROM browser_session_sync_requests
                    WHERE site_id = ? AND admin_site_id IS NULL
                      AND platform = 'sub2api' AND target_origin = ?
                      AND status = 'validating'
                    """,
                    (int(row["site_id"]), target_origin),
                    connection=conn,
                )
                if active and str(active.get("id") or "") != str(request_id):
                    return True
                execute_rowcount(
                    """
                    UPDATE sites AS s
                    SET session_sync_status = ?, session_sync_error = ?,
                        session_synced_at = CASE WHEN ? = 'ready' THEN ? ELSE session_synced_at END,
                        updated_at = ?
                    WHERE s.id = ?
                      AND s.platform = 'sub2api'
                      AND s.auth_mode = 'browser'
                      AND EXISTS (
                          SELECT 1
                          FROM browser_session_sync_requests AS r
                          WHERE r.id = ?
                            AND r.site_id = s.id
                            AND r.admin_site_id IS NULL
                            AND r.platform = 'sub2api'
                            AND r.target_origin = ?
                            AND r.status IN ('validating', 'pending', 'ready', 'failed', 'expired')
                      )
                    """,
                    (
                        str(status),
                        str(error_message or "") or None,
                        str(status),
                        str(now),
                        str(now),
                        int(row["site_id"]),
                        str(request_id),
                        target_origin,
                    ),
                    connection=conn,
                )
            elif row.get("admin_site_id") is not None and platform == "newapi":
                active = query_one(
                    """
                    SELECT id FROM browser_session_sync_requests
                    WHERE admin_site_id = ? AND site_id IS NULL
                      AND platform = 'newapi' AND target_origin = ?
                      AND status = 'validating'
                    """,
                    (int(row["admin_site_id"]), target_origin),
                    connection=conn,
                )
                if active and str(active.get("id") or "") != str(request_id):
                    return True
                execute_rowcount(
                    """
                    UPDATE admin_sites AS a
                    SET browser_login_last_error = CASE
                            WHEN ? = 'ready' THEN NULL ELSE ? END,
                        browser_login_last_check_at = ?, updated_at = ?
                    WHERE a.id = ?
                      AND a.platform = 'newapi'
                      AND EXISTS (
                          SELECT 1
                          FROM browser_session_sync_requests AS r
                          WHERE r.id = ?
                            AND r.admin_site_id = a.id
                            AND r.site_id IS NULL
                            AND r.platform = 'newapi'
                            AND r.target_origin = ?
                            AND r.status IN ('validating', 'pending', 'ready', 'failed', 'expired')
                      )
                    """,
                    (
                        str(status),
                        str(error_message or "") or None,
                        str(now),
                        str(now),
                        int(row["admin_site_id"]),
                        str(request_id),
                        target_origin,
                    ),
                    connection=conn,
                )
        return True

    def persist_sub2api_browser_session_cas(
        self,
        *,
        site_id: int,
        request_id: str,
        target_origin: str,
        access_token: str,
        refresh_token: str,
        token_expires_at: str,
        now: str,
    ) -> bool:
        """Write a validated browser session only while its request is active."""
        changed = execute_rowcount(
            """
            UPDATE sites AS s
            SET auth_mode = 'browser', login_enabled = 1,
                access_token = ?, refresh_token = ?, token_expires_at = ?,
                session_sync_status = 'ready', session_sync_error = NULL,
                session_synced_at = ?, updated_at = ?
            WHERE s.id = ?
              AND s.platform = 'sub2api'
              AND s.auth_mode = 'browser'
              AND EXISTS (
                  SELECT 1
                  FROM browser_session_sync_requests AS r
                  WHERE r.id = ?
                    AND r.site_id = s.id
                    AND r.admin_site_id IS NULL
                    AND r.platform = 'sub2api'
                    AND r.target_origin = ?
                    AND r.status = 'validating'
              )
            """,
            (
                str(access_token or "").strip(),
                str(refresh_token or "").strip(),
                str(token_expires_at or "").strip() or None,
                str(now),
                str(now),
                int(site_id),
                str(request_id),
                str(target_origin),
            ),
        )
        return changed > 0

    def persist_newapi_site_browser_session_cas(
        self,
        *,
        site_id: int,
        request_id: str,
        target_origin: str,
        session: dict[str, Any],
        now: str,
    ) -> bool:
        """Persist a regular NewAPI browser session behind its sync CAS."""
        try:
            expires_at = int(session.get("browser_access_expires_at") or 0)
        except (TypeError, ValueError):
            expires_at = 0
        changed = execute_rowcount(
            """
            UPDATE sites AS s
            SET auth_mode = 'browser', login_enabled = 1,
                login_username = NULL, login_password = NULL,
                access_token = ?, access_user_id = ?,
                browser_cookie = ?, browser_refresh_cookie = ?,
                browser_session_id = ?, browser_access_expires_at = ?,
                session_sync_status = 'ready', session_sync_error = NULL,
                session_synced_at = ?, updated_at = ?
            WHERE s.id = ?
              AND s.platform = 'newapi'
              AND s.auth_mode = 'browser'
              AND EXISTS (
                  SELECT 1
                  FROM browser_session_sync_requests AS r
                  WHERE r.id = ?
                    AND r.site_id = s.id
                    AND r.admin_site_id IS NULL
                    AND r.platform = 'newapi'
                    AND r.target_origin = ?
                    AND r.status = 'validating'
              )
            """,
            (
                str(session.get("access_token") or "").strip(),
                str(session.get("access_user_id") or "").strip(),
                str(session.get("browser_cookie") or "").strip() or None,
                str(session.get("browser_refresh_cookie") or "").strip()
                or None,
                str(session.get("browser_session_id") or "").strip() or None,
                expires_at,
                str(now),
                str(now),
                int(site_id),
                str(request_id),
                str(target_origin),
            ),
        )
        return changed > 0

    def persist_newapi_admin_browser_session_cas(
        self,
        *,
        admin_site_id: int,
        request_id: str,
        target_origin: str,
        session: dict[str, Any],
        now: str,
    ) -> bool:
        """Persist browser-only admin fields without touching system tokens."""
        try:
            expires_at = int(session.get("browser_access_expires_at") or 0)
        except (TypeError, ValueError):
            expires_at = 0
        changed = execute_rowcount(
            """
            UPDATE admin_sites AS a
            SET browser_access_token = ?, browser_refresh_cookie = ?,
                browser_session_id = ?, browser_access_expires_at = ?,
                browser_login_last_error = NULL,
                browser_login_last_check_at = ?, updated_at = ?
            WHERE a.id = ?
              AND a.platform = 'newapi'
              AND EXISTS (
                  SELECT 1
                  FROM browser_session_sync_requests AS r
                  WHERE r.id = ?
                    AND r.admin_site_id = a.id
                    AND r.site_id IS NULL
                    AND r.platform = 'newapi'
                    AND r.target_origin = ?
                    AND r.status = 'validating'
              )
            """,
            (
                str(session.get("access_token") or "").strip(),
                str(session.get("browser_refresh_cookie") or "").strip(),
                str(session.get("browser_session_id") or "").strip(),
                expires_at,
                str(now),
                str(now),
                int(admin_site_id),
                str(request_id),
                str(target_origin),
            ),
        )
        return changed > 0
