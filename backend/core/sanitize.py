"""Credential-safe values used at API and integration boundaries."""

from __future__ import annotations

import re
from typing import Any


SECRET_FIELD_NAMES = frozenset(
    {
        "access_token",
        "api_key",
        "api_token",
        "authorization",
        "browser_access_token",
        "browser_cookie",
        "browser_refresh_cookie",
        "channel_key",
        "client_secret",
        "key",
        "login_password",
        "password",
        "refresh_token",
        "secret",
        "security_proof",
        "token",
    }
)


def is_masked_key(value: Any) -> bool:
    text = str(value or "").strip()
    return not text or "****" in text or text == "-"


def mask_channel_key(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if len(text) <= 8:
        return "****"
    return f"{text[:4]}****{text[-4:]}"


def safe_value(value: Any, field_name: str = "", depth: int = 0) -> Any:
    """Copy nested metadata while dropping credential-bearing fields."""
    normalized_name = str(field_name or "").strip().lower()
    if (
        normalized_name in SECRET_FIELD_NAMES
        or normalized_name.endswith(("_token", "_password", "_cookie", "_secret"))
    ):
        return None
    if depth > 8:
        return str(value)
    if isinstance(value, dict):
        return {
            str(key): safe_value(raw, str(key), depth + 1)
            for key, raw in value.items()
            if str(key).strip().lower() not in SECRET_FIELD_NAMES
            and not str(key).strip().lower().endswith(
                ("_token", "_password", "_cookie", "_secret")
            )
        }
    if isinstance(value, list):
        return [safe_value(item, "", depth + 1) for item in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def sanitize_error_text(value: Any) -> str:
    text = str(value or "").strip()
    text = re.sub(r"(?i)\bbearer\s+[a-z0-9._~+/=-]+", "Bearer [redacted]", text)
    text = re.sub(
        r"(?i)\b(access_token|refresh_token|password|authorization|token)\b"
        r"(\s*[:=]\s*)(?:\"[^\"]*\"|'[^']*'|[^\s,;]+)",
        r"\1\2[redacted]",
        text,
    )
    return text[:500]


__all__ = [
    "SECRET_FIELD_NAMES",
    "is_masked_key",
    "mask_channel_key",
    "safe_value",
    "sanitize_error_text",
]
