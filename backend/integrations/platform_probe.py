"""Unauthenticated upstream-platform probes used by discovery sync."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from typing import Any, Iterable

from backend.integrations.transport import normalize_base_url, request_json


def detect_upstream_platform(base_url: str) -> str:
    """Return ``sub2api``, ``newapi`` or ``""`` when the public probe is unknown."""
    base = normalize_base_url(base_url)
    if not base:
        return ""
    ok, payload, _error = request_json(f"{base}/api/v1/models")
    if ok and isinstance(payload, dict) and payload.get("code") == 0:
        return "sub2api"
    ok, payload, _error = request_json(f"{base}/api/pricing")
    if ok and isinstance(payload, dict) and payload.get("success") is True:
        return "newapi"
    return ""


def detect_upstream_platforms(base_urls: Iterable[str]) -> dict[str, str]:
    """Probe unique URLs concurrently; unknown platforms map to an empty string."""
    urls = [
        value
        for value in dict.fromkeys(normalize_base_url(str(item or "")) for item in base_urls)
        if value
    ]
    if len(urls) <= 1:
        return {urls[0]: detect_upstream_platform(urls[0])} if urls else {}
    results: dict[str, str] = {}
    with ThreadPoolExecutor(max_workers=min(8, len(urls))) as executor:
        for url, platform in zip(urls, executor.map(detect_upstream_platform, urls)):
            results[url] = platform
    return results


__all__ = ["detect_upstream_platform", "detect_upstream_platforms"]
