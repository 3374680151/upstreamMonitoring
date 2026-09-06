"""Recent-request TTFT aggregation for admin-site channels.

Serves the main-site monitor page ("主站监控"): per channel, the latest
requests inside a configurable window with first-token latency classified by
environment-variable thresholds. Reads are answered from an SWR short cache
(``state.MAIN_CHANNEL_LOGS_*``) so the page opens instantly; uncached channels
are fetched with bounded concurrency behind a per-base rate gate that protects
the upstream NewAPI ``/api/log/`` endpoint. Thresholds come from
``core.config`` only — the frontend receives them in the payload and never
hardcodes boundaries.
"""

from __future__ import annotations

import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from backend.core import config
from backend.core import state
from backend.core.normalize import normalize_base_url
from backend.core.time import APP_TIMEZONE, utc_now_iso
from backend.integrations.newapi_channel_logs import (
    fetchNewapiChannelRecentLogs,
    parseLogRow,
)
from backend.repositories.admin_sites import (
    admin_site_platform,
    get_admin_site_by_id,
)

FETCH_CONCURRENCY = 4


def buildTtftThresholdsPayload() -> Dict[str, Any]:
    """Expose the active thresholds so frontend legend/labels stay in sync."""
    return {
        "fast_seconds": config.MAIN_CHANNEL_TTFT_FAST_SECONDS,
        "slow_seconds": config.MAIN_CHANNEL_TTFT_SLOW_SECONDS,
        "stale_seconds": config.MAIN_CHANNEL_REQUEST_STALE_SECONDS,
        "window_seconds": config.MAIN_CHANNEL_RECENT_WINDOW_SECONDS,
    }


def fetchAdminSiteChannelRecentRequests(
    site: Dict[str, Any], channelIds: List[int], forceRefresh: bool = False
) -> Dict[str, Any]:
    """Aggregate recent-request TTFT entries for the given channels.

    ``forceRefresh`` (query ``refresh=1``) bypasses even fresh cache entries
    and refetches every requested channel synchronously.
    """
    adminSiteId = int(site.get("id") or 0)
    uniqueIds = list(dict.fromkeys(int(channelId) for channelId in channelIds))
    entries: Dict[str, Any] = {}
    targets: List[int] = []
    for channelId in uniqueIds:
        if forceRefresh:
            targets.append(channelId)
            continue
        cached, age = _serveCachedEntry(adminSiteId, channelId)
        if cached is None or age > state.MAIN_CHANNEL_LOGS_FRESH_SECONDS:
            if cached is not None:
                # SWR: stale cache answers immediately, background refresh runs.
                entries[str(channelId)] = cached
                _scheduleLogsRefresh(adminSiteId, channelId)
            else:
                targets.append(channelId)
        else:
            entries[str(channelId)] = cached
    if targets:
        with ThreadPoolExecutor(max_workers=FETCH_CONCURRENCY) as pool:
            fetched = list(
                pool.map(lambda cid: _fetchAndCacheEntry(site, adminSiteId, cid), targets)
            )
        for channelId, entry in zip(targets, fetched):
            entries[str(channelId)] = entry
    return {
        "admin_site_id": adminSiteId,
        "platform": "newapi",
        "thresholds": buildTtftThresholdsPayload(),
        "channels": entries,
        "fetched_at": utc_now_iso(),
    }


def _levelForTtft(ttftSeconds: float) -> str:
    if ttftSeconds >= config.MAIN_CHANNEL_TTFT_SLOW_SECONDS:
        return "slow"
    if ttftSeconds >= config.MAIN_CHANNEL_TTFT_FAST_SECONDS:
        return "normal"
    return "fast"


def _buildEntry(
    channelId: int, ok: bool, rows: List[Dict[str, Any]], error: Optional[str]
) -> Dict[str, Any]:
    fetchedAt = utc_now_iso()
    if not ok:
        return {
            "channel_id": channelId,
            "state": "error",
            "requests": [],
            "error": error or "读取请求日志失败",
            "fetched_at": fetchedAt,
        }
    nowUnix = time.time()
    windowSeconds = config.MAIN_CHANNEL_RECENT_WINDOW_SECONDS
    staleSeconds = config.MAIN_CHANNEL_REQUEST_STALE_SECONDS
    points: List[Dict[str, Any]] = []
    for row in rows:  # rows are newest-first from the upstream
        parsed = parseLogRow(row)
        if parsed is None:
            continue
        createdAtUnix = int(parsed.pop("created_at_unix") or 0)
        if createdAtUnix <= 0:
            continue
        ageSeconds = max(0, int(nowUnix - createdAtUnix))
        if ageSeconds > windowSeconds:
            break  # everything after is older: outside the window
        points.append(
            {
                "log_id": parsed["log_id"],
                "created_at": _isoFromUnix(createdAtUnix),
                "model_name": parsed["model_name"],
                "ttft_seconds": parsed["ttft_seconds"],
                "total_seconds": parsed["total_seconds"],
                "is_stream": parsed["is_stream"],
                "age_seconds": ageSeconds,
                "stale": ageSeconds > staleSeconds,
                "level": _levelForTtft(parsed["ttft_seconds"]),
            }
        )
    return {
        "channel_id": channelId,
        "state": "active" if points else "idle",
        "requests": points,
        "error": None,
        "fetched_at": fetchedAt,
    }


def _isoFromUnix(unixSeconds: int) -> str:
    return datetime.fromtimestamp(int(unixSeconds), APP_TIMEZONE).isoformat(timespec="seconds")


def _logsCacheKey(adminSiteId: int, channelId: int) -> str:
    return f"{adminSiteId}:{channelId}"


def _cacheLogsEntry(adminSiteId: int, channelId: int, entry: Dict[str, Any]) -> None:
    with state.MAIN_CHANNEL_LOGS_CACHE_LOCK:
        state.MAIN_CHANNEL_LOGS_CACHE[_logsCacheKey(adminSiteId, channelId)] = {
            "payload": json.loads(json.dumps(entry, ensure_ascii=False)),
            "updated_monotonic": time.monotonic(),
        }


def _serveCachedEntry(
    adminSiteId: int, channelId: int
) -> Tuple[Optional[Dict[str, Any]], float]:
    """Return a deep copy of the cached entry with timing re-derived.

    Points were classified against fetch time; a cached entry served later
    must age with real time (stale ring) and drop points that left the
    window, otherwise a long SWR tail would keep stale dots looking fresh.
    """
    with state.MAIN_CHANNEL_LOGS_CACHE_LOCK:
        entry = state.MAIN_CHANNEL_LOGS_CACHE.get(_logsCacheKey(adminSiteId, channelId))
        if not entry or not isinstance(entry.get("payload"), dict):
            return None, float("inf")
        age = time.monotonic() - float(entry.get("updated_monotonic") or 0)
        payload = json.loads(json.dumps(entry["payload"], ensure_ascii=False))
    _rederiveEntryTiming(payload)
    return payload, age


def _rederiveEntryTiming(entry: Dict[str, Any]) -> None:
    """Re-derive age_seconds/stale/window per point against the current clock."""
    if entry.get("state") != "active":
        return
    nowUnix = time.time()
    windowSeconds = config.MAIN_CHANNEL_RECENT_WINDOW_SECONDS
    staleSeconds = config.MAIN_CHANNEL_REQUEST_STALE_SECONDS
    kept: List[Dict[str, Any]] = []
    for point in entry.get("requests") or []:
        try:
            createdUnix = int(datetime.fromisoformat(str(point.get("created_at"))).timestamp())
        except (TypeError, ValueError):
            continue
        ageSeconds = max(0, int(nowUnix - createdUnix))
        if ageSeconds > windowSeconds:
            continue  # left the display window while sitting in cache
        point["age_seconds"] = ageSeconds
        point["stale"] = ageSeconds > staleSeconds
        kept.append(point)
    entry["requests"] = kept
    if not kept:
        entry["state"] = "idle"


def _logsRefreshWorker(adminSiteId: int, channelId: int) -> None:
    try:
        site = get_admin_site_by_id(adminSiteId)
        if not site or admin_site_platform(site) != "newapi":
            return
        ok, rows, _error = _fetchLogsWithGate(
            site, channelId, config.MAIN_CHANNEL_RECENT_REQUESTS
        )
        if ok:
            _cacheLogsEntry(adminSiteId, channelId, _buildEntry(channelId, ok, rows, _error))
    finally:
        with state.MAIN_CHANNEL_LOGS_CACHE_LOCK:
            state.MAIN_CHANNEL_LOGS_REFRESHING.discard(_logsCacheKey(adminSiteId, channelId))


def _scheduleLogsRefresh(adminSiteId: int, channelId: int) -> None:
    key = _logsCacheKey(adminSiteId, channelId)
    with state.MAIN_CHANNEL_LOGS_CACHE_LOCK:
        if key in state.MAIN_CHANNEL_LOGS_REFRESHING:
            return
        state.MAIN_CHANNEL_LOGS_REFRESHING.add(key)
    threading.Thread(
        target=_logsRefreshWorker, args=(adminSiteId, channelId), daemon=True
    ).start()


def _fetchAndCacheEntry(
    site: Dict[str, Any], adminSiteId: int, channelId: int
) -> Dict[str, Any]:
    ok, rows, error = _fetchLogsWithGate(
        site, channelId, config.MAIN_CHANNEL_RECENT_REQUESTS
    )
    entry = _buildEntry(channelId, ok, rows, error)
    if ok:
        # 拉取失败不写缓存：瞬时故障（5xx/超时/限流）不能把已有好数据覆盖成
        # error，也不能在 60s 内被当「新鲜」反复返回；与后台 SWR 路径同口径。
        _cacheLogsEntry(adminSiteId, channelId, entry)
    return entry


def _fetchLogsWithGate(
    site: Dict[str, Any], channelId: int, limit: int
) -> Tuple[bool, List[Dict[str, Any]], Optional[str]]:
    """Fetch logs behind the per-base reservation gate (mirrors channel key).

    The gate reserves the next slot while holding the lock, then sleeps and
    performs the HTTP call outside the lock so other sites are never blocked.
    A 429 from the upstream puts the base into a cooldown during which every
    fetch for that base fails fast with a wait hint instead of hammering it.
    """
    base = normalize_base_url(str(site.get("base_url") or ""))
    gateKey = f"{int(site.get('id') or 0)}|{base}"
    with state.MAIN_CHANNEL_LOGS_REQUEST_LOCK:
        cooldownUntil = state.MAIN_CHANNEL_LOGS_RATE_LIMIT_UNTIL.get(gateKey, 0.0)
        if cooldownUntil > time.monotonic():
            waitSeconds = int(cooldownUntil - time.monotonic()) + 1
            return False, [], f"主站日志接口限流冷却中，请等待约 {waitSeconds} 秒后重试"
        elapsed = time.monotonic() - state.MAIN_CHANNEL_LOGS_LAST_REQUEST_AT.get(gateKey, 0.0)
        delay = state.MAIN_CHANNEL_LOGS_MIN_INTERVAL_SECONDS - elapsed
        state.MAIN_CHANNEL_LOGS_LAST_REQUEST_AT[gateKey] = time.monotonic() + max(0.0, delay)
    if delay > 0:
        time.sleep(delay)
    ok, rows, error = fetchNewapiChannelRecentLogs(site, channelId, limit)
    if not ok and ("429" in (error or "") or "限流" in (error or "")):
        with state.MAIN_CHANNEL_LOGS_REQUEST_LOCK:
            state.MAIN_CHANNEL_LOGS_RATE_LIMIT_UNTIL[gateKey] = (
                time.monotonic() + state.MAIN_CHANNEL_LOGS_RATE_LIMIT_COOLDOWN_SECONDS
            )
    return ok, rows, error
