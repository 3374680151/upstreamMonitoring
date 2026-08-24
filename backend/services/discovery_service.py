"""Channel discovery/import service.

The discovery helpers (``_discovery_existing_site``,
``_discovery_public_item``, ``_discovery_channel_ids``,
``import_discovered_sites``, ``_import_discovered_site_item``,
``list_site_discovery_links``, ``_live_channel_urls_from_candidates``,
``_reconcile_site_discovery_links_in_connection``,
``reconcile_site_discovery_links``) were moved here from
``backend.legacy_runtime``.  The legacy runtime re-exports every public
name below so existing ``legacy.<fn>`` callers keep working unchanged.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from backend.core.config import DEFAULT_INTERVAL_MINUTES, MIN_INTERVAL_MINUTES
from backend.core.normalize import (
    _normalize_discovery_base_url,
    _positive_channel_id,
    _safe_discovery_display_url,
    normalize_base_url,
)
from backend.core.state import (
    MAX_DISCOVERY_CHANNEL_IDS_PER_ITEM,
    MAX_DISCOVERY_IMPORT_ITEMS,
    MAX_DISCOVERY_INTERVAL_MINUTES,
)
from backend.core.time import next_check_iso, utc_now_iso
from backend.db.connection import (
    _q,
    db_connection,
    db_execute,
    db_query_all,
    db_query_one,
)
from backend.repositories.sites import find_monitor_site_for_channel


def _discovery_existing_site(base_url: str) -> Optional[Dict[str, Any]]:
    """Find a local site by normalized URL, including legacy trailing slashes."""
    row = db_query_one(
        "SELECT id, base_url, platform FROM sites WHERE base_url = ? LIMIT 1",
        (base_url,),
    )
    if row:
        return row
    # Older rows may predate normalize_base_url and retain a trailing slash.
    # Keep the fallback query separate so normal indexed lookups stay cheap.
    return db_query_one(
        "SELECT id, base_url, platform FROM sites "
        "WHERE TRIM(TRAILING '/' FROM base_url) = ? LIMIT 1",
        (base_url,),
    )


def _discovery_public_item(
    item: Dict[str, Any],
    status: str,
    site_id: Any = None,
    message: str = "",
) -> Dict[str, Any]:
    """Build an import result without copying any credential-bearing fields."""
    result: Dict[str, Any] = {
        "base_url": str(item.get("base_url") or ""),
        "name": str(item.get("name") or "").strip()
        or str(item.get("base_url") or ""),
        "channel_ids": list(item.get("channel_ids") or []),
        "status": status,
    }
    if site_id is not None:
        result["site_id"] = site_id
    if message:
        result["message"] = message
    return result


def _discovery_channel_ids(item: Dict[str, Any]) -> Tuple[List[int], Optional[str]]:
    raw_ids = item.get("channel_ids")
    if not isinstance(raw_ids, list) or not raw_ids:
        return [], "channel_ids required"
    if len(raw_ids) > MAX_DISCOVERY_CHANNEL_IDS_PER_ITEM:
        return [], "too many channel_ids"
    channel_ids: List[int] = []
    for raw_id in raw_ids:
        if isinstance(raw_id, bool):
            return [], "channel_id invalid"
        try:
            channel_id = int(raw_id)
        except (TypeError, ValueError):
            return [], "channel_id invalid"
        if channel_id <= 0:
            return [], "channel_id invalid"
        if channel_id not in channel_ids:
            channel_ids.append(channel_id)
    if not channel_ids:
        return [], "channel_ids required"
    return channel_ids, None


def import_discovered_sites(
    admin_site: Dict[str, Any], body: Dict[str, Any]
) -> Any:
    """Idempotently create monitoring sites from NewAPI channel candidates.

    The function intentionally returns per-item results for valid requests and
    a small error object for request-wide validation failures.  It never accepts
    or returns credentials; browser session synchronization remains a separate
    explicit endpoint/flow.
    """
    if not isinstance(admin_site, dict):
        return {
            "error": "platform_invalid",
            "message": "主站渠道发现导入仅支持 NewAPI",
        }
    requested_platform = str(admin_site.get("platform") or "newapi").strip().lower()
    if requested_platform != "newapi":
        return {
            "error": "platform_invalid",
            "message": "主站渠道发现导入仅支持 NewAPI",
        }
    if not isinstance(body, dict):
        return {"error": "invalid_body", "message": "请求体无效"}

    raw_items = body.get("items")
    if not isinstance(raw_items, list):
        return {"error": "items_invalid", "message": "items 必须是数组"}
    if len(raw_items) > MAX_DISCOVERY_IMPORT_ITEMS:
        return {
            "error": "too_many_items",
            "message": f"单次最多导入 {MAX_DISCOVERY_IMPORT_ITEMS} 个渠道",
        }

    raw_interval = body.get("interval_minutes", DEFAULT_INTERVAL_MINUTES)
    if isinstance(raw_interval, bool):
        return {"error": "interval_invalid", "message": "interval_minutes 无效"}
    try:
        interval_minutes = int(raw_interval or DEFAULT_INTERVAL_MINUTES)
    except (TypeError, ValueError):
        return {"error": "interval_invalid", "message": "interval_minutes 无效"}
    interval_minutes = max(
        MIN_INTERVAL_MINUTES,
        min(MAX_DISCOVERY_INTERVAL_MINUTES, interval_minutes),
    )

    try:
        admin_site_id = int(admin_site.get("id"))
    except (TypeError, ValueError):
        return {"error": "admin_site_invalid", "message": "管理站点 ID 无效"}

    prepared: List[Tuple[int, Dict[str, Any]]] = []
    results: List[Optional[Dict[str, Any]]] = [None] * len(raw_items)
    for item_index, raw_item in enumerate(raw_items):
        if not isinstance(raw_item, dict):
            results[item_index] = (
                {
                    "base_url": "",
                    "name": "",
                    "channel_ids": [],
                    "status": "invalid",
                    "message": "候选项无效",
                }
            )
            continue
        normalized, url_error = _normalize_discovery_base_url(raw_item.get("base_url"))
        item = {
            "base_url": normalized
            or _safe_discovery_display_url(raw_item.get("base_url")),
            "name": str(raw_item.get("name") or "").strip(),
            "channel_ids": [],
            "channel_names": raw_item.get("channel_names"),
        }
        if url_error:
            item["channel_ids"] = (
                list(raw_item.get("channel_ids") or [])
                if isinstance(raw_item.get("channel_ids"), list)
                else []
            )
            results[item_index] = (
                _discovery_public_item(item, "invalid", message=url_error)
            )
            continue
        channel_ids, ids_error = _discovery_channel_ids(raw_item)
        if ids_error:
            submitted_ids = (
                list(raw_item.get("channel_ids") or [])
                if isinstance(raw_item.get("channel_ids"), list)
                else []
            )
            results[item_index] = (
                _discovery_public_item(
                    {**item, "base_url": normalized, "channel_ids": submitted_ids},
                    "invalid",
                    message=ids_error,
                )
            )
            continue
        item["base_url"] = normalized
        item["channel_ids"] = channel_ids
        item["name"] = item["name"] or normalized
        prepared.append((item_index, item))

    # Do not touch the database when every submitted item failed local
    # validation.  This also makes malformed requests cheap and predictable.
    for item_index, item in prepared:
        with db_connection() as connection:
            try:
                result = _import_discovered_site_item(
                    connection,
                    admin_site_id,
                    item,
                    interval_minutes,
                )
                connection.commit()
            except Exception:
                try:
                    connection.rollback()
                except Exception:
                    pass
                result = _discovery_public_item(
                    item, "conflict", message="创建或关联监控站点失败"
                )
        results[item_index] = result

    return [result for result in results if result is not None]


def _import_discovered_site_item(
    connection: Any,
    admin_site_id: int,
    item: Dict[str, Any],
    interval_minutes: int,
    allow_existing_platform: bool = False,
) -> Dict[str, Any]:
    """Create or reuse a local monitoring site for one discovery candidate.

    All writes (the ``sites`` row and every ``site_discovery_links`` row)
    happen on the supplied connection.  The caller is expected to commit
    on success and roll back on any exception; this function itself never
    commits, so a partial failure cannot leave a created site dangling
    without its provenance links.
    """
    base_url = item["base_url"]
    name = item["name"]
    with connection.cursor() as cursor:
        cursor.execute(
            _q(
                "SELECT * FROM sites "
                "WHERE base_url = ? OR TRIM(TRAILING '/' FROM base_url) = ? "
                "ORDER BY (base_url = ?) DESC LIMIT 1 FOR UPDATE"
            ),
            (base_url, base_url, base_url),
        )
        existing = cursor.fetchone()
        if existing:
            platform = str(existing.get("platform") or "newapi").strip().lower()
            if platform != "newapi" and not allow_existing_platform:
                return _discovery_public_item(
                    item,
                    "conflict",
                    site_id=existing.get("id"),
                    message="同 URL 已存在非 NewAPI 监控站点",
                )
            site_id = int(existing["id"])
            item_status = "existing"
        else:
            now = utc_now_iso()
            try:
                cursor.execute(
                    _q(
                        """
                        INSERT INTO sites
                        (name, base_url, platform, enabled, interval_minutes,
                         login_enabled, auth_mode, status, next_check_at,
                         created_at, updated_at)
                        VALUES (?, ?, 'newapi', 1, ?, 0, 'token', 'unknown', ?, ?, ?)
                        """
                    ),
                    (
                        name[:255],
                        base_url,
                        interval_minutes,
                        next_check_iso(interval_minutes),
                        now,
                        now,
                    ),
                )
                site_id = int(cursor.lastrowid or 0)
                item_status = "created"
            except Exception:
                # A concurrent import can win the unique base_url race; treat
                # that row as existing.  Unrelated write failures bubble up
                # so the caller can roll the whole transaction back.
                cursor.execute(
                    _q(
                        "SELECT id, platform FROM sites "
                        "WHERE base_url = ? OR TRIM(TRAILING '/' FROM base_url) = ? "
                        "LIMIT 1"
                    ),
                    (base_url, base_url),
                )
                refreshed = cursor.fetchone()
                refreshed_platform = str(
                    (refreshed or {}).get("platform") or "newapi"
                ).strip().lower()
                if refreshed and (
                    refreshed_platform == "newapi" or allow_existing_platform
                ):
                    site_id = int(refreshed["id"])
                    item_status = "existing"
                elif refreshed:
                    return _discovery_public_item(
                        item,
                        "conflict",
                        site_id=refreshed.get("id"),
                        message="同 URL 已存在非 NewAPI 监控站点",
                    )
                else:
                    return _discovery_public_item(
                        item, "conflict", message="创建监控站点失败"
                    )

        if not site_id:
            return _discovery_public_item(
                item, "conflict", message="监控站点 ID 无效"
            )

        source_names = item.get("channel_names")
        source_names = source_names if isinstance(source_names, list) else []
        now = utc_now_iso()
        for index, channel_id in enumerate(item["channel_ids"]):
            channel_name = (
                str(source_names[index] or "").strip()
                if index < len(source_names)
                else ""
            ) or name
            cursor.execute(
                _q(
                    """
                    INSERT INTO site_discovery_links
                    (site_id, admin_site_id, channel_id, upstream_base_url,
                     channel_name, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    ON DUPLICATE KEY UPDATE
                      upstream_base_url = VALUES(upstream_base_url),
                      channel_name = VALUES(channel_name),
                      updated_at = VALUES(updated_at)
                    """
                ),
                (
                    int(site_id),
                    int(admin_site_id),
                    int(channel_id),
                    base_url,
                    channel_name[:255],
                    now,
                    now,
                ),
            )
    return _discovery_public_item(item, item_status, site_id=site_id)


def list_site_discovery_links(site_id: int) -> List[Dict[str, Any]]:
    """Return only the public (non-credential) provenance columns.

    The route is intentionally narrow: it joins against ``admin_sites`` to
    surface the source main-site name, but it never copies any token,
    password or session payload from the link row.  The handler relies on
    ``get_site_or_404`` to 404 unknown sites before this lookup runs.
    """
    rows = db_query_all(
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


def _live_channel_urls_from_candidates(
    candidates: List[Dict[str, Any]],
) -> Dict[int, str]:
    live_urls: Dict[int, str] = {}
    for candidate in candidates or []:
        if not isinstance(candidate, dict):
            continue
        base_url = normalize_base_url(str(candidate.get("base_url") or ""))
        for raw_channel_id in candidate.get("channel_ids") or []:
            channel_id = _positive_channel_id(raw_channel_id)
            if channel_id is not None:
                live_urls[channel_id] = base_url
    return live_urls


def _reconcile_site_discovery_links_in_connection(
    connection: Any,
    admin_site_id: int,
    candidates: List[Dict[str, Any]],
    preferred_site_by_channel: Optional[Dict[int, int]] = None,
) -> Tuple[int, set]:
    """Keep one source link per ``admin_site_id + channel_id`` identity."""
    live_urls = _live_channel_urls_from_candidates(candidates)
    preferred_site_by_channel = preferred_site_by_channel or {}
    rows = db_query_all(
        "SELECT id, site_id, channel_id, upstream_base_url "
        "FROM site_discovery_links WHERE admin_site_id = ?",
        (int(admin_site_id),),
        connection=connection,
    )
    stale_rows: List[Dict[str, Any]] = []
    affected_site_ids: set = set()
    live_rows_by_channel: Dict[int, List[Dict[str, Any]]] = {}
    for row in rows:
        channel_id = _positive_channel_id(row.get("channel_id"))
        if channel_id is None or channel_id not in live_urls:
            stale_rows.append(row)
        else:
            live_url = live_urls.get(channel_id) or ""
            current_url = normalize_base_url(
                str(row.get("upstream_base_url") or "")
            )
            # A live channel with no usable URL is preserved rather than
            # treated as a disappearance caused by malformed metadata.
            if live_url and current_url != live_url:
                stale_rows.append(row)
            else:
                live_rows_by_channel.setdefault(channel_id, []).append(row)
        try:
            affected_site_ids.add(int(row.get("site_id")))
        except (TypeError, ValueError):
            continue

    # A legacy import could have linked one upstream channel to multiple local
    # sites.  The current sync result identifies the canonical site; otherwise
    # retain the oldest link deterministically and release the rest.
    for channel_id, channel_rows in live_rows_by_channel.items():
        if len(channel_rows) < 2:
            continue
        preferred_site_id = preferred_site_by_channel.get(channel_id)
        canonical = next(
            (
                row
                for row in channel_rows
                if preferred_site_id is not None
                and int(row.get("site_id") or 0) == int(preferred_site_id)
            ),
            None,
        )
        if canonical is None:
            canonical = min(channel_rows, key=lambda row: int(row.get("id") or 0))
        canonical_id = int(canonical.get("id") or 0)
        stale_rows.extend(
            row for row in channel_rows if int(row.get("id") or 0) != canonical_id
        )

    removed = 0
    if stale_rows:
        with connection.cursor() as cursor:
            for row in stale_rows:
                cursor.execute(
                    _q(
                        "DELETE FROM site_discovery_links "
                        "WHERE id = ? AND admin_site_id = ?"
                    ),
                    (int(row["id"]), int(admin_site_id)),
                )
                removed += int(cursor.rowcount or 0)
    return removed, affected_site_ids


def reconcile_site_discovery_links(
    admin_site_id: int,
    candidates: List[Dict[str, Any]],
) -> int:
    """Drop provenance rows whose source channel no longer exists upstream.

    Only ``site_discovery_links`` rows for this admin site are deleted; the
    local monitoring site, snapshots and change history are never touched.
    The caller must invoke this after a complete and successful upstream
    read — paging failures, upstream errors and truncated lists must skip
    the reconcile step.
    """
    with db_connection() as connection:
        try:
            removed, _affected = _reconcile_site_discovery_links_in_connection(
                connection, int(admin_site_id), candidates
            )
            connection.commit()
            return removed
        except Exception:
            try:
                connection.rollback()
            except Exception:
                pass
            raise


class DiscoveryService:
    def import_sites(self, admin_site: dict[str, Any], payload: dict[str, Any]):
        return import_discovered_sites(admin_site, payload)

    def links(self, site_id: int):
        return list_site_discovery_links(site_id)
