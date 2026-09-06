"""Recent consume-log fetch for a NewAPI admin-site channel.

Boundary: only the upstream ``GET /api/log/`` call plus row normalization.
Caching / fan-out / rate gating / thresholds live in
``services/channel_logs_service.py``; the router only sees normalized entries.

Field notes verified against a live NewAPI deployment (2026-09):

- the channel filter query param is ``channel``; ``channel_id`` is silently
  ignored by the upstream and returns unfiltered logs;
- rows are newest-first and carry ``channel`` / ``use_time`` (integer
  seconds) / ``created_at`` (unix seconds) / ``is_stream`` / ``model_name``;
- ``other`` is a JSON string in some versions and a dict in others;
- ``other.frt`` is the first-token time in seconds with ``-1000`` as the
  "not recorded" sentinel (every non-stream request), so callers must fall
  back to ``use_time`` whenever ``frt`` is missing or negative.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional, Tuple

from backend.integrations.http import request_json
from backend.integrations.newapi import newapi_admin_target


def fetchNewapiChannelRecentLogs(
    site: Dict[str, Any], channelId: int, limit: int
) -> Tuple[bool, List[Dict[str, Any]], Optional[str]]:
    """Fetch the latest ``limit`` consume logs of one channel, newest first."""
    base, headers = newapi_admin_target(site)
    url = f"{base}/api/log/?p=1&page_size={int(limit)}&type=2&channel={int(channelId)}"
    ok, payload, error = request_json(url, headers=headers)
    if not ok:
        return False, [], error or "读取主站请求日志失败"
    if not isinstance(payload, dict) or not payload.get("success"):
        message = (
            str(payload.get("message"))
            if isinstance(payload, dict)
            else "主站请求日志响应异常"
        )
        return False, [], message or "主站请求日志 success=false"
    return True, _logListItems(payload), None


def parseLogRow(row: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Normalize one upstream log row into TTFT point fields.

    ``created_at_unix`` stays raw (unix seconds) so the service layer can do
    window/stale math against a single ``time.time()`` reading. Returns None
    when the row lacks both a usable first-token value and a total time.
    """
    try:
        createdAtUnix = int(row.get("created_at"))
    except (TypeError, ValueError):
        createdAtUnix = 0
    useTime = _nonNegativeNumber(row.get("use_time"))
    firstToken = _nonNegativeNumber(_parseOther(row.get("other")).get("frt"))
    # frt < 0 (the -1000 sentinel) or missing means "not recorded": every
    # non-stream request lands here and falls back to the total time.
    ttft = firstToken if firstToken is not None else useTime
    if ttft is None:
        return None
    return {
        "log_id": row.get("id"),
        "created_at_unix": createdAtUnix,
        "model_name": str(row.get("model_name") or ""),
        "ttft_seconds": round(float(ttft), 3),
        "total_seconds": round(float(useTime or 0.0), 3),
        "is_stream": bool(row.get("is_stream")),
    }


def _logListItems(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Accept both upstream list shapes: data=[...] and data={items:[...]}."""
    data = payload.get("data")
    if isinstance(data, list):
        return [row for row in data if isinstance(row, dict)]
    if isinstance(data, dict) and isinstance(data.get("items"), list):
        return [row for row in data["items"] if isinstance(row, dict)]
    return []


def _parseOther(raw: Any) -> Dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str) and raw.strip():
        try:
            parsed = json.loads(raw)
        except ValueError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _nonNegativeNumber(value: Any) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number >= 0 else None
