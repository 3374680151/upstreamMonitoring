"""Runtime configuration for the FastAPI application."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


APP_DIR = Path(__file__).resolve().parents[2]
DATA_DIR = APP_DIR / "data"
WEB_DIST_DIR = APP_DIR / "apps" / "web" / "dist"


def _load_dotenv() -> None:
    """Load the local .env without adding a dotenv dependency."""
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


def _env_int(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(os.getenv(name) or default)
    except (TypeError, ValueError):
        value = default
    return max(minimum, min(maximum, value))


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
        host=os.getenv("HOST", "127.0.0.1"),
        port=int(os.getenv("PORT", "8000")),
        enable_api_docs=(os.getenv("ENABLE_API_DOCS", "0").strip().lower() in {"1", "true", "yes"}),
        scheduler_enabled=(os.getenv("SCHEDULER_ENABLED", "1").strip().lower() not in {"0", "false", "no"}),
        slow_request_threshold_ms=_env_int("SLOW_REQUEST_THRESHOLD_MS", 500, 0, 60000),
    )
