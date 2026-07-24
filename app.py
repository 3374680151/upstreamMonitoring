from __future__ import annotations

import hashlib
import json
import os
import smtplib
import sqlite3
import threading
import time
import traceback
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple
from urllib.parse import parse_qs, urlparse
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


APP_DIR = Path(__file__).resolve().parent
DATA_DIR = APP_DIR / "data"
STATIC_DIR = APP_DIR / "static"
WEB_DIST_DIR = APP_DIR / "apps" / "web" / "dist"
DB_PATH = DATA_DIR / "app.db"
DEFAULT_INTERVAL_MINUTES = 3
MIN_INTERVAL_MINUTES = 1
HTTP_TIMEOUT_SECONDS = 15
SCAN_INTERVAL_SECONDS = 10
SERVER_HOST = os.getenv("HOST", "127.0.0.1")
SERVER_PORT = int(os.getenv("PORT", "8000"))
APP_TIMEZONE_NAME = os.getenv("APP_TIMEZONE") or os.getenv("TZ") or "Asia/Shanghai"
try:
    APP_TIMEZONE = ZoneInfo(APP_TIMEZONE_NAME)
except ZoneInfoNotFoundError:
    APP_TIMEZONE_NAME = "Asia/Shanghai"
    APP_TIMEZONE = timezone(timedelta(hours=8), APP_TIMEZONE_NAME)

DB_LOCK = threading.RLock()
STOP_EVENT = threading.Event()
MODEL_CACHE_TTL_SECONDS = 90
UPTIME_CACHE_TTL_SECONDS = 300
MODEL_DATA_CACHE: Dict[int, Dict[str, Any]] = {}
MODEL_CACHE_REFRESHING: set[int] = set()
MODEL_CACHE_LOCK = threading.RLock()
NEWAPI_UPTIME_CACHE: Dict[str, Dict[str, Any]] = {}
NEWAPI_UPTIME_REFRESHING: set[str] = set()


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


def normalize_base_url(value: str) -> str:
    value = (value or "").strip().rstrip("/")
    if not value:
        return value
    return value


def ensure_dirs() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    STATIC_DIR.mkdir(parents=True, exist_ok=True)


def connect_db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    return conn


def init_db() -> None:
    with DB_LOCK, connect_db() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS sites (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                base_url TEXT NOT NULL UNIQUE,
                platform TEXT NOT NULL DEFAULT 'newapi',
                enabled INTEGER NOT NULL DEFAULT 1,
                interval_minutes INTEGER NOT NULL DEFAULT 3,
                focus_keywords TEXT,
                login_enabled INTEGER NOT NULL DEFAULT 0,
                auth_mode TEXT NOT NULL DEFAULT 'password',
                login_username TEXT,
                login_password TEXT,
                access_token TEXT,
                access_user_id TEXT,
                refresh_token TEXT,
                token_expires_at TEXT,
                status TEXT NOT NULL DEFAULT 'unknown',
                last_error TEXT,
                last_check_at TEXT,
                next_check_at TEXT,
                consecutive_failures INTEGER NOT NULL DEFAULT 0,
                current_groups_json TEXT,
                current_login_groups_json TEXT,
                login_last_error TEXT,
                login_last_check_at TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                site_id INTEGER NOT NULL,
                status TEXT NOT NULL,
                source TEXT NOT NULL DEFAULT '/api/user/groups',
                groups_json TEXT,
                raw_json TEXT,
                hash TEXT,
                error_message TEXT,
                checked_at TEXT NOT NULL,
                FOREIGN KEY(site_id) REFERENCES sites(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS changes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                site_id INTEGER NOT NULL,
                change_type TEXT NOT NULL,
                group_name TEXT,
                old_value TEXT,
                new_value TEXT,
                change_percent REAL,
                message TEXT NOT NULL,
                created_at TEXT NOT NULL,
                acknowledged INTEGER NOT NULL DEFAULT 0,
                FOREIGN KEY(site_id) REFERENCES sites(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS notification_settings (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                qq_enabled INTEGER NOT NULL DEFAULT 0,
                qq_app_id TEXT,
                qq_client_secret TEXT,
                qq_group_openid TEXT,
                qq_access_token TEXT,
                qq_token_expires_at TEXT,
                qq_last_error TEXT,
                qq_last_sent_at TEXT,
                wecom_enabled INTEGER NOT NULL DEFAULT 0,
                wecom_webhook TEXT,
                wecom_last_error TEXT,
                wecom_last_sent_at TEXT,
                email_enabled INTEGER NOT NULL DEFAULT 0,
                smtp_host TEXT,
                smtp_port INTEGER NOT NULL DEFAULT 465,
                smtp_username TEXT,
                smtp_password TEXT,
                smtp_use_ssl INTEGER NOT NULL DEFAULT 1,
                smtp_from TEXT,
                smtp_to TEXT,
                email_last_error TEXT,
                email_last_sent_at TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS notification_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                channel TEXT NOT NULL,
                status TEXT NOT NULL,
                target TEXT,
                message TEXT,
                error_message TEXT,
                created_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_sites_enabled_next_check ON sites(enabled, next_check_at);
            CREATE INDEX IF NOT EXISTS idx_snapshots_site_checked ON snapshots(site_id, checked_at DESC);
            CREATE INDEX IF NOT EXISTS idx_changes_site_created ON changes(site_id, created_at DESC);
            CREATE INDEX IF NOT EXISTS idx_notification_logs_created ON notification_logs(created_at DESC);
            """
        )
        columns = {
            row["name"]
            for row in conn.execute("PRAGMA table_info(sites)").fetchall()
        }
        if "focus_keywords" not in columns:
            conn.execute("ALTER TABLE sites ADD COLUMN focus_keywords TEXT")
        if "login_enabled" not in columns:
            conn.execute("ALTER TABLE sites ADD COLUMN login_enabled INTEGER NOT NULL DEFAULT 0")
        if "auth_mode" not in columns:
            conn.execute("ALTER TABLE sites ADD COLUMN auth_mode TEXT NOT NULL DEFAULT 'password'")
        if "login_username" not in columns:
            conn.execute("ALTER TABLE sites ADD COLUMN login_username TEXT")
        if "login_password" not in columns:
            conn.execute("ALTER TABLE sites ADD COLUMN login_password TEXT")
        if "access_token" not in columns:
            conn.execute("ALTER TABLE sites ADD COLUMN access_token TEXT")
        if "access_user_id" not in columns:
            conn.execute("ALTER TABLE sites ADD COLUMN access_user_id TEXT")
        if "refresh_token" not in columns:
            conn.execute("ALTER TABLE sites ADD COLUMN refresh_token TEXT")
        if "token_expires_at" not in columns:
            conn.execute("ALTER TABLE sites ADD COLUMN token_expires_at TEXT")
        if "current_login_groups_json" not in columns:
            conn.execute("ALTER TABLE sites ADD COLUMN current_login_groups_json TEXT")
        if "login_last_error" not in columns:
            conn.execute("ALTER TABLE sites ADD COLUMN login_last_error TEXT")
        if "login_last_check_at" not in columns:
            conn.execute("ALTER TABLE sites ADD COLUMN login_last_check_at TEXT")
        setting_columns = {
            row["name"]
            for row in conn.execute("PRAGMA table_info(notification_settings)").fetchall()
        }
        notification_columns = {
            "email_enabled": "INTEGER NOT NULL DEFAULT 0",
            "wecom_enabled": "INTEGER NOT NULL DEFAULT 0",
            "wecom_webhook": "TEXT",
            "wecom_last_error": "TEXT",
            "wecom_last_sent_at": "TEXT",
            "smtp_host": "TEXT",
            "smtp_port": "INTEGER NOT NULL DEFAULT 465",
            "smtp_username": "TEXT",
            "smtp_password": "TEXT",
            "smtp_use_ssl": "INTEGER NOT NULL DEFAULT 1",
            "smtp_from": "TEXT",
            "smtp_to": "TEXT",
            "email_last_error": "TEXT",
            "email_last_sent_at": "TEXT",
        }
        for column_name, column_type in notification_columns.items():
            if column_name not in setting_columns:
                conn.execute(f"ALTER TABLE notification_settings ADD COLUMN {column_name} {column_type}")
        setting = conn.execute("SELECT id FROM notification_settings WHERE id = 1").fetchone()
        if not setting:
            now = utc_now_iso()
            conn.execute(
                """
                INSERT INTO notification_settings
                (id, qq_enabled, qq_app_id, qq_client_secret, qq_group_openid, qq_access_token, qq_token_expires_at, qq_last_error, qq_last_sent_at, wecom_enabled, wecom_webhook, wecom_last_error, wecom_last_sent_at, email_enabled, smtp_host, smtp_port, smtp_username, smtp_password, smtp_use_ssl, smtp_from, smtp_to, email_last_error, email_last_sent_at, created_at, updated_at)
                VALUES (1, 0, '', '', '', NULL, NULL, NULL, NULL, 0, '', NULL, NULL, 0, '', 465, '', '', 1, '', '', NULL, NULL, ?, ?)
                """,
                (now, now),
            )
        conn.execute("UPDATE notification_settings SET qq_enabled = 0, qq_last_error = NULL")


def dict_from_row(row: sqlite3.Row) -> Dict[str, Any]:
    return dict(row)


def db_query_all(sql: str, params: Iterable[Any] = ()) -> List[Dict[str, Any]]:
    with DB_LOCK, connect_db() as conn:
        rows = conn.execute(sql, tuple(params)).fetchall()
        return [dict_from_row(row) for row in rows]


def db_query_one(sql: str, params: Iterable[Any] = ()) -> Optional[Dict[str, Any]]:
    with DB_LOCK, connect_db() as conn:
        row = conn.execute(sql, tuple(params)).fetchone()
        return dict_from_row(row) if row else None


def db_execute(sql: str, params: Iterable[Any] = ()) -> int:
    with DB_LOCK, connect_db() as conn:
        cur = conn.execute(sql, tuple(params))
        conn.commit()
        return cur.lastrowid


def db_execute_many(sql: str, params_list: Iterable[Iterable[Any]]) -> None:
    with DB_LOCK, connect_db() as conn:
        conn.executemany(sql, params_list)
        conn.commit()


def json_request(
    url: str,
    payload: Dict[str, Any],
    headers: Optional[Dict[str, str]] = None,
    method: str = "POST",
) -> Tuple[int, Dict[str, Any], str]:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request_headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "User-Agent": "Upstream-Ratio-Watch/1.0",
    }
    if headers:
        request_headers.update(headers)
    req = urllib.request.Request(url, data=data, headers=request_headers, method=method)
    with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT_SECONDS) as resp:
        raw = resp.read().decode("utf-8", errors="replace")
        try:
            payload_obj = json.loads(raw) if raw else {}
        except Exception:
            payload_obj = {"raw": raw}
        if not isinstance(payload_obj, dict):
            payload_obj = {"raw": raw}
        return resp.status, payload_obj, raw


def parse_groups_payload(payload: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    data = payload.get("data") or {}
    if not isinstance(data, dict):
        return {}

    normalized: Dict[str, Dict[str, Any]] = {}
    for name in sorted(data.keys()):
        info = data.get(name) or {}
        if not isinstance(info, dict):
            info = {}

        ratio = info.get("ratio")
        if isinstance(ratio, (int, float)):
            ratio_value: Any = float(ratio)
            ratio_type = "number"
        elif isinstance(ratio, str):
            stripped = ratio.strip()
            try:
                ratio_value = float(stripped)
                ratio_type = "number"
            except ValueError:
                ratio_value = stripped
                ratio_type = "text"
        else:
            ratio_value = ratio
            ratio_type = "text"

        normalized[name] = {
            "ratio": ratio_value,
            "ratio_type": ratio_type,
            "desc": info.get("desc", ""),
        }
    return normalized


def parse_newapi_models_by_group(
    pricing_payload: Any,
    uptime_payload: Any,
    groups: Dict[str, Dict[str, Any]],
) -> Dict[str, List[Dict[str, Any]]]:
    if isinstance(pricing_payload, dict) and "data" in pricing_payload:
        pricing_payload = pricing_payload.get("data")
    if isinstance(uptime_payload, dict) and "data" in uptime_payload:
        uptime_payload = uptime_payload.get("data")
    if not isinstance(pricing_payload, list) or not isinstance(groups, dict):
        return {}

    monitors: List[Dict[str, Any]] = []
    if isinstance(uptime_payload, list):
        for category in uptime_payload:
            if not isinstance(category, dict):
                continue
            category_name = str(category.get("categoryName") or "").strip()
            for monitor in category.get("monitors") or []:
                if not isinstance(monitor, dict):
                    continue
                entry = dict(monitor)
                entry["category"] = category_name
                monitors.append(entry)

    def matching_monitor(model_name: str) -> Optional[Dict[str, Any]]:
        normalized = model_name.casefold()
        exact = [item for item in monitors if str(item.get("name") or "").strip().casefold() == normalized]
        if exact:
            return exact[0]
        fuzzy = [
            item for item in monitors
            if str(item.get("name") or "").strip()
            and (
                normalized in str(item.get("name") or "").strip().casefold()
                or str(item.get("name") or "").strip().casefold() in normalized
            )
        ]
        return fuzzy[0] if len(fuzzy) == 1 else None

    def monitor_status(value: Any) -> str:
        try:
            status = int(value)
        except (TypeError, ValueError):
            return "configured"
        return {0: "error", 1: "operational", 2: "degraded", 3: "maintenance"}.get(status, "configured")

    models_by_group: Dict[str, List[Dict[str, Any]]] = {}
    seen: set[Tuple[str, str]] = set()
    for pricing in pricing_payload:
        if not isinstance(pricing, dict):
            continue
        model_name = str(pricing.get("model_name") or pricing.get("name") or "").strip()
        if not model_name:
            continue
        enabled_groups = pricing.get("enable_groups") or pricing.get("groups") or []
        if isinstance(enabled_groups, str):
            enabled_groups = [enabled_groups]
        if not isinstance(enabled_groups, list):
            enabled_groups = []
        enabled_names = {str(name).strip() for name in enabled_groups if str(name).strip()}
        target_groups = list(groups.keys()) if "all" in enabled_names else [name for name in groups if name in enabled_names]
        if not target_groups:
            continue

        monitor = matching_monitor(model_name)
        uptime_value = monitor.get("uptime") if monitor else None
        try:
            availability = float(uptime_value)
            if 0 <= availability <= 1:
                availability *= 100
        except (TypeError, ValueError):
            availability = None

        ratio_value = pricing.get("model_ratio")
        try:
            ratio_value = float(ratio_value)
            ratio_type = "number"
        except (TypeError, ValueError):
            ratio_type = "text"

        for group_name in target_groups:
            key = (group_name, model_name.casefold())
            if key in seen:
                continue
            seen.add(key)
            group_info = groups.get(group_name) or {}
            models_by_group.setdefault(group_name, []).append({
                "name": model_name,
                "ratio": ratio_value,
                "ratio_type": ratio_type,
                "group_ratio": group_info.get("ratio"),
                "channel": str(monitor.get("category") or "") if monitor else "",
                "platform": pricing.get("owner_by") or "NewAPI",
                "status": monitor_status(monitor.get("status")) if monitor else "configured",
                "latency_ms": None,
                "ping_latency_ms": None,
                "availability_7d": availability,
                "availability_label": "24 小时" if availability is not None else "",
                "timeline": [],
                "monitor": str(monitor.get("name") or "") if monitor else model_name,
                "source": "NewAPI 公开监控" if monitor else "NewAPI 模型配置",
                "completion_ratio": pricing.get("completion_ratio"),
            })

    for model_list in models_by_group.values():
        model_list.sort(key=lambda item: str(item.get("name") or "").casefold())
    return models_by_group


def parse_sub2api_groups(groups_payload: Any, rates_payload: Any = None) -> Dict[str, Dict[str, Any]]:
    if isinstance(groups_payload, dict) and "data" in groups_payload:
        groups_payload = groups_payload.get("data")
    if isinstance(rates_payload, dict) and "data" in rates_payload:
        rates_payload = rates_payload.get("data")
    if not isinstance(groups_payload, list):
        return {}
    rates: Dict[str, Any] = {}
    if isinstance(rates_payload, dict):
        rates = {str(key): value for key, value in rates_payload.items()}

    normalized: Dict[str, Dict[str, Any]] = {}
    for item in groups_payload:
        if not isinstance(item, dict):
            continue
        group_id = item.get("id")
        name = str(item.get("name") or group_id or "").strip()
        if not name:
            continue
        base_ratio = item.get("rate_multiplier")
        effective_ratio = rates.get(str(group_id), base_ratio)
        try:
            ratio_value: Any = float(effective_ratio)
            ratio_type = "number"
        except (TypeError, ValueError):
            ratio_value = effective_ratio
            ratio_type = "text"
        normalized[name] = {
            "ratio": ratio_value,
            "ratio_type": ratio_type,
            "desc": item.get("description") or "",
            "id": group_id,
            "platform": item.get("platform") or "",
            "base_ratio": base_ratio,
            "user_ratio": rates.get(str(group_id)),
            "status": item.get("status") or "",
            "is_exclusive": bool(item.get("is_exclusive")),
            "subscription_type": item.get("subscription_type") or "",
            "rpm_limit": item.get("rpm_limit"),
        }
    return normalized


def parse_sub2api_channel_models(
    channels_payload: Any,
    groups: Dict[str, Dict[str, Any]],
) -> Dict[str, List[Dict[str, Any]]]:
    """Map the user-facing channel model list back to the monitored groups.

    sub2api's user endpoint groups models by channel and platform. A group in
    the same channel/platform section shares that section's model list, while
    its own group multiplier remains the effective multiplier for each model.
    """
    if isinstance(channels_payload, dict) and "data" in channels_payload:
        channels_payload = channels_payload.get("data")
    if not isinstance(channels_payload, list) or not isinstance(groups, dict):
        return {}

    groups_by_id = {
        str(item.get("id")): (name, item)
        for name, item in groups.items()
        if isinstance(item, dict) and item.get("id") is not None
    }
    models_by_group: Dict[str, List[Dict[str, Any]]] = {}
    seen: set[Tuple[str, str, str]] = set()

    for channel in channels_payload:
        if not isinstance(channel, dict):
            continue
        channel_name = str(channel.get("name") or "").strip()
        sections = channel.get("platforms") or []
        if not isinstance(sections, list):
            continue
        for section in sections:
            if not isinstance(section, dict):
                continue
            models = section.get("supported_models") or section.get("models") or []
            group_refs = section.get("groups") or []
            if not isinstance(models, list) or not isinstance(group_refs, list):
                continue

            matched_groups: List[Tuple[str, Dict[str, Any]]] = []
            for group_ref in group_refs:
                if not isinstance(group_ref, dict):
                    continue
                group_id = group_ref.get("id")
                matched = groups_by_id.get(str(group_id)) if group_id is not None else None
                if not matched:
                    group_name = str(group_ref.get("name") or "").strip()
                    group_info = groups.get(group_name)
                    matched = (group_name, group_info) if isinstance(group_info, dict) else None
                if matched:
                    matched_groups.append(matched)

            for group_name, group_info in matched_groups:
                destination = models_by_group.setdefault(group_name, [])
                for model in models:
                    if not isinstance(model, dict):
                        continue
                    model_name = str(model.get("name") or model.get("id") or model.get("model") or "").strip()
                    if not model_name:
                        continue
                    model_key = (group_name, channel_name, model_name)
                    if model_key in seen:
                        continue
                    seen.add(model_key)

                    raw_status = model.get("status") or model.get("health") or model.get("state") or ""
                    if not raw_status and isinstance(model.get("available"), bool):
                        raw_status = "可用" if model["available"] else "不可用"
                    if not raw_status and isinstance(model.get("enabled"), bool):
                        raw_status = "启用" if model["enabled"] else "停用"

                    destination.append({
                        "name": model_name,
                        "ratio": group_info.get("ratio"),
                        "ratio_type": group_info.get("ratio_type") or "text",
                        "channel": channel_name,
                        "platform": model.get("platform") or section.get("platform") or group_info.get("platform") or "",
                        "status": str(raw_status),
                    })

    for model_list in models_by_group.values():
        model_list.sort(key=lambda item: (str(item.get("name") or "").lower(), str(item.get("channel") or "").lower()))
    return models_by_group


def parse_sub2api_monitor_models(
    monitors_payload: Any,
    groups: Dict[str, Dict[str, Any]],
) -> Tuple[Dict[str, List[Dict[str, Any]]], List[Dict[str, Any]]]:
    """Attach sub2api's read-only channel monitor statuses to known groups.

    The monitor API normally includes ``group_name``. Some deployments leave
    it empty; in that case only a single candidate group can be matched safely.
    Ambiguous monitors stay separate instead of being assigned to a wrong group.
    """
    if isinstance(monitors_payload, dict) and "data" in monitors_payload:
        monitors_payload = monitors_payload.get("data")
    items = monitors_payload.get("items") if isinstance(monitors_payload, dict) else None
    if not isinstance(items, list) or not isinstance(groups, dict):
        return {}, []

    models_by_group: Dict[str, List[Dict[str, Any]]] = {}
    unmatched: List[Dict[str, Any]] = []

    def resolve_group(monitor: Dict[str, Any]) -> Optional[Tuple[str, Dict[str, Any]]]:
        group_name = str(monitor.get("group_name") or "").strip()
        group_info = groups.get(group_name)
        if isinstance(group_info, dict):
            return group_name, group_info
        provider = str(monitor.get("provider") or "").strip().lower()
        platform_matches = [
            (name, item)
            for name, item in groups.items()
            if isinstance(item, dict) and str(item.get("platform") or "").strip().lower() == provider
        ]
        if len(platform_matches) == 1:
            return platform_matches[0]
        if len(groups) == 1:
            name, item = next(iter(groups.items()))
            return (name, item) if isinstance(item, dict) else None
        return None

    for monitor in items:
        if not isinstance(monitor, dict):
            continue
        monitor_name = str(monitor.get("name") or "").strip()
        target_group = resolve_group(monitor)
        monitored_models: List[Dict[str, Any]] = []
        primary_model = str(monitor.get("primary_model") or "").strip()
        timeline = []
        for point in monitor.get("timeline") or []:
            if not isinstance(point, dict):
                continue
            timeline.append({
                "status": str(point.get("status") or ""),
                "latency_ms": point.get("latency_ms"),
                "ping_latency_ms": point.get("ping_latency_ms"),
                "checked_at": str(point.get("checked_at") or ""),
            })
            if len(timeline) >= 60:
                break
        if primary_model:
            monitored_models.append({
                "name": primary_model,
                "status": str(monitor.get("primary_status") or ""),
                "latency_ms": monitor.get("primary_latency_ms"),
                "ping_latency_ms": monitor.get("primary_ping_latency_ms"),
                "availability_7d": monitor.get("availability_7d"),
                "timeline": timeline,
            })
        for extra in monitor.get("extra_models") or []:
            if not isinstance(extra, dict):
                continue
            model_name = str(extra.get("model") or "").strip()
            if model_name:
                monitored_models.append({
                    "name": model_name,
                    "status": str(extra.get("status") or ""),
                    "latency_ms": extra.get("latency_ms"),
                    "ping_latency_ms": None,
                    "availability_7d": None,
                    "timeline": [],
                })

        if not target_group:
            unmatched.extend({
                "name": item["name"],
                "status": item.get("status") or "",
                "monitor": monitor_name,
                "provider": monitor.get("provider") or "",
            } for item in monitored_models)
            continue

        group_name, group_info = target_group
        destination = models_by_group.setdefault(group_name, [])
        for item in monitored_models:
            destination.append({
                "name": item["name"],
                "ratio": group_info.get("ratio"),
                "ratio_type": group_info.get("ratio_type") or "text",
                "channel": "",
                "platform": monitor.get("provider") or group_info.get("platform") or "",
                "status": item["status"],
                "latency_ms": item["latency_ms"],
                "ping_latency_ms": item["ping_latency_ms"],
                "availability_7d": item["availability_7d"],
                "timeline": item["timeline"],
                "monitor": monitor_name,
                "source": "上游监控",
            })

    return models_by_group, unmatched


def merge_sub2api_group_models(
    configured_models: Dict[str, List[Dict[str, Any]]],
    monitored_models: Dict[str, List[Dict[str, Any]]],
) -> Dict[str, List[Dict[str, Any]]]:
    """Use the monitor status as authoritative while retaining configured-only models."""
    merged = {name: [dict(item) for item in values] for name, values in configured_models.items()}
    for group_name, items in monitored_models.items():
        destination = merged.setdefault(group_name, [])
        indexes = {str(item.get("name") or "").casefold(): index for index, item in enumerate(destination)}
        for item in items:
            key = str(item.get("name") or "").casefold()
            if key in indexes:
                destination[indexes[key]].update({key: value for key, value in item.items() if value not in (None, "")})
            else:
                indexes[key] = len(destination)
                destination.append(dict(item))
    for model_list in merged.values():
        model_list.sort(key=lambda item: str(item.get("name") or "").casefold())
    return merged


def stable_hash(obj: Any) -> str:
    text = json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def next_check_iso(interval_minutes: int) -> str:
    return (app_now() + timedelta(minutes=max(MIN_INTERVAL_MINUTES, interval_minutes))).isoformat(timespec="seconds")


def fetch_newapi_groups(base_url: str) -> Tuple[bool, Dict[str, Any], Optional[str]]:
    url = f"{normalize_base_url(base_url)}/api/user/groups"
    req = urllib.request.Request(
        url,
        headers={
            "Accept": "application/json",
            "User-Agent": "Upstream-Ratio-Watch/1.0",
        },
        method="GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT_SECONDS) as resp:
            body = resp.read().decode("utf-8", errors="replace")
            payload = json.loads(body)
            if not isinstance(payload, dict) or not payload.get("success"):
                return False, payload if isinstance(payload, dict) else {"raw": body}, "success=false"
            return True, payload, None
    except urllib.error.HTTPError as exc:
        try:
            raw = exc.read().decode("utf-8", errors="replace")
        except Exception:
            raw = ""
        return False, {"status": exc.code, "raw": raw}, f"HTTP {exc.code}"
    except Exception as exc:
        return False, {"error": str(exc)}, str(exc)


def request_json(url: str, headers: Optional[Dict[str, str]] = None, payload: Optional[Dict[str, Any]] = None, method: str = "GET") -> Tuple[bool, Any, Optional[str]]:
    data = None
    request_headers = {
        "Accept": "application/json",
        "User-Agent": "Upstream-Ratio-Watch/1.0",
    }
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request_headers["Content-Type"] = "application/json"
    if headers:
        request_headers.update(headers)
    req = urllib.request.Request(url, data=data, headers=request_headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT_SECONDS) as resp:
            body = resp.read().decode("utf-8", errors="replace")
            parsed = json.loads(body) if body else {}
            return True, parsed, None
    except urllib.error.HTTPError as exc:
        try:
            raw = exc.read().decode("utf-8", errors="replace")
        except Exception:
            raw = ""
        return False, {"status": exc.code, "raw": raw}, f"HTTP {exc.code}"
    except Exception as exc:
        return False, {"error": str(exc)}, str(exc)


def refresh_newapi_uptime_cache(base_url: str, headers: Dict[str, str]) -> None:
    normalized_base = normalize_base_url(base_url)
    try:
        uptime_ok, uptime_payload, uptime_error = request_json(
            f"{normalized_base}/api/uptime/status",
            headers=headers,
        )
        if uptime_ok and isinstance(uptime_payload, dict) and uptime_payload.get("success"):
            with MODEL_CACHE_LOCK:
                NEWAPI_UPTIME_CACHE[normalized_base] = {
                    "payload": uptime_payload,
                    "updated_monotonic": time.monotonic(),
                    "error": None,
                }
            matching_sites = db_query_all("SELECT id FROM sites WHERE base_url = ?", (normalized_base,))
            for site in matching_sites:
                invalidate_site_model_cache(int(site["id"]))
                schedule_model_cache_refresh(int(site["id"]))
        elif uptime_error:
            with MODEL_CACHE_LOCK:
                previous = NEWAPI_UPTIME_CACHE.get(normalized_base, {})
                previous["error"] = uptime_error
                NEWAPI_UPTIME_CACHE[normalized_base] = previous
    finally:
        with MODEL_CACHE_LOCK:
            NEWAPI_UPTIME_REFRESHING.discard(normalized_base)


def get_cached_newapi_uptime(base_url: str, headers: Dict[str, str]) -> Tuple[Dict[str, Any], Optional[str]]:
    normalized_base = normalize_base_url(base_url)
    with MODEL_CACHE_LOCK:
        entry = NEWAPI_UPTIME_CACHE.get(normalized_base)
        age = time.monotonic() - float(entry.get("updated_monotonic") or 0) if entry else float("inf")
        if age >= UPTIME_CACHE_TTL_SECONDS and normalized_base not in NEWAPI_UPTIME_REFRESHING:
            NEWAPI_UPTIME_REFRESHING.add(normalized_base)
            threading.Thread(
                target=refresh_newapi_uptime_cache,
                args=(normalized_base, dict(headers)),
                daemon=True,
            ).start()
        if entry and isinstance(entry.get("payload"), dict):
            return entry["payload"], entry.get("error")
        return {"success": True, "data": []}, "公开监控正在后台刷新"


def fetch_newapi_model_data(
    base_url: str,
    access_token: str = "",
    user_id: str = "",
) -> Tuple[bool, Dict[str, Any], Optional[str]]:
    headers: Dict[str, str] = {}
    token = (access_token or "").strip()
    if token:
        headers["Authorization"] = token.removeprefix("Bearer ").removeprefix("bearer ").strip()
    if str(user_id or "").strip():
        headers["New-Api-User"] = str(user_id).strip()

    normalized_base = normalize_base_url(base_url)
    pricing_ok, pricing_payload, pricing_error = request_json(
        f"{normalized_base}/api/pricing",
        headers=headers,
    )
    if pricing_ok and (not isinstance(pricing_payload, dict) or not pricing_payload.get("success")):
        pricing_ok = False
        pricing_error = str(pricing_payload.get("message") or "pricing success=false") if isinstance(pricing_payload, dict) else "pricing 响应异常"
    if not pricing_ok:
        return False, {"pricing": pricing_payload}, pricing_error or "读取 NewAPI 模型配置失败"

    uptime_payload, uptime_error = get_cached_newapi_uptime(normalized_base, headers)

    return True, {
        "success": True,
        "pricing": pricing_payload,
        "uptime": uptime_payload,
        "uptime_error": uptime_error,
    }, None


def is_sub2api_auth_error(payload: Any, error: Optional[str] = None) -> bool:
    if isinstance(payload, dict):
        for key in ("groups", "channels", "monitors", "refresh"):
            if isinstance(payload.get(key), dict) and is_sub2api_auth_error(payload[key], error):
                return True
        status = payload.get("status")
        raw = str(payload.get("raw") or "")
        message = str(payload.get("message") or payload.get("error") or "")
        code = str(payload.get("code") or "")
        if status in {401, 403}:
            return True
        text = f"{raw} {message} {code}".lower()
        return any(word in text for word in ("unauthorized", "forbidden", "token", "jwt", "auth"))
    return bool(error and error.startswith(("HTTP 401", "HTTP 403")))


def unwrap_sub2api_response(payload: Any) -> Tuple[bool, Any, Optional[str]]:
    if not isinstance(payload, dict):
        return False, payload, "响应不是 JSON 对象"
    if "code" in payload and payload.get("code") != 0:
        return False, payload, str(payload.get("message") or "code != 0")
    return True, payload.get("data"), None


def sub2api_login(base_url: str, username: str, password: str) -> Tuple[bool, str, Dict[str, Any], Optional[str]]:
    email = (username or "").strip()
    password = password or ""
    if not email or not password:
        return False, "", {}, "sub2api 需要填写普通用户邮箱和密码"
    ok, payload, error = request_json(
        f"{normalize_base_url(base_url)}/api/v1/auth/login",
        payload={"email": email, "password": password},
        method="POST",
    )
    if not ok:
        return False, "", payload if isinstance(payload, dict) else {"raw": payload}, error
    success, data, message = unwrap_sub2api_response(payload)
    if not success or not isinstance(data, dict):
        return False, "", payload if isinstance(payload, dict) else {"raw": payload}, message or "登录失败"
    token = str(data.get("access_token") or "").strip()
    if not token:
        return False, "", payload if isinstance(payload, dict) else {"raw": payload}, "登录成功但没有返回 access_token"
    return True, token, payload if isinstance(payload, dict) else {"raw": payload}, None


def sub2api_refresh_token(base_url: str, refresh_token: str) -> Tuple[bool, Dict[str, Any], Optional[str]]:
    token = (refresh_token or "").strip()
    if not token:
        return False, {}, "refresh_token 为空"
    ok, payload, error = request_json(
        f"{normalize_base_url(base_url)}/api/v1/auth/refresh",
        payload={"refresh_token": token},
        method="POST",
    )
    if not ok:
        return False, payload if isinstance(payload, dict) else {"raw": payload}, error
    success, data, message = unwrap_sub2api_response(payload)
    if not success or not isinstance(data, dict):
        return False, payload if isinstance(payload, dict) else {"raw": payload}, message or "刷新登录态失败"
    return True, data, None


def sub2api_token_headers(access_token: str) -> Dict[str, str]:
    token = (access_token or "").strip()
    if token.lower().startswith("bearer "):
        return {"Authorization": token}
    return {"Authorization": f"Bearer {token}"}


def fetch_sub2api_groups_by_token(base_url: str, access_token: str) -> Tuple[bool, Dict[str, Any], Optional[str]]:
    token = (access_token or "").strip()
    if not token:
        return False, {}, "auth_token 为空"
    headers = sub2api_token_headers(token)
    groups_ok, groups_payload, groups_error = request_json(
        f"{normalize_base_url(base_url)}/api/v1/groups/available",
        headers=headers,
    )
    if not groups_ok:
        return False, {"groups": groups_payload}, groups_error or "用户可用分组请求失败"
    groups_success, groups_data, groups_message = unwrap_sub2api_response(groups_payload)
    if not groups_success:
        return False, {"groups": groups_payload}, groups_message or "用户可用分组响应失败"

    rates_ok, rates_payload, rates_error = request_json(
        f"{normalize_base_url(base_url)}/api/v1/groups/rates",
        headers=headers,
    )
    rates_data: Any = {}
    if rates_ok:
        rates_success, parsed_rates, _ = unwrap_sub2api_response(rates_payload)
        if rates_success and isinstance(parsed_rates, dict):
            rates_data = parsed_rates

    return True, {
        "success": True,
        "data": groups_data,
        "user_rates": rates_data,
        "rates_error": None if rates_ok else rates_error,
    }, None


def fetch_sub2api_channel_models_by_token(base_url: str, access_token: str) -> Tuple[bool, Dict[str, Any], Optional[str]]:
    token = (access_token or "").strip()
    if not token:
        return False, {}, "auth_token 为空"
    ok, payload, error = request_json(
        f"{normalize_base_url(base_url)}/api/v1/channels/available",
        headers=sub2api_token_headers(token),
    )
    if not ok:
        return False, {"channels": payload}, error or "用户可用渠道请求失败"
    success, data, message = unwrap_sub2api_response(payload)
    if not success:
        return False, {"channels": payload}, message or "用户可用渠道响应失败"
    if not isinstance(data, list):
        return False, {"channels": payload}, "用户可用渠道响应不是列表"
    return True, {"success": True, "data": data}, None


def fetch_sub2api_channel_monitors_by_token(base_url: str, access_token: str) -> Tuple[bool, Dict[str, Any], Optional[str]]:
    token = (access_token or "").strip()
    if not token:
        return False, {}, "auth_token 为空"
    ok, payload, error = request_json(
        f"{normalize_base_url(base_url)}/api/v1/channel-monitors",
        headers=sub2api_token_headers(token),
    )
    if not ok:
        return False, {"monitors": payload}, error or "上游模型状态请求失败"
    success, data, message = unwrap_sub2api_response(payload)
    if not success:
        return False, {"monitors": payload}, message or "上游模型状态响应失败"
    if not isinstance(data, dict) or not isinstance(data.get("items"), list):
        return False, {"monitors": payload}, "上游模型状态响应格式异常"
    return True, {"success": True, "data": data}, None


def fetch_sub2api_model_data_by_token(base_url: str, access_token: str) -> Tuple[bool, Dict[str, Any], Optional[str]]:
    channels_ok, channels_payload, channels_error = fetch_sub2api_channel_models_by_token(base_url, access_token)
    monitors_ok, monitors_payload, monitors_error = fetch_sub2api_channel_monitors_by_token(base_url, access_token)
    if not channels_ok and not monitors_ok:
        errors = [message for message in (channels_error, monitors_error) if message]
        return False, {"channels": channels_payload, "monitors": monitors_payload}, "；".join(errors) or "读取上游模型数据失败"
    return True, {
        "success": True,
        "channels": channels_payload.get("data") if channels_ok else [],
        "monitors": monitors_payload.get("data") if monitors_ok else {"items": []},
        "channels_error": None if channels_ok else channels_error,
        "monitors_error": None if monitors_ok else monitors_error,
    }, None


def fetch_sub2api_model_data(
    base_url: str,
    username: str = "",
    password: str = "",
    auth_mode: str = "password",
    access_token: str = "",
    refresh_token: str = "",
) -> Tuple[bool, Dict[str, Any], Optional[str]]:
    mode = (auth_mode or "password").strip().lower()
    if mode == "token":
        ok, payload, error_message = fetch_sub2api_model_data_by_token(base_url, access_token)
        if ok or not refresh_token or not is_sub2api_auth_error(payload, error_message):
            return ok, payload, error_message
        refresh_ok, refreshed, refresh_error = sub2api_refresh_token(base_url, refresh_token)
        if not refresh_ok:
            return False, {"channels": payload, "refresh": refreshed}, refresh_error or error_message or "登录态刷新失败"
        new_access_token = str(refreshed.get("access_token") or "").strip()
        if not new_access_token:
            return False, {"refresh": refreshed}, "刷新成功但没有返回 access_token"
        ok, payload, error_message = fetch_sub2api_model_data_by_token(base_url, new_access_token)
        if isinstance(payload, dict):
            payload["refreshed_auth"] = {
                "access_token": new_access_token,
                "refresh_token": str(refreshed.get("refresh_token") or refresh_token).strip(),
                "expires_in": refreshed.get("expires_in"),
            }
        return ok, payload, error_message

    login_ok, token, login_payload, login_error = sub2api_login(base_url, username, password)
    if not login_ok:
        return False, {"login": login_payload}, login_error or "登录失败"
    return fetch_sub2api_model_data_by_token(base_url, token)


def fetch_sub2api_user_groups(
    base_url: str,
    username: str = "",
    password: str = "",
    auth_mode: str = "password",
    access_token: str = "",
    refresh_token: str = "",
) -> Tuple[bool, Dict[str, Any], Optional[str]]:
    mode = (auth_mode or "password").strip().lower()
    if mode == "token":
        ok, payload, error_message = fetch_sub2api_groups_by_token(base_url, access_token)
        if ok or not refresh_token or not is_sub2api_auth_error(payload, error_message):
            return ok, payload, error_message
        refresh_ok, refreshed, refresh_error = sub2api_refresh_token(base_url, refresh_token)
        if not refresh_ok:
            return False, {"groups": payload, "refresh": refreshed}, refresh_error or error_message or "登录态刷新失败"
        new_access_token = str(refreshed.get("access_token") or "").strip()
        if not new_access_token:
            return False, {"refresh": refreshed}, "刷新成功但没有返回 access_token"
        ok, payload, error_message = fetch_sub2api_groups_by_token(base_url, new_access_token)
        if isinstance(payload, dict):
            payload["refreshed_auth"] = {
                "access_token": new_access_token,
                "refresh_token": str(refreshed.get("refresh_token") or refresh_token).strip(),
                "expires_in": refreshed.get("expires_in"),
            }
        return ok, payload, error_message

    login_ok, token, login_payload, login_error = sub2api_login(base_url, username, password)
    if not login_ok:
        return False, {"login": login_payload}, login_error or "登录失败"
    return fetch_sub2api_groups_by_token(base_url, token)


def fetch_newapi_groups_with_access_token(base_url: str, access_token: str, user_id: str = "") -> Tuple[bool, Dict[str, Any], Optional[str]]:
    token = (access_token or "").strip()
    if not token:
        return False, {}, "访问令牌为空"

    headers = {
        "Accept": "application/json",
        "User-Agent": "Upstream-Ratio-Watch/1.0",
        "Authorization": token.removeprefix("Bearer ").removeprefix("bearer ").strip(),
    }
    if str(user_id or "").strip():
        headers["New-Api-User"] = str(user_id).strip()
    errors: List[str] = []
    for path in ("/api/user/self/groups", "/api/user/groups"):
        url = f"{normalize_base_url(base_url)}{path}"
        req = urllib.request.Request(url, headers=headers, method="GET")
        try:
            with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT_SECONDS) as resp:
                body = resp.read().decode("utf-8", errors="replace")
                payload = json.loads(body)
                if isinstance(payload, dict) and payload.get("success"):
                    return True, payload, None
                message = payload.get("message") if isinstance(payload, dict) else None
                errors.append(f"{path}: {message or 'success=false'}")
        except urllib.error.HTTPError as exc:
            errors.append(f"{path}: HTTP {exc.code}")
        except Exception as exc:
            errors.append(f"{path}: {exc}")

    return False, {"errors": errors}, "访问令牌分组采集失败：" + "；".join(errors)


def probe_newapi_groups(base_url: str) -> Dict[str, Any]:
    ok, payload, error_message = fetch_newapi_groups(base_url)
    if not ok:
        return {
            "success": False,
            "message": error_message or "request failed",
            "groups_count": 0,
            "groups": {},
            "raw": payload,
        }

    groups = parse_groups_payload(payload)
    return {
        "success": True,
        "message": "ok",
        "groups_count": len(groups),
        "groups": groups,
    }


def probe_sub2api_groups(
    base_url: str,
    username: str = "",
    password: str = "",
    auth_mode: str = "password",
    access_token: str = "",
    refresh_token: str = "",
) -> Dict[str, Any]:
    ok, payload, error_message = fetch_sub2api_user_groups(
        base_url,
        username=username,
        password=password,
        auth_mode=auth_mode,
        access_token=access_token,
        refresh_token=refresh_token,
    )
    if not ok:
        return {
            "success": False,
            "message": error_message or "request failed",
            "groups_count": 0,
            "groups": {},
            "raw": payload,
        }
    groups = parse_sub2api_groups(payload.get("data"), payload.get("user_rates"))
    return {
        "success": True,
        "message": "ok",
        "groups_count": len(groups),
        "groups": groups,
    }


def get_last_success_snapshot(site_id: int) -> Optional[Dict[str, Any]]:
    return db_query_one(
        """
        SELECT * FROM snapshots
        WHERE site_id = ? AND status = 'success'
        ORDER BY id DESC
        LIMIT 1
        """,
        (site_id,),
    )


def diff_groups(old_groups: Dict[str, Dict[str, Any]], new_groups: Dict[str, Dict[str, Any]]) -> List[Dict[str, Any]]:
    changes: List[Dict[str, Any]] = []
    old_names = set(old_groups.keys())
    new_names = set(new_groups.keys())

    for name in sorted(new_names - old_names):
        changes.append({
            "change_type": "group_added",
            "group_name": name,
            "old_value": None,
            "new_value": new_groups[name],
            "change_percent": None,
            "message": f"新增分组 {name}",
        })

    for name in sorted(old_names - new_names):
        changes.append({
            "change_type": "group_removed",
            "group_name": name,
            "old_value": old_groups[name],
            "new_value": None,
            "change_percent": None,
            "message": f"删除分组 {name}",
        })

    for name in sorted(old_names & new_names):
        old_item = old_groups[name]
        new_item = new_groups[name]
        if old_item.get("ratio") != new_item.get("ratio"):
            old_ratio = old_item.get("ratio")
            new_ratio = new_item.get("ratio")
            change_percent = None
            if isinstance(old_ratio, (int, float)) and isinstance(new_ratio, (int, float)) and old_ratio != 0:
                change_percent = round((float(new_ratio) - float(old_ratio)) / float(old_ratio) * 100, 2)

            if isinstance(old_ratio, (int, float)) and isinstance(new_ratio, (int, float)):
                message = f"{name} 倍率 {old_ratio} -> {new_ratio}"
            else:
                message = f"{name} 倍率 {old_ratio} -> {new_ratio}"

            changes.append({
                "change_type": "ratio_changed",
                "group_name": name,
                "old_value": old_item,
                "new_value": new_item,
                "change_percent": change_percent,
                "message": message,
            })

        if old_item.get("desc") != new_item.get("desc"):
            changes.append({
                "change_type": "desc_changed",
                "group_name": name,
                "old_value": old_item.get("desc"),
                "new_value": new_item.get("desc"),
                "change_percent": None,
                "message": f"{name} 描述变化",
            })
        for field, label in (
            ("status", "状态"),
            ("is_exclusive", "专属分组"),
            ("subscription_type", "订阅类型"),
            ("rpm_limit", "RPM 限制"),
            ("platform", "平台"),
        ):
            if field in old_item or field in new_item:
                if old_item.get(field) != new_item.get(field):
                    changes.append({
                        "change_type": f"{field}_changed",
                        "group_name": name,
                        "old_value": old_item.get(field),
                        "new_value": new_item.get(field),
                        "change_percent": None,
                        "message": f"{name} {label}变化：{old_item.get(field)} -> {new_item.get(field)}",
                    })

    return changes


def get_notification_settings() -> Dict[str, Any]:
    row = db_query_one("SELECT * FROM notification_settings WHERE id = 1")
    if row:
        return row
    now = utc_now_iso()
    db_execute(
        """
        INSERT OR IGNORE INTO notification_settings
        (id, qq_enabled, qq_app_id, qq_client_secret, qq_group_openid, email_enabled, smtp_host, smtp_port, smtp_username, smtp_password, smtp_use_ssl, smtp_from, smtp_to, created_at, updated_at)
        VALUES (1, 0, '', '', '', 0, '', 465, '', '', 1, '', '', ?, ?)
        """,
        (now, now),
    )
    return db_query_one("SELECT * FROM notification_settings WHERE id = 1") or {}


def notification_settings_payload() -> Dict[str, Any]:
    settings = get_notification_settings()
    return {
        "wecom_enabled": bool(settings.get("wecom_enabled")),
        "wecom_webhook": settings.get("wecom_webhook") or "",
        "wecom_has_webhook": bool(settings.get("wecom_webhook")),
        "wecom_last_error": settings.get("wecom_last_error"),
        "wecom_last_sent_at": settings.get("wecom_last_sent_at"),
        "email_enabled": bool(settings.get("email_enabled")),
        "smtp_host": settings.get("smtp_host") or "",
        "smtp_port": int(settings.get("smtp_port") or 465),
        "smtp_username": settings.get("smtp_username") or "",
        "has_smtp_password": bool(settings.get("smtp_password")),
        "smtp_use_ssl": bool(settings.get("smtp_use_ssl")),
        "smtp_from": settings.get("smtp_from") or "",
        "smtp_to": settings.get("smtp_to") or "",
        "email_last_error": settings.get("email_last_error"),
        "email_last_sent_at": settings.get("email_last_sent_at"),
        "updated_at": settings.get("updated_at"),
    }


def update_notification_settings(body: Dict[str, Any]) -> None:
    settings = get_notification_settings()
    wecom_enabled = bool(body.get("wecom_enabled", False))
    wecom_webhook = str(body.get("wecom_webhook") or "").strip()
    email_enabled = bool(body.get("email_enabled", False))
    smtp_host = str(body.get("smtp_host") or "").strip()
    smtp_port = int(body.get("smtp_port") or 465)
    smtp_username = str(body.get("smtp_username") or "").strip()
    smtp_password = str(body.get("smtp_password") or "")
    smtp_use_ssl = bool(body.get("smtp_use_ssl", True))
    smtp_from = str(body.get("smtp_from") or "").strip()
    smtp_to = str(body.get("smtp_to") or "").strip()

    if email_enabled:
        if not smtp_host or not smtp_port or not smtp_username or not (smtp_password or settings.get("smtp_password")) or not smtp_to:
            raise ValueError("启用邮箱推送时需要填写 SMTP 服务器、端口、账号、密码和收件人")
        if not smtp_from:
            smtp_from = smtp_username
    if wecom_enabled and not (wecom_webhook or settings.get("wecom_webhook")):
        raise ValueError("启用企业微信推送时需要填写 Webhook 地址")

    fields = [
        "qq_enabled = 0",
        "wecom_enabled = ?",
        "email_enabled = ?",
        "wecom_webhook = ?",
        "smtp_host = ?",
        "smtp_port = ?",
        "smtp_username = ?",
        "smtp_use_ssl = ?",
        "smtp_from = ?",
        "smtp_to = ?",
        "updated_at = ?",
    ]
    params: List[Any] = [
        1 if wecom_enabled else 0,
        1 if email_enabled else 0,
        wecom_webhook if wecom_webhook else (settings.get("wecom_webhook") or ""),
        smtp_host,
        smtp_port,
        smtp_username,
        1 if smtp_use_ssl else 0,
        smtp_from,
        smtp_to,
        utc_now_iso(),
    ]
    if smtp_password:
        fields.append("smtp_password = ?")
        params.append(smtp_password)
    params.append(1)
    db_execute(f"UPDATE notification_settings SET {', '.join(fields)} WHERE id = ?", params)


def log_notification(channel: str, status: str, target: str, message: str, error_message: Optional[str] = None) -> None:
    db_execute(
        """
        INSERT INTO notification_logs (channel, status, target, message, error_message, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (channel, status, target, message, error_message, utc_now_iso()),
    )


def send_email_message(subject: str, message: str) -> Tuple[bool, Optional[str]]:
    settings = get_notification_settings()
    if not settings.get("email_enabled"):
        return True, "邮箱推送未启用，未发送测试邮件"

    smtp_host = str(settings.get("smtp_host") or "").strip()
    smtp_port = int(settings.get("smtp_port") or 465)
    smtp_username = str(settings.get("smtp_username") or "").strip()
    smtp_password = str(settings.get("smtp_password") or "")
    smtp_from = str(settings.get("smtp_from") or smtp_username).strip()
    smtp_to = str(settings.get("smtp_to") or "").strip()
    smtp_use_ssl = bool(settings.get("smtp_use_ssl"))
    if not smtp_host or not smtp_port or not smtp_username or not smtp_password or not smtp_to:
        return False, "邮箱 SMTP 配置不完整"

    recipients = [item.strip() for item in smtp_to.replace("，", ",").split(",") if item.strip()]
    email = EmailMessage()
    email["Subject"] = subject
    email["From"] = smtp_from
    email["To"] = ", ".join(recipients)
    email.set_content(message)

    try:
        if smtp_use_ssl:
            with smtplib.SMTP_SSL(smtp_host, smtp_port, timeout=HTTP_TIMEOUT_SECONDS) as smtp:
                smtp.login(smtp_username, smtp_password)
                smtp.send_message(email)
        else:
            with smtplib.SMTP(smtp_host, smtp_port, timeout=HTTP_TIMEOUT_SECONDS) as smtp:
                smtp.starttls()
                smtp.login(smtp_username, smtp_password)
                smtp.send_message(email)
    except Exception as exc:
        error = f"邮箱推送失败：{exc}"
        db_execute(
            "UPDATE notification_settings SET email_last_error = ?, updated_at = ? WHERE id = 1",
            (error, utc_now_iso()),
        )
        log_notification("email", "failed", smtp_to, message, error)
        return False, error

    sent_at = utc_now_iso()
    db_execute(
        """
        UPDATE notification_settings
        SET email_last_error = NULL, email_last_sent_at = ?, updated_at = ?
        WHERE id = 1
        """,
        (sent_at, sent_at),
    )
    log_notification("email", "success", smtp_to, message, None)
    return True, None


def send_wecom_message(subject: str, message: str) -> Tuple[bool, Optional[str]]:
    settings = get_notification_settings()
    if not settings.get("wecom_enabled"):
        return True, "企业微信推送未启用，未发送消息"

    webhook = str(settings.get("wecom_webhook") or "").strip()
    if not webhook:
        return False, "企业微信 Webhook 未配置"

    content = f"**{subject}**\n\n{message}"
    ok, payload, error = request_json(
        webhook,
        payload={
            "msgtype": "markdown",
            "markdown": {
                "content": content,
            },
        },
        method="POST",
    )
    if not ok:
        error_text = error or "企业微信推送失败"
        db_execute(
            "UPDATE notification_settings SET wecom_last_error = ?, updated_at = ? WHERE id = 1",
            (error_text, utc_now_iso()),
        )
        log_notification("wecom", "failed", webhook, message, error_text)
        return False, error_text

    if isinstance(payload, dict) and payload.get("errcode") not in (None, 0):
        error_text = f"企业微信推送失败：{payload.get('errmsg') or payload.get('errcode')}"
        db_execute(
            "UPDATE notification_settings SET wecom_last_error = ?, updated_at = ? WHERE id = 1",
            (error_text, utc_now_iso()),
        )
        log_notification("wecom", "failed", webhook, message, error_text)
        return False, error_text

    sent_at = utc_now_iso()
    db_execute(
        """
        UPDATE notification_settings
        SET wecom_last_error = NULL, wecom_last_sent_at = ?, updated_at = ?
        WHERE id = 1
        """,
        (sent_at, sent_at),
    )
    log_notification("wecom", "success", webhook, message, None)
    return True, None


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


def percent_text(change: Dict[str, Any]) -> str:
    percent = change.get("change_percent")
    if isinstance(percent, (int, float)):
        return f"{abs(percent):.2f}".rstrip("0").rstrip(".") + "%"
    return ""


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


def platform_label(site: Dict[str, Any]) -> str:
    return "sub2api" if (site.get("platform") or "newapi") == "sub2api" else "NewAPI"


def format_change_subject(site: Dict[str, Any], changes: List[Dict[str, Any]]) -> str:
    site_name = site["name"]
    platform = platform_label(site)
    ratio_changes = [item for item in changes if item.get("change_type") == "ratio_changed"]
    if len(ratio_changes) == 1:
        change = ratio_changes[0]
        label = "倍率上涨" if ratio_direction(change) == "up" else "倍率下降" if ratio_direction(change) == "down" else "倍率变动"
        return f"【{platform} {label}】{site_name} / {change.get('group_name') or '-'}：{format_change_value(change.get('old_value'))} -> {format_change_value(change.get('new_value'))}"
    if len(ratio_changes) > 1:
        return f"【{platform} 倍率变动】{site_name}：{len(ratio_changes)} 个分组有变化"

    added = [item for item in changes if item.get("change_type") == "group_added"]
    removed = [item for item in changes if item.get("change_type") == "group_removed"]
    if len(added) == 1 and not removed:
        change = added[0]
        return f"【{platform} 新增分组】{site_name} / {change.get('group_name') or '-'}：{format_change_value(change.get('new_value'))}"
    if len(removed) == 1 and not added:
        change = removed[0]
        return f"【{platform} 删除分组】{site_name} / {change.get('group_name') or '-'}"
    return f"【{platform} 分组变化】{site_name}：{len(changes)} 条变化"


def format_change_notification(site: Dict[str, Any], changes: List[Dict[str, Any]], checked_at: str) -> str:
    up_changes = [item for item in changes if item.get("change_type") == "ratio_changed" and ratio_direction(item) == "up"]
    down_changes = [item for item in changes if item.get("change_type") == "ratio_changed" and ratio_direction(item) == "down"]
    changed_ratio = [item for item in changes if item.get("change_type") == "ratio_changed" and ratio_direction(item) == "changed"]
    added = [item for item in changes if item.get("change_type") == "group_added"]
    removed = [item for item in changes if item.get("change_type") == "group_removed"]
    desc_changed = [item for item in changes if item.get("change_type") == "desc_changed"]
    other_changed = [
        item for item in changes
        if item.get("change_type") not in {"ratio_changed", "group_added", "group_removed", "desc_changed"}
    ]

    lines = [
        "上游倍率监控提醒",
        f"站点：{site['name']}",
        f"平台：{platform_label(site)}",
        f"时间：{fmt_local_time_for_message(checked_at)}",
        f"本次共 {len(changes)} 条变化",
    ]

    def append_ratio_block(title: str, items: List[Dict[str, Any]], suffix: str) -> None:
        if not items:
            return
        lines.extend(["", title])
        for change in items[:6]:
            percent = percent_text(change)
            extra = f"，{suffix} {percent}" if percent else f"，{suffix}"
            lines.append(
                f"- {change.get('group_name') or '-'}：{format_change_value(change.get('old_value'))} -> {format_change_value(change.get('new_value'))}{extra}"
            )

    append_ratio_block("涨价了，钱包先别眨眼：", up_changes, "上涨")
    append_ratio_block("降价了，这波可以多看两眼：", down_changes, "下降")

    if changed_ratio:
        lines.extend(["", "倍率变了，但方向不太好判断："])
        for change in changed_ratio[:6]:
            lines.append(f"- {change.get('group_name') or '-'}：{format_change_value(change.get('old_value'))} -> {format_change_value(change.get('new_value'))}")

    if added:
        lines.extend(["", "新分组上线："])
        for change in added[:6]:
            lines.append(f"- {change.get('group_name') or '-'}：{format_change_value(change.get('new_value'))}")

    if removed:
        lines.extend(["", "分组下线了："])
        for change in removed[:6]:
            lines.append(f"- {change.get('group_name') or '-'}：原倍率 {format_change_value(change.get('old_value'))}")

    if desc_changed:
        lines.extend(["", "描述有变化："])
        for change in desc_changed[:6]:
            lines.append(f"- {change.get('group_name') or '-'}")

    if other_changed:
        lines.extend(["", "其他配置变化："])
        for change in other_changed[:8]:
            lines.append(
                f"- {change.get('group_name') or '-'}：{format_change_value(change.get('old_value'))} -> {format_change_value(change.get('new_value'))}"
            )

    if len(changes) > 8:
        lines.append("")
        lines.append(f"其余 {len(changes) - 8} 条变化请在面板查看")
    return "\n".join(lines)


def notify_changes(site: Dict[str, Any], changes: List[Dict[str, Any]], checked_at: str) -> None:
    if not changes:
        return
    subject = format_change_subject(site, changes)
    message = format_change_notification(site, changes, checked_at)
    send_email_message(subject, message)
    send_wecom_message(subject, message)


def collect_site_groups(site: Dict[str, Any]) -> Tuple[bool, Dict[str, Dict[str, Any]], Dict[str, Any], str, Optional[str]]:
    platform = site.get("platform") or "newapi"
    if platform == "sub2api":
        ok, payload, error_message = fetch_sub2api_user_groups(
            site["base_url"],
            username=site.get("login_username") or "",
            password=site.get("login_password") or "",
            auth_mode=site.get("auth_mode") or "password",
            access_token=site.get("access_token") or "",
            refresh_token=site.get("refresh_token") or "",
        )
        groups = parse_sub2api_groups(payload.get("data"), payload.get("user_rates")) if ok else {}
        return ok, groups, payload, "/api/v1/groups/available", error_message

    ok, payload, error_message = fetch_newapi_groups(site["base_url"])
    groups = parse_groups_payload(payload) if ok else {}
    return ok, groups, payload, "/api/user/groups", error_message


def detect_site(site_id: int) -> Dict[str, Any]:
    site = db_query_one("SELECT * FROM sites WHERE id = ?", (site_id,))
    if not site:
        return {"success": False, "message": "site not found"}

    checked_at = utc_now_iso()
    ok, new_groups, payload, source, error_message = collect_site_groups(site)
    latest_success = get_last_success_snapshot(site_id)

    if not ok:
        db_execute(
            """
            INSERT INTO snapshots (site_id, status, source, raw_json, error_message, checked_at, hash)
            VALUES (?, 'failed', ?, ?, ?, ?, NULL)
            """,
            (site_id, source, json.dumps(payload, ensure_ascii=False), error_message, checked_at),
        )

        consecutive_failures = int(site["consecutive_failures"] or 0) + 1
        status = "failed" if consecutive_failures >= 3 else "warning"
        next_check_at = next_check_iso(int(site["interval_minutes"] or DEFAULT_INTERVAL_MINUTES))
        db_execute(
            """
            UPDATE sites
            SET status = ?, last_error = ?, last_check_at = ?, next_check_at = ?, consecutive_failures = ?, updated_at = ?
            WHERE id = ?
            """,
            (status, error_message, checked_at, next_check_at, consecutive_failures, checked_at, site_id),
        )
        return {"success": False, "message": error_message, "status": status}

    groups_json = json.dumps(new_groups, ensure_ascii=False, sort_keys=True)
    hash_value = stable_hash(new_groups)
    login_groups: Dict[str, Dict[str, Any]] = {}
    login_groups_json: Optional[str] = None
    login_error: Optional[str] = None
    refreshed_auth = payload.get("refreshed_auth") if isinstance(payload, dict) else None
    if refreshed_auth and isinstance(payload, dict):
        payload = dict(payload)
        payload.pop("refreshed_auth", None)

    db_execute(
        """
        INSERT INTO snapshots (site_id, status, source, groups_json, raw_json, hash, error_message, checked_at)
        VALUES (?, 'success', ?, ?, ?, ?, NULL, ?)
        """,
        (site_id, source, groups_json, json.dumps(payload, ensure_ascii=False), hash_value, checked_at),
    )

    changes: List[Dict[str, Any]] = []
    if latest_success and latest_success.get("groups_json"):
        try:
            old_groups = json.loads(latest_success["groups_json"])
            if isinstance(old_groups, dict):
                changes = diff_groups(old_groups, new_groups)
        except Exception:
            changes = []

    if (site.get("platform") or "newapi") == "newapi" and site.get("login_enabled") and site.get("access_token") and site.get("access_user_id"):
        login_ok, login_payload, login_error_message = fetch_newapi_groups_with_access_token(
            site["base_url"],
            site["access_token"],
            site.get("access_user_id") or "",
        )
        if login_ok:
            login_groups = parse_groups_payload(login_payload)
            login_groups_json = json.dumps(login_groups, ensure_ascii=False, sort_keys=True)
            old_login_groups = {}
            if site.get("current_login_groups_json"):
                try:
                    parsed_old_login = json.loads(site["current_login_groups_json"])
                    if isinstance(parsed_old_login, dict):
                        old_login_groups = parsed_old_login
                except Exception:
                    old_login_groups = {}
            login_changes = diff_groups(old_login_groups, login_groups) if old_login_groups else []
            for change in login_changes:
                change["message"] = f"认证增强 {change['message']}"
            changes.extend(login_changes)
        else:
            login_error = login_error_message or "认证增强采集失败"

    for change in changes:
        severity = "info"
        if change["change_type"] in {"group_removed"}:
            severity = "critical"
        elif change["change_type"] == "ratio_changed":
            percent = change.get("change_percent")
            if isinstance(percent, (int, float)) and percent > 0:
                severity = "warning"

        db_execute(
            """
            INSERT INTO changes
            (site_id, change_type, group_name, old_value, new_value, change_percent, message, created_at, acknowledged)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0)
            """,
            (
                site_id,
                change["change_type"],
                change.get("group_name"),
                json.dumps(change.get("old_value"), ensure_ascii=False) if change.get("old_value") is not None else None,
                json.dumps(change.get("new_value"), ensure_ascii=False) if change.get("new_value") is not None else None,
                change.get("change_percent"),
                change["message"],
                checked_at,
            ),
        )
        change["severity"] = severity

    notify_changes(site, changes, checked_at)

    next_check_at = next_check_iso(int(site["interval_minutes"] or DEFAULT_INTERVAL_MINUTES))
    effective_status = "warning" if login_error else "ok"
    refreshed_access_token = ""
    refreshed_refresh_token = ""
    refreshed_expires_at = None
    if isinstance(refreshed_auth, dict):
        refreshed_access_token = str(refreshed_auth.get("access_token") or "").strip()
        refreshed_refresh_token = str(refreshed_auth.get("refresh_token") or "").strip()
        expires_in = refreshed_auth.get("expires_in")
        try:
            refreshed_expires_at = (
                app_now() + timedelta(seconds=int(expires_in))
            ).isoformat(timespec="seconds") if expires_in is not None else None
        except (TypeError, ValueError):
            refreshed_expires_at = None
    db_execute(
        """
        UPDATE sites
        SET status = ?,
            last_error = NULL,
            last_check_at = ?,
            next_check_at = ?,
            consecutive_failures = 0,
            current_groups_json = ?,
            current_login_groups_json = COALESCE(?, current_login_groups_json),
            login_last_error = ?,
            login_last_check_at = ?,
            access_token = COALESCE(NULLIF(?, ''), access_token),
            refresh_token = COALESCE(NULLIF(?, ''), refresh_token),
            token_expires_at = COALESCE(?, token_expires_at),
            updated_at = ?
        WHERE id = ?
        """,
        (
            effective_status,
            checked_at,
            next_check_at,
            groups_json,
            login_groups_json,
            login_error,
            checked_at if site.get("login_enabled") else None,
            refreshed_access_token,
            refreshed_refresh_token,
            refreshed_expires_at,
            checked_at,
            site_id,
        ),
    )
    schedule_model_cache_refresh(site_id)

    return {
        "success": not bool(login_error),
        "message": login_error or "ok",
        "checked_at": checked_at,
        "groups": new_groups,
        "login_groups": login_groups,
        "changes": changes,
    }


def schedule_worker() -> None:
    while not STOP_EVENT.is_set():
        try:
            now = app_now()
            due_sites = db_query_all(
                """
                SELECT * FROM sites
                WHERE enabled = 1
                  AND (next_check_at IS NULL OR next_check_at <= ?)
                ORDER BY
                  CASE WHEN next_check_at IS NULL THEN 0 ELSE 1 END,
                  next_check_at ASC,
                  id ASC
                """,
                (now.isoformat(timespec="seconds"),),
            )
            for site in due_sites:
                if STOP_EVENT.is_set():
                    break
                try:
                    detect_site(int(site["id"]))
                except Exception:
                    checked_at = utc_now_iso()
                    err = traceback.format_exc(limit=2)
                    consecutive_failures = int(site["consecutive_failures"] or 0) + 1
                    next_check_at = next_check_iso(int(site["interval_minutes"] or DEFAULT_INTERVAL_MINUTES))
                    db_execute(
                        """
                        UPDATE sites
                        SET status = ?,
                            last_error = ?,
                            last_check_at = ?,
                            next_check_at = ?,
                            consecutive_failures = ?,
                            updated_at = ?
                        WHERE id = ?
                        """,
                        (
                            "failed" if consecutive_failures >= 3 else "warning",
                            err,
                            checked_at,
                            next_check_at,
                            consecutive_failures,
                            checked_at,
                            site["id"],
                        ),
                    )
        except Exception:
            pass
        STOP_EVENT.wait(SCAN_INTERVAL_SECONDS)


def json_response(handler: BaseHTTPRequestHandler, payload: Any, status: int = 200) -> None:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.send_header("Cache-Control", "no-store")
    handler.end_headers()
    handler.wfile.write(body)


def read_json_body(handler: BaseHTTPRequestHandler) -> Any:
    length = int(handler.headers.get("Content-Length", "0") or "0")
    raw = handler.rfile.read(length).decode("utf-8") if length > 0 else "{}"
    return json.loads(raw or "{}")


def site_summary(site: Dict[str, Any]) -> Dict[str, Any]:
    groups = {}
    login_groups = {}
    if site.get("current_groups_json"):
        try:
            groups = json.loads(site["current_groups_json"]) or {}
        except Exception:
            groups = {}
    if site.get("current_login_groups_json"):
        try:
            login_groups = json.loads(site["current_login_groups_json"]) or {}
        except Exception:
            login_groups = {}
    latest_snapshot = db_query_one(
        "SELECT checked_at, status, error_message FROM snapshots WHERE site_id = ? ORDER BY id DESC LIMIT 1",
        (site["id"],),
    )
    latest_change = db_query_one(
        "SELECT * FROM changes WHERE site_id = ? ORDER BY id DESC LIMIT 1",
        (site["id"],),
    )
    return {
        "id": site["id"],
        "name": site["name"],
        "base_url": site["base_url"],
        "platform": site["platform"],
        "platform_label": "sub2api" if site["platform"] == "sub2api" else "NewAPI",
        "enabled": bool(site["enabled"]),
        "interval_minutes": site["interval_minutes"],
        "login_enabled": bool(site.get("login_enabled")),
        "auth_mode": site.get("auth_mode") or "password",
        "login_username": site.get("login_username") or "",
        "has_login_password": bool(site.get("login_password")),
        "has_access_token": bool(site.get("access_token")),
        "has_refresh_token": bool(site.get("refresh_token")),
        "token_expires_at": site.get("token_expires_at") or "",
        "access_user_id": site.get("access_user_id") or "",
        "login_last_error": site.get("login_last_error"),
        "login_last_check_at": site.get("login_last_check_at"),
        "status": site["status"],
        "last_error": site["last_error"],
        "last_check_at": site["last_check_at"],
        "next_check_at": site["next_check_at"],
        "consecutive_failures": site["consecutive_failures"],
        "current_groups": groups,
        "current_groups_count": len(groups) if isinstance(groups, dict) else 0,
        "current_login_groups": login_groups,
        "current_login_groups_count": len(login_groups) if isinstance(login_groups, dict) else 0,
        "latest_snapshot": latest_snapshot,
        "latest_change": latest_change,
    }


def overview_payload() -> Dict[str, Any]:
    sites = db_query_all("SELECT * FROM sites ORDER BY id DESC")
    changes = db_query_all("SELECT * FROM changes ORDER BY id DESC LIMIT 8")
    totals = {
        "sites_total": len(sites),
        "sites_enabled": sum(1 for s in sites if s["enabled"]),
        "sites_ok": sum(1 for s in sites if s["status"] == "ok"),
        "sites_failed": sum(1 for s in sites if s["status"] in {"failed", "warning"}),
        "changes_today": db_query_one(
            "SELECT COUNT(*) AS count FROM changes WHERE created_at >= ?",
            (app_now().replace(hour=0, minute=0, second=0, microsecond=0).isoformat(timespec="seconds"),),
        ) or {"count": 0},
    }
    return {
        "stats": {
            "sites_total": totals["sites_total"],
            "sites_enabled": totals["sites_enabled"],
            "sites_ok": totals["sites_ok"],
            "sites_failed": totals["sites_failed"],
            "changes_today": totals["changes_today"]["count"],
        },
        "sites": [site_summary(site) for site in sites],
        "changes": changes,
    }


def list_sites_payload() -> List[Dict[str, Any]]:
    sites = db_query_all("SELECT * FROM sites ORDER BY id DESC")
    return [site_summary(site) for site in sites]


def list_snapshots(site_id: int) -> List[Dict[str, Any]]:
    return db_query_all(
        """
        SELECT * FROM snapshots
        WHERE site_id = ?
        ORDER BY id DESC
        LIMIT 100
        """,
        (site_id,),
    )


def list_changes(limit: int = 100) -> List[Dict[str, Any]]:
    return db_query_all(
        "SELECT * FROM changes ORDER BY id DESC LIMIT ?",
        (limit,),
    )


def list_site_changes(site_id: int, limit: int = 100) -> List[Dict[str, Any]]:
    return db_query_all(
        """
        SELECT * FROM changes
        WHERE site_id = ?
        ORDER BY id DESC
        LIMIT ?
        """,
        (site_id, limit),
    )


def site_groups_from_row(site: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    if not site.get("current_groups_json"):
        return {}
    try:
        groups = json.loads(site["current_groups_json"])
        return groups if isinstance(groups, dict) else {}
    except Exception:
        return {}


def persist_sub2api_refreshed_auth(site_id: int, refreshed_auth: Any) -> None:
    if not isinstance(refreshed_auth, dict):
        return
    expires_at = None
    try:
        expires_in = refreshed_auth.get("expires_in")
        expires_at = (app_now() + timedelta(seconds=int(expires_in))).isoformat(timespec="seconds") if expires_in is not None else None
    except (TypeError, ValueError):
        expires_at = None
    db_execute(
        """
        UPDATE sites
        SET access_token = COALESCE(NULLIF(?, ''), access_token),
            refresh_token = COALESCE(NULLIF(?, ''), refresh_token),
            token_expires_at = COALESCE(?, token_expires_at),
            updated_at = ?
        WHERE id = ?
        """,
        (
            str(refreshed_auth.get("access_token") or "").strip(),
            str(refreshed_auth.get("refresh_token") or "").strip(),
            expires_at,
            utc_now_iso(),
            site_id,
        ),
    )


def build_site_models_payload(site: Dict[str, Any]) -> Tuple[int, Dict[str, Any]]:
    groups = site_groups_from_row(site)
    if not groups:
        return 409, {"success": False, "message": "请先检测站点，获取分组倍率后再查看模型"}

    platform = site.get("platform") or "newapi"
    if platform == "newapi":
        ok, payload, error_message = fetch_newapi_model_data(
            site["base_url"],
            access_token=site.get("access_token") or "",
            user_id=site.get("access_user_id") or "",
        )
        if not ok:
            return 502, {"success": False, "message": error_message or "读取 NewAPI 模型失败"}
        models_by_group = parse_newapi_models_by_group(payload.get("pricing"), payload.get("uptime"), groups)
        pricing_data = payload.get("pricing", {}).get("data", []) if isinstance(payload.get("pricing"), dict) else []
        uptime_categories = payload.get("uptime", {}).get("data", []) if isinstance(payload.get("uptime"), dict) else []
        uptime_monitors_count = sum(
            len(category.get("monitors") or [])
            for category in uptime_categories
            if isinstance(category, dict)
        )
        return 200, {
            "success": True,
            "source": ["/api/pricing", "/api/uptime/status"],
            "fetched_at": utc_now_iso(),
            "models_by_group": models_by_group,
            "models_count": len(pricing_data),
            "monitors_count": uptime_monitors_count,
            "uptime_error": payload.get("uptime_error"),
        }

    ok, payload, error_message = fetch_sub2api_model_data(
        site["base_url"],
        username=site.get("login_username") or "",
        password=site.get("login_password") or "",
        auth_mode=site.get("auth_mode") or "password",
        access_token=site.get("access_token") or "",
        refresh_token=site.get("refresh_token") or "",
    )
    persist_sub2api_refreshed_auth(int(site["id"]), payload.get("refreshed_auth") if isinstance(payload, dict) else None)
    if not ok:
        return 502, {"success": False, "message": error_message or "读取上游模型失败"}

    configured_models = parse_sub2api_channel_models(payload.get("channels"), groups)
    monitored_models, unmatched_models = parse_sub2api_monitor_models(payload.get("monitors"), groups)
    models_by_group = merge_sub2api_group_models(configured_models, monitored_models)
    monitor_items = payload.get("monitors", {}).get("items", []) if isinstance(payload.get("monitors"), dict) else []
    return 200, {
        "success": True,
        "source": ["/api/v1/channels/available", "/api/v1/channel-monitors"],
        "fetched_at": utc_now_iso(),
        "models_by_group": models_by_group,
        "channels_count": len(payload.get("channels") or []),
        "monitors_count": len(monitor_items),
        "unmatched_models_count": len(unmatched_models),
        "channels_error": payload.get("channels_error"),
        "monitors_error": payload.get("monitors_error"),
    }


def invalidate_site_model_cache(site_id: int) -> None:
    with MODEL_CACHE_LOCK:
        MODEL_DATA_CACHE.pop(site_id, None)


def cache_site_model_payload(site_id: int, payload: Dict[str, Any]) -> None:
    with MODEL_CACHE_LOCK:
        MODEL_DATA_CACHE[site_id] = {
            "payload": json.loads(json.dumps(payload, ensure_ascii=False)),
            "updated_monotonic": time.monotonic(),
        }


def get_site_model_cache(site_id: int) -> Tuple[Optional[Dict[str, Any]], float]:
    with MODEL_CACHE_LOCK:
        entry = MODEL_DATA_CACHE.get(site_id)
        if not entry or not isinstance(entry.get("payload"), dict):
            return None, float("inf")
        age = time.monotonic() - float(entry.get("updated_monotonic") or 0)
        return json.loads(json.dumps(entry["payload"], ensure_ascii=False)), age


def refresh_site_model_cache(site_id: int) -> Tuple[int, Dict[str, Any]]:
    site = db_query_one("SELECT * FROM sites WHERE id = ?", (site_id,))
    if not site:
        return 404, {"success": False, "message": "site not found"}
    status, payload = build_site_models_payload(site)
    if status == 200 and payload.get("success"):
        cache_site_model_payload(site_id, payload)
    return status, payload


def model_cache_refresh_worker(site_id: int) -> None:
    try:
        refresh_site_model_cache(site_id)
    finally:
        with MODEL_CACHE_LOCK:
            MODEL_CACHE_REFRESHING.discard(site_id)


def schedule_model_cache_refresh(site_id: int) -> None:
    with MODEL_CACHE_LOCK:
        if site_id in MODEL_CACHE_REFRESHING:
            return
        MODEL_CACHE_REFRESHING.add(site_id)
    threading.Thread(target=model_cache_refresh_worker, args=(site_id,), daemon=True).start()


def warm_model_cache() -> None:
    for site in db_query_all("SELECT id FROM sites WHERE enabled = 1 ORDER BY id"):
        schedule_model_cache_refresh(int(site["id"]))


class Handler(BaseHTTPRequestHandler):
    server_version = "NewAPIPriceWatch/0.1"

    def log_message(self, fmt: str, *args: Any) -> None:
        return

    def _serve_file(self, path: Path, content_type: str) -> None:
        if not path.exists():
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        data = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _content_type_for(self, path: Path) -> str:
        suffix = path.suffix.lower()
        return {
            ".html": "text/html; charset=utf-8",
            ".js": "application/javascript; charset=utf-8",
            ".css": "text/css; charset=utf-8",
            ".json": "application/json; charset=utf-8",
            ".svg": "image/svg+xml",
            ".png": "image/png",
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".webp": "image/webp",
            ".ico": "image/x-icon",
            ".woff": "font/woff",
            ".woff2": "font/woff2",
            ".map": "application/json; charset=utf-8",
        }.get(suffix, "application/octet-stream")

    def _serve_spa(self, request_path: str) -> bool:
        """Serve Vite React build when present; fall back to legacy static/."""
        if WEB_DIST_DIR.exists():
            rel = request_path.lstrip("/") or "index.html"
            candidate = (WEB_DIST_DIR / rel).resolve()
            try:
                candidate.relative_to(WEB_DIST_DIR.resolve())
            except ValueError:
                return False
            if candidate.is_file():
                self._serve_file(candidate, self._content_type_for(candidate))
                return True
            # SPA client routes → index.html
            index = WEB_DIST_DIR / "index.html"
            if index.is_file():
                self._serve_file(index, "text/html; charset=utf-8")
                return True
            return False

        if request_path in {"/", "/index.html"}:
            self._serve_file(STATIC_DIR / "index.html", "text/html; charset=utf-8")
            return True
        if request_path == "/app.js":
            self._serve_file(STATIC_DIR / "app.js", "application/javascript; charset=utf-8")
            return True
        if request_path == "/styles.css":
            self._serve_file(STATIC_DIR / "styles.css", "text/css; charset=utf-8")
            return True
        return False

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path

        if path == "/api/overview":
            return json_response(self, overview_payload())
        if path == "/api/sites":
            return json_response(self, {"data": list_sites_payload()})
        if path == "/api/changes":
            params = parse_qs(parsed.query)
            limit = int(params.get("limit", ["100"])[0] or 100)
            return json_response(self, {"data": list_changes(limit)})
        if path == "/api/notifications/settings":
            return json_response(self, {"data": notification_settings_payload()})
        if path == "/api/notifications/logs":
            return json_response(self, {"data": db_query_all("SELECT * FROM notification_logs ORDER BY id DESC LIMIT 30")})
        if path.startswith("/api/sites/") and path.endswith("/snapshots"):
            try:
                site_id = int(path.split("/")[3])
            except Exception:
                return self.send_error(HTTPStatus.BAD_REQUEST, "invalid site id")
            return json_response(self, {"data": list_snapshots(site_id)})
        if path.startswith("/api/sites/") and path.endswith("/changes"):
            try:
                site_id = int(path.split("/")[3])
            except Exception:
                return self.send_error(HTTPStatus.BAD_REQUEST, "invalid site id")
            params = parse_qs(parsed.query)
            limit = int(params.get("limit", ["100"])[0] or 100)
            return json_response(self, {"data": list_site_changes(site_id, limit)})
        if path.startswith("/api/sites/") and path.endswith("/models"):
            try:
                site_id = int(path.split("/")[3])
            except Exception:
                return json_response(self, {"success": False, "message": "invalid site id"}, 400)
            site = db_query_one("SELECT * FROM sites WHERE id = ?", (site_id,))
            if not site:
                return json_response(self, {"success": False, "message": "site not found"}, 404)
            cached_payload, cache_age = get_site_model_cache(site_id)
            if cached_payload is not None:
                cached_payload["cache_hit"] = True
                cached_payload["cache_age_seconds"] = round(cache_age, 1)
                if cache_age >= MODEL_CACHE_TTL_SECONDS:
                    schedule_model_cache_refresh(site_id)
                    cached_payload["refreshing"] = True
                return json_response(self, cached_payload)

            status, payload = refresh_site_model_cache(site_id)
            payload["cache_hit"] = False
            return json_response(self, payload, status)

        if self._serve_spa(path):
            return

        self.send_error(HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path

        try:
            if path == "/api/check-connection":
                body = read_json_body(self)
                base_url = normalize_base_url(str(body.get("base_url") or ""))
                platform = str(body.get("platform") or "newapi").strip().lower()
                if not base_url:
                    return json_response(self, {"success": False, "message": "base_url required"}, 400)
                if platform == "sub2api":
                    result = probe_sub2api_groups(
                        base_url,
                        username=str(body.get("login_username") or "").strip(),
                        password=str(body.get("login_password") or ""),
                        auth_mode=str(body.get("auth_mode") or "password").strip().lower(),
                        access_token=str(body.get("access_token") or "").strip(),
                        refresh_token=str(body.get("refresh_token") or "").strip(),
                    )
                else:
                    result = probe_newapi_groups(base_url)
                return json_response(self, result)

            if path == "/api/check-login":
                body = read_json_body(self)
                base_url = normalize_base_url(str(body.get("base_url") or ""))
                access_token = str(body.get("access_token") or "").strip()
                access_user_id = str(body.get("access_user_id") or "").strip()
                if not base_url or not access_token or not access_user_id:
                    return json_response(self, {"success": False, "message": "Base URL、系统访问令牌、NewAPI 用户 ID 都需要填写"}, 400)
                groups_ok, groups_payload, groups_error = fetch_newapi_groups_with_access_token(base_url, access_token, access_user_id)
                groups = parse_groups_payload(groups_payload) if groups_ok else {}
                return json_response(self, {
                    "success": groups_ok,
                    "message": groups_error or "访问令牌验证成功",
                    "groups_count": len(groups),
                    "groups": groups,
                })

            if path == "/api/sites":
                body = read_json_body(self)
                name = str(body.get("name") or "").strip()
                base_url = normalize_base_url(str(body.get("base_url") or ""))
                platform = str(body.get("platform") or "newapi").strip().lower()
                enabled = bool(body.get("enabled", True))
                interval = int(body.get("interval_minutes") or DEFAULT_INTERVAL_MINUTES)
                interval = max(MIN_INTERVAL_MINUTES, interval)
                login_enabled = bool(body.get("login_enabled", False))
                login_username = str(body.get("login_username") or "").strip()
                login_password = str(body.get("login_password") or "")
                access_token = str(body.get("access_token") or "").strip()
                access_user_id = str(body.get("access_user_id") or "").strip()
                refresh_token = str(body.get("refresh_token") or "").strip()
                token_expires_at = str(body.get("token_expires_at") or "").strip()
                auth_mode = str(body.get("auth_mode") or "password").strip().lower()
                if platform not in {"newapi", "sub2api"}:
                    return json_response(self, {"success": False, "message": "platform invalid"}, 400)
                if auth_mode not in {"password", "token"}:
                    return json_response(self, {"success": False, "message": "auth_mode invalid"}, 400)
                if not name or not base_url:
                    return json_response(self, {"success": False, "message": "name/base_url required"}, 400)
                if platform == "newapi" and login_enabled and (not access_token or not access_user_id):
                    return json_response(self, {"success": False, "message": "使用系统访问令牌时需要填写 NewAPI 用户 ID"}, 400)
                if platform == "sub2api" and auth_mode == "password" and (not login_username or not login_password):
                    return json_response(self, {"success": False, "message": "sub2api 需要填写普通用户邮箱和密码"}, 400)
                if platform == "sub2api" and auth_mode == "token" and not access_token:
                    return json_response(self, {"success": False, "message": "导入登录态时需要填写 auth_token"}, 400)
                now = utc_now_iso()
                site_id = db_execute(
                    """
                    INSERT INTO sites
                    (name, base_url, platform, enabled, interval_minutes, login_enabled, auth_mode, login_username, login_password, access_token, access_user_id, refresh_token, token_expires_at, status, last_error, last_check_at, next_check_at, consecutive_failures, current_groups_json, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'unknown', NULL, NULL, ?, 0, NULL, ?, ?)
                    """,
                    (
                        name,
                        base_url,
                        platform,
                        1 if enabled else 0,
                        interval,
                        1 if (login_enabled or platform == "sub2api") else 0,
                        auth_mode if platform == "sub2api" else "password",
                        login_username if platform == "sub2api" and auth_mode == "password" else "",
                        login_password if platform == "sub2api" and auth_mode == "password" else "",
                        access_token if ((platform == "newapi" and login_enabled) or (platform == "sub2api" and auth_mode == "token")) else "",
                        access_user_id if platform == "newapi" and login_enabled else "",
                        refresh_token if platform == "sub2api" and auth_mode == "token" else "",
                        token_expires_at if platform == "sub2api" and auth_mode == "token" else "",
                        next_check_iso(interval),
                        now,
                        now,
                    ),
                )
                return json_response(self, {"success": True, "id": site_id})

            if path.startswith("/api/sites/") and path.endswith("/check"):
                try:
                    site_id = int(path.split("/")[3])
                except Exception:
                    return json_response(self, {"success": False, "message": "invalid site id"}, 400)
                result = detect_site(site_id)
                return json_response(self, result)

            if path == "/api/notifications/test-email":
                body = read_json_body(self)
                if body:
                    update_notification_settings(body)
                message = "这是一封上游分组倍率监控测试邮件。"
                ok, error_message = send_email_message("上游倍率监控邮箱测试", message)
                return json_response(self, {"success": ok, "message": error_message or "测试邮件已发送"})

            if path == "/api/notifications/test-wecom":
                body = read_json_body(self)
                if body:
                    update_notification_settings(body)
                message = "这是一条上游分组倍率监控测试消息。"
                ok, error_message = send_wecom_message("上游倍率监控企业微信测试", message)
                return json_response(self, {"success": ok, "message": error_message or "测试消息已发送"})

            self.send_error(HTTPStatus.NOT_FOUND)
        except Exception as exc:
            return json_response(self, {"success": False, "message": str(exc)}, 500)

    def do_PUT(self) -> None:
        path = urlparse(self.path).path
        if path.startswith("/api/sites/"):
            try:
                site_id = int(path.split("/")[3])
            except Exception:
                return json_response(self, {"success": False, "message": "invalid site id"}, 400)
            body = read_json_body(self)
            site = db_query_one("SELECT * FROM sites WHERE id = ?", (site_id,))
            if not site:
                return json_response(self, {"success": False, "message": "site not found"}, 404)
            fields = []
            params = []

            if "name" in body:
                fields.append("name = ?")
                params.append(str(body["name"]).strip())
            if "base_url" in body:
                fields.append("base_url = ?")
                params.append(normalize_base_url(str(body["base_url"])))
            target_platform = str(body.get("platform") or site.get("platform") or "newapi").strip().lower()
            if target_platform not in {"newapi", "sub2api"}:
                return json_response(self, {"success": False, "message": "platform invalid"}, 400)
            if "platform" in body:
                fields.append("platform = ?")
                params.append(target_platform)
            if "enabled" in body:
                fields.append("enabled = ?")
                params.append(1 if body["enabled"] else 0)
            if "interval_minutes" in body:
                fields.append("interval_minutes = ?")
                params.append(max(MIN_INTERVAL_MINUTES, int(body["interval_minutes"])))
            if "login_enabled" in body:
                login_enabled = bool(body["login_enabled"])
                login_username = str(body.get("login_username") or "").strip()
                login_password = str(body.get("login_password") or "")
                access_token = str(body.get("access_token") or "").strip()
                access_user_id = str(body.get("access_user_id") or "").strip()
                refresh_token = str(body.get("refresh_token") or "").strip()
                token_expires_at = str(body.get("token_expires_at") or "").strip()
                auth_mode = str(body.get("auth_mode") or site.get("auth_mode") or "password").strip().lower()
                existing_access_token = site.get("access_token") or ""
                existing_access_user_id = site.get("access_user_id") or ""
                existing_refresh_token = site.get("refresh_token") or ""
                existing_username = site.get("login_username") or ""
                existing_password = site.get("login_password") or ""
                if auth_mode not in {"password", "token"}:
                    return json_response(self, {"success": False, "message": "auth_mode invalid"}, 400)
                if target_platform == "newapi":
                    has_token_after_update = bool(access_token or existing_access_token)
                    has_user_id_after_update = bool(access_user_id or existing_access_user_id)
                    if login_enabled and (not has_token_after_update or not has_user_id_after_update):
                        return json_response(self, {"success": False, "message": "使用系统访问令牌时需要填写 NewAPI 用户 ID"}, 400)
                if target_platform == "sub2api" and auth_mode == "password" and (not (login_username or existing_username) or not (login_password or existing_password)):
                    return json_response(self, {"success": False, "message": "sub2api 需要填写普通用户邮箱和密码"}, 400)
                if target_platform == "sub2api" and auth_mode == "token" and not (access_token or existing_access_token):
                    return json_response(self, {"success": False, "message": "导入登录态时需要填写 auth_token"}, 400)
                fields.append("login_enabled = ?")
                params.append(1 if (login_enabled or target_platform == "sub2api") else 0)
                fields.append("auth_mode = ?")
                params.append(auth_mode if target_platform == "sub2api" else "password")
                if target_platform == "sub2api":
                    if auth_mode == "password" and login_username:
                        fields.append("login_username = ?")
                        params.append(login_username)
                    if auth_mode == "password" and login_password:
                        fields.append("login_password = ?")
                        params.append(login_password)
                    if auth_mode == "token":
                        fields.append("login_username = ?")
                        params.append("")
                        fields.append("login_password = ?")
                        params.append("")
                        if access_token:
                            fields.append("access_token = ?")
                            params.append(access_token)
                        if refresh_token or not existing_refresh_token:
                            fields.append("refresh_token = ?")
                            params.append(refresh_token)
                        fields.append("token_expires_at = ?")
                        params.append(token_expires_at)
                    else:
                        fields.append("access_token = ?")
                        params.append("")
                        fields.append("refresh_token = ?")
                        params.append("")
                        fields.append("token_expires_at = ?")
                        params.append("")
                    fields.append("access_user_id = ?")
                    params.append("")
                else:
                    fields.append("login_username = ?")
                    params.append("")
                    fields.append("login_password = ?")
                    params.append("")
                    fields.append("refresh_token = ?")
                    params.append("")
                    fields.append("token_expires_at = ?")
                    params.append("")
                    if not login_enabled:
                        fields.append("access_token = ?")
                        params.append("")
                        fields.append("access_user_id = ?")
                        params.append("")
                    if login_enabled and access_token:
                        fields.append("access_token = ?")
                        params.append(access_token)
                    if login_enabled and access_user_id:
                        fields.append("access_user_id = ?")
                        params.append(access_user_id)
            if "status" in body:
                fields.append("status = ?")
                params.append(str(body["status"]))

            if not fields:
                return json_response(self, {"success": False, "message": "no fields"}, 400)

            fields.append("updated_at = ?")
            params.append(utc_now_iso())
            params.append(site_id)

            db_execute(f"UPDATE sites SET {', '.join(fields)} WHERE id = ?", params)
            invalidate_site_model_cache(site_id)
            schedule_model_cache_refresh(site_id)
            return json_response(self, {"success": True})

        if path == "/api/notifications/settings":
            body = read_json_body(self)
            try:
                update_notification_settings(body)
            except ValueError as exc:
                return json_response(self, {"success": False, "message": str(exc)}, 400)
            return json_response(self, {"success": True, "data": notification_settings_payload()})

        self.send_error(HTTPStatus.NOT_FOUND)

    def do_DELETE(self) -> None:
        path = urlparse(self.path).path
        if path.startswith("/api/sites/"):
            try:
                site_id = int(path.split("/")[3])
            except Exception:
                return json_response(self, {"success": False, "message": "invalid site id"}, 400)
            db_execute("DELETE FROM sites WHERE id = ?", (site_id,))
            invalidate_site_model_cache(site_id)
            return json_response(self, {"success": True})
        self.send_error(HTTPStatus.NOT_FOUND)


def bootstrap_demo_data() -> None:
    if db_query_one("SELECT id FROM sites LIMIT 1"):
        return

    now = utc_now_iso()
    db_execute(
        """
        INSERT INTO sites
        (name, base_url, platform, enabled, interval_minutes, status, last_error, last_check_at, next_check_at, consecutive_failures, current_groups_json, created_at, updated_at)
        VALUES (?, ?, 'newapi', 1, 3, 'unknown', NULL, NULL, ?, 0, NULL, ?, ?)
        """,
        (
            "Demo NewAPI",
            "http://127.0.0.1:3000",
            next_check_iso(3),
            now,
            now,
        ),
    )


def main() -> None:
    ensure_dirs()
    init_db()
    bootstrap_demo_data()

    worker = threading.Thread(target=schedule_worker, daemon=True)
    worker.start()
    warm_model_cache()

    server = ThreadingHTTPServer((SERVER_HOST, SERVER_PORT), Handler)
    ui = "apps/web/dist" if WEB_DIST_DIR.exists() else "static/"
    print(f"Upstream Ratio Watch running at http://{SERVER_HOST}:{SERVER_PORT} (ui={ui})")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        STOP_EVENT.set()
        server.server_close()


if __name__ == "__main__":
    main()
