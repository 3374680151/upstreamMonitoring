"""Runtime configuration for the FastAPI application."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


# Resolve paths from the repository root, not from the backend package directory.
APP_DIR = Path(__file__).resolve().parents[2]
DATA_DIR = APP_DIR / "data"
STATIC_DIR = APP_DIR / "static"
WEB_DIST_DIR = APP_DIR / "apps" / "web" / "dist"
DB_PATH = DATA_DIR / "app.db"  # 旧 SQLite 路径，仅供一次性迁移工具引用


def _load_dotenv() -> None:
    """把本地 .env（已 gitignore）的 KEY=VALUE 读入环境变量。

    使用 python-dotenv：数据库密码 / SMTP / 令牌等密钥只留在本地 .env，
    不进源码、不进 git。已存在的环境变量优先，不会被覆盖（override=False）。
    """
    load_dotenv(APP_DIR / ".env", override=False, encoding="utf-8")


# 在读取任何依赖 .env 的模块级常量之前先把密钥载入环境变量。
_load_dotenv()


def _env_int(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(os.getenv(name) or default)
    except (TypeError, ValueError):
        value = default
    return max(minimum, min(maximum, value))


# 数据库连接（MySQL）：全部走环境变量 / .env，密码严禁写死在源码里。
DB_CONFIG = {
    "host": os.getenv("DB_HOST", "127.0.0.1"),
    "port": int(os.getenv("DB_PORT", "3306")),
    "user": os.getenv("DB_USER", "root"),
    "password": os.getenv("DB_PASSWORD", ""),
    "database": os.getenv("DB_NAME", "upstream"),
}

DEFAULT_INTERVAL_MINUTES = 3
MIN_INTERVAL_MINUTES = 1
DB_POOL_SIZE = _env_int("DB_POOL_SIZE", 8, 1, 32)
DB_POOL_ACQUIRE_TIMEOUT_SECONDS = _env_int("DB_POOL_ACQUIRE_TIMEOUT", 5, 1, 60)
DB_CONNECT_TIMEOUT_SECONDS = _env_int("DB_CONNECT_TIMEOUT", 5, 1, 60)
DB_READ_TIMEOUT_SECONDS = _env_int("DB_READ_TIMEOUT", 15, 1, 300)
DB_WRITE_TIMEOUT_SECONDS = _env_int("DB_WRITE_TIMEOUT", 15, 1, 300)
HTTP_TIMEOUT_SECONDS = _env_int("UPSTREAM_HTTP_TIMEOUT", 15, 1, 120)
SLOW_REQUEST_THRESHOLD_MS = _env_int("SLOW_REQUEST_THRESHOLD_MS", 500, 0, 60000)
SCAN_INTERVAL_SECONDS = 10
SERVER_HOST = os.getenv("HOST", "127.0.0.1")
SERVER_PORT = int(os.getenv("PORT", "8000"))
# 控制台登录密码：留空则不启用鉴权（本地/内网直连场景）。设置后所有 /api/* 需先登录。
CONSOLE_PASSWORD = (os.getenv("CONSOLE_PASSWORD") or "").strip()
try:
    CONSOLE_SESSION_TTL_SECONDS = max(300, int(os.getenv("CONSOLE_SESSION_TTL") or "604800"))
except ValueError:
    CONSOLE_SESSION_TTL_SECONDS = 604800  # 7 天


@dataclass(frozen=True)
class Settings:
    host: str
    port: int
    enable_api_docs: bool
    scheduler_enabled: bool
    slow_request_threshold_ms: int


def get_settings() -> Settings:
    _load_dotenv()
    return Settings(
        host=SERVER_HOST,
        port=SERVER_PORT,
        enable_api_docs=(os.getenv("ENABLE_API_DOCS", "0").strip().lower() in {"1", "true", "yes"}),
        scheduler_enabled=(os.getenv("SCHEDULER_ENABLED", "1").strip().lower() not in {"0", "false", "no"}),
        slow_request_threshold_ms=SLOW_REQUEST_THRESHOLD_MS,
    )
