"""URL normalization and value-formatting pure functions.

Extracted from ``backend.legacy_runtime`` during the FastAPI boundary
migration.  Everything here is side-effect free: no database access, no I/O,
no module-level mutable state.  Functions that previously relied on config
constants (e.g. ``APP_TIMEZONE``) resolve them lazily so this module can be
imported without pulling the full legacy runtime.
"""

from __future__ import annotations

from datetime import datetime, timezone
from http.cookies import SimpleCookie
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

from backend.core.time import APP_TIMEZONE


# Credential-bearing field names that must never be copied into channel
# metadata snapshots.  Kept in sync with the upstream admin sync flow.
SYNC_SECRET_FIELD_NAMES = {
    "access_token",
    "browser_access_token",
    "browser_cookie",
    "browser_refresh_cookie",
    "channel_key",
    "key",
    "login_password",
    "password",
    "refresh_token",
    "secret",
    "client_secret",
    "security_proof",
    "token",
}


def normalize_base_url(value: str) -> str:
    value = (value or "").strip().rstrip("/")
    if not value:
        return value
    return value


def _normalize_discovery_base_url(value: Any) -> Tuple[str, Optional[str]]:
    """Normalize and validate a URL supplied by the channel discovery flow.

    Discovery URLs originate from an upstream channel list, but import requests
    are still treated as untrusted input.  Keep the existing normalization
    function as the canonical representation and reject credentials or schemes
    that the browser/session bridge cannot safely handle.
    """
    normalized = normalize_base_url(str(value or ""))
    if not normalized:
        return "", "base_url required"
    try:
        parsed = urlparse(normalized)
        # Accessing .port validates malformed/out-of-range port values.
        parsed.port
    except (TypeError, ValueError):
        return "", "base_url invalid"
    if (
        parsed.scheme.lower() not in {"http", "https"}
        or not parsed.netloc
        or not parsed.hostname
    ):
        return "", "base_url must use http or https"
    if parsed.username or parsed.password:
        return "", "base_url must not include credentials"
    return normalized, None


def _safe_discovery_display_url(value: Any) -> str:
    """Return a URL suitable for an error row without exposing userinfo."""
    text = normalize_base_url(str(value or ""))
    if not text:
        return ""
    try:
        parsed = urlparse(text)
        if parsed.username or parsed.password:
            scheme = parsed.scheme.lower()
            hostname = parsed.hostname or ""
            if not hostname:
                return ""
            try:
                port = f":{parsed.port}" if parsed.port else ""
            except (TypeError, ValueError):
                port = ""
            return f"{scheme}://{hostname}{port}{parsed.path or ''}".rstrip("/")
    except (TypeError, ValueError):
        return ""
    return text[:512]


def _positive_channel_id(value: Any) -> Optional[int]:
    if isinstance(value, bool):
        return None
    try:
        channel_id = int(value)
    except (TypeError, ValueError):
        return None
    return channel_id if channel_id > 0 else None


def _sync_safe_value(value: Any, field_name: str = "", depth: int = 0) -> Any:
    """Copy upstream metadata while excluding credential-bearing fields."""
    normalized_name = str(field_name or "").strip().lower()
    if (
        normalized_name in SYNC_SECRET_FIELD_NAMES
        or normalized_name.endswith("_token")
        or normalized_name.endswith("_password")
        or normalized_name.endswith("_cookie")
        or normalized_name.endswith("_secret")
    ):
        return None
    if depth > 8:
        return str(value)
    if isinstance(value, dict):
        result: Dict[str, Any] = {}
        for raw_key, raw_value in value.items():
            key = str(raw_key)
            normalized_key = key.strip().lower()
            if (
                normalized_key in SYNC_SECRET_FIELD_NAMES
                or normalized_key.endswith("_token")
                or normalized_key.endswith("_password")
                or normalized_key.endswith("_cookie")
                or normalized_key.endswith("_secret")
            ):
                continue
            result[key] = _sync_safe_value(raw_value, key, depth + 1)
        return result
    if isinstance(value, list):
        return [_sync_safe_value(item, "", depth + 1) for item in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def normalize_session_expiry(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    try:
        epoch_value = float(text)
    except (TypeError, ValueError):
        epoch_value = -1
    if epoch_value >= 0:
        if epoch_value >= 100_000_000_000:
            epoch_value /= 1000
        try:
            return datetime.fromtimestamp(epoch_value, tz=timezone.utc).astimezone(
                APP_TIMEZONE
            ).isoformat(timespec="seconds")
        except (OverflowError, OSError, ValueError):
            return ""
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return ""
    if parsed.tzinfo is None:
        return ""
    return parsed.isoformat(timespec="seconds")


def site_origin(base_url: str) -> str:
    try:
        parsed = urlparse(str(base_url or "").strip())
        parsed.port
    except (TypeError, ValueError):
        return ""
    if (
        parsed.scheme.lower() not in {"http", "https"}
        or not parsed.hostname
        or parsed.username
        or parsed.password
    ):
        return ""
    return f"{parsed.scheme.lower()}://{parsed.netloc.lower()}"


def _url_origin(value: str) -> Tuple[str, Optional[str], Optional[int]]:
    parsed = urlparse(value)
    scheme = parsed.scheme.lower()
    port = parsed.port
    if port is None:
        port = 443 if scheme == "https" else 80 if scheme == "http" else None
    return scheme, parsed.hostname, port


def _admin_site_origin(base_url: str) -> str:
    try:
        parsed = urlparse(normalize_base_url(base_url))
        scheme = parsed.scheme.lower()
        hostname = parsed.hostname
        parsed.port  # Access validates malformed or out-of-range ports.
    except (TypeError, ValueError):
        return ""
    if (
        scheme not in {"http", "https"}
        or not parsed.netloc
        or not hostname
        or parsed.username
        or parsed.password
    ):
        return ""
    return f"{scheme}://{parsed.netloc}"


def _cookie_header_from_response(headers: Dict[str, Any], previous: str = "") -> str:
    """Keep only cookie name/value pairs from Set-Cookie response headers."""
    raw_values = headers.get("set-cookie") if isinstance(headers, dict) else []
    if isinstance(raw_values, str):
        raw_values = [raw_values]
    cookie = SimpleCookie()
    for raw in raw_values or []:
        try:
            cookie.load(str(raw))
        except Exception:
            continue
    values = [f"{key}={morsel.value}" for key, morsel in cookie.items()]
    return "; ".join(values) or str(previous or "").strip()


def clamp_perf_hours(raw: Any, default: float = 24) -> float:
    try:
        hours = float(raw)
    except (TypeError, ValueError):
        hours = float(default)
    if hours <= 0:
        hours = float(default)
    # NewAPI caps at 30 days
    return min(hours, 24 * 30)


def mask_channel_key(key: Any) -> str:
    text = str(key or "").strip()
    if not text:
        return ""
    if len(text) <= 8:
        return "****"
    return f"{text[:4]}****{text[-4:]}"


def mask_channel_in_place(channel: Dict[str, Any]) -> Dict[str, Any]:
    """Return a shallow copy of a channel row with the key masked for list views."""
    safe = dict(channel)
    if "key" in safe:
        safe["key"] = mask_channel_key(safe.get("key"))
        safe["key_masked"] = True
    return safe


def split_channel_groups(value: Any) -> List[str]:
    if isinstance(value, list):
        raw = value
    else:
        raw = str(value or "").replace("\n", ",").split(",")
    return [str(item).strip() for item in raw if str(item).strip()]


def _channel_key_is_masked(value: Any) -> bool:
    text = str(value or "").strip()
    return not text or "****" in text or text == "-"


def normalize_newapi_user_token_key(value: Any) -> str:
    """NewAPI 数据库存储的用户 token 不带 sk-，前端展示/渠道配置通常会带。"""
    text = str(value or "").strip()
    return text[3:] if text.lower().startswith("sk-") else text


def mask_newapi_user_token_key(value: Any) -> str:
    """与 NewAPI model.MaskTokenKey 保持一致，用于从掩码列表筛选候选 token。"""
    key = normalize_newapi_user_token_key(value)
    if not key:
        return ""
    if len(key) <= 4:
        return "*" * len(key)
    if len(key) <= 8:
        return f"{key[:2]}****{key[-2:]}"
    return f"{key[:4]}**********{key[-4:]}"


def format_change_value(raw: Any) -> str:
    if raw is None:
        return "-"
    if isinstance(raw, dict) and "ratio" in raw:
        ratio = raw.get("ratio")
        try:
            return f"{float(ratio):.2f}x"
        except Exception:
            return str(ratio)
    return str(raw)


def ratio_number(raw: Any) -> Optional[float]:
    if isinstance(raw, dict):
        raw = raw.get("ratio")
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def ratio_direction(change: Dict[str, Any]) -> str:
    old_ratio = ratio_number(change.get("old_value"))
    new_ratio = ratio_number(change.get("new_value"))
    if old_ratio is None or new_ratio is None:
        return "changed"
    if new_ratio > old_ratio:
        return "up"
    if new_ratio < old_ratio:
        return "down"
    return "changed"


def platform_label(site: Dict[str, Any]) -> str:
    return "sub2api" if (site.get("platform") or "newapi") == "sub2api" else "NewAPI"
