"""Retention pruning for monitoring snapshots and change history.

Each admin site caps how long its discovered monitoring sites keep
snapshot / change rows (``admin_sites.retention_days``; 0 = keep forever).
Pruning only touches sites linked through ``site_discovery_links``; sites
added manually are never pruned. Timestamps are ISO strings written by
``core.time.utc_now_iso`` so a lexicographic cutoff comparison is safe as
long as APP_TIMEZONE stays unchanged.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any, Dict

from backend.core.time import app_now
from backend.db.connection import _q, db_connection, db_query_all


def pruneExpiredMonitoringData() -> Dict[str, int]:
    """Delete snapshots/changes older than each admin site's retention window."""
    admins = db_query_all(
        "SELECT id, retention_days FROM admin_sites WHERE retention_days > 0"
    )
    deleted: Dict[str, int] = {
        "snapshots": 0,
        "changes": 0,
        "admin_sites": len(admins),
    }
    if not admins:
        return deleted

    with db_connection() as connection:
        for admin in admins:
            try:
                retentionDays = int(admin.get("retention_days") or 0)
            except (TypeError, ValueError):
                continue
            if retentionDays <= 0:
                continue
            siteRows: list[dict[str, Any]] = db_query_all(
                "SELECT site_id FROM site_discovery_links WHERE admin_site_id = ?",
                (int(admin["id"]),),
                connection=connection,
            )
            siteIds = [
                int(row["site_id"]) for row in siteRows if row.get("site_id")
            ]
            if not siteIds:
                continue
            cutoff = (
                app_now() - timedelta(days=retentionDays)
            ).isoformat(timespec="seconds")
            placeholders = ",".join("?" for _ in siteIds)
            with connection.cursor() as cursor:
                cursor.execute(
                    _q(
                        f"DELETE FROM snapshots WHERE site_id IN ({placeholders}) "
                        "AND checked_at < ?"
                    ),
                    (*siteIds, cutoff),
                )
                deleted["snapshots"] += int(cursor.rowcount or 0)
                cursor.execute(
                    _q(
                        f"DELETE FROM changes WHERE site_id IN ({placeholders}) "
                        "AND created_at < ?"
                    ),
                    (*siteIds, cutoff),
                )
                deleted["changes"] += int(cursor.rowcount or 0)
        connection.commit()
    return deleted
