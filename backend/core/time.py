"""Application clock helpers shared by repositories and services."""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


def application_timezone():
    name = os.getenv("APP_TIMEZONE") or os.getenv("TZ") or "Asia/Shanghai"
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError:
        return timezone(timedelta(hours=8), "Asia/Shanghai")


def app_now() -> datetime:
    return datetime.now(application_timezone())


def utc_now_iso() -> str:
    return app_now().isoformat(timespec="seconds")


__all__ = ["app_now", "application_timezone", "utc_now_iso"]
