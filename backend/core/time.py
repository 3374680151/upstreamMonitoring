"""Pure time helpers extracted from the legacy runtime.

This module centralizes timezone handling and datetime utilities so that
both the legacy ``backend.legacy_runtime`` compatibility layer and the
new FastAPI boundary can rely on a single source of truth.
"""

from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

APP_DIR = Path(__file__).resolve().parents[2]


def _load_dotenv() -> None:
    """Load the local .env without adding a dotenv dependency.

    Mirrors the helper in ``backend.core.config`` so that timezone env vars
    are resolved even when this module is imported before the legacy runtime.
    """
    env_path = APP_DIR / ".env"
    if not env_path.exists():
        return
    try:
        for raw in env_path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))
    except OSError:
        return


_load_dotenv()

# Minimum scheduler interval (minutes); mirrors the constant in legacy_runtime.
MIN_INTERVAL_MINUTES = 1

APP_TIMEZONE_NAME = os.getenv("APP_TIMEZONE") or os.getenv("TZ") or "Asia/Shanghai"
try:
    APP_TIMEZONE = ZoneInfo(APP_TIMEZONE_NAME)
except ZoneInfoNotFoundError:
    APP_TIMEZONE_NAME = "Asia/Shanghai"
    APP_TIMEZONE = timezone(timedelta(hours=8), APP_TIMEZONE_NAME)


def app_now() -> datetime:
    return datetime.now(APP_TIMEZONE)


def utc_now_iso() -> str:
    return app_now().isoformat(timespec="seconds")


def parse_iso_dt(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def stable_hash(obj: Any) -> str:
    text = json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def next_check_iso(interval_minutes: int) -> str:
    return (app_now() + timedelta(minutes=max(MIN_INTERVAL_MINUTES, interval_minutes))).isoformat(timespec="seconds")


def fmt_local_time_for_message(value: str) -> str:
    dt = parse_iso_dt(value)
    if not dt:
        return value
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    local_dt = dt.astimezone(APP_TIMEZONE)
    tz_name = local_dt.tzname() or ""
    suffix = f" {tz_name}" if tz_name else ""
    return local_dt.strftime("%Y-%m-%d %H:%M:%S") + suffix
