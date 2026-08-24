"""FastAPI application factory and process lifecycle."""

from __future__ import annotations

import time
import os
from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from backend.api.routers import admin_sites, auth, monitoring, notifications, session_sync, settings
from backend.core.config import SLOW_REQUEST_THRESHOLD_MS, WEB_DIST_DIR, get_settings
from backend.core.errors import (
    DatabasePoolTimeoutError,
    database_busy_handler,
    http_exception_handler,
    validation_exception_handler,
)
from backend.core.security import require_console_auth
from backend.core.state import STOP_EVENT
from backend.db.connection import close_database_pool
from backend.db.schema import ensure_dirs, init_db, wait_for_db
from backend.workers.cache import ModelCacheWorker
from backend.workers.scheduler import SchedulerWorker


class SPAStaticFiles(StaticFiles):
    """Serve Vite assets and fall back to index.html for client-side routes."""

    async def get_response(self, path: str, scope):  # type: ignore[no-untyped-def]
        try:
            return await super().get_response(path, scope)
        except StarletteHTTPException as exc:
            if exc.status_code != 404:
                raise
            return await super().get_response("index.html", scope)


def _seed_demo_if_enabled() -> None:
    if (os.getenv("SEED_DEMO") or "").strip().lower() in {"1", "true", "yes"}:
        from backend.db.schema import bootstrap_demo_data
        bootstrap_demo_data()


@asynccontextmanager
async def lifespan(application: FastAPI) -> AsyncIterator[None]:
    """Initialize shared resources and own the process-level workers."""
    settings_value = get_settings()
    application.state.settings = settings_value
    ensure_dirs()
    wait_for_db()
    init_db()
    _seed_demo_if_enabled()

    STOP_EVENT.clear()
    worker: SchedulerWorker | None = None
    if settings_value.scheduler_enabled:
        worker = SchedulerWorker()
        worker.start()
        application.state.scheduler = worker
    else:
        application.state.scheduler = None

    # Cache warming is intentionally after the database is ready.  It uses the
    # existing per-site locks and never blocks the HTTP startup path on upstream
    # responses.
    ModelCacheWorker().warm()
    try:
        yield
    finally:
        STOP_EVENT.set()
        if worker is not None:
            worker.stop(timeout=5)
        close_database_pool()


settings_value = get_settings()
app = FastAPI(
    title="Upstream Ratio Watch",
    version="0.1.0",
    lifespan=lifespan,
    docs_url="/docs" if settings_value.enable_api_docs else None,
    redoc_url="/redoc" if settings_value.enable_api_docs else None,
    openapi_url="/openapi.json" if settings_value.enable_api_docs else None,
)

app.add_exception_handler(StarletteHTTPException, http_exception_handler)
app.add_exception_handler(RequestValidationError, validation_exception_handler)
app.add_exception_handler(DatabasePoolTimeoutError, database_busy_handler)


@app.middleware("http")
async def request_timing_middleware(request: Request, call_next):  # type: ignore[no-untyped-def]
    started = time.monotonic()
    response = None
    try:
        response = await call_next(request)
        return response
    finally:
        elapsed_ms = (time.monotonic() - started) * 1000
        if settings_value.slow_request_threshold_ms > 0 and elapsed_ms >= settings_value.slow_request_threshold_ms:
            from urllib.parse import urlparse
            safe_path = urlparse(str(request.url.path or "")).path or "/"
            print(f"[慢请求] {request.method} {safe_path} {response.status_code if response is not None else 500} {elapsed_ms:.1f}ms", flush=True)


@app.get("/healthz", include_in_schema=False)
def healthz() -> dict[str, str]:
    return {"status": "ok"}


# Every API router keeps the legacy public-path exceptions through the shared
# dependency.  This prevents newly added endpoints from accidentally bypassing
# CONSOLE_PASSWORD protection.
protected = {"dependencies": [Depends(require_console_auth)]}
app.include_router(auth.router, prefix="/api", **protected)
app.include_router(monitoring.router, prefix="/api", **protected)
app.include_router(notifications.router, prefix="/api", **protected)
app.include_router(settings.router, prefix="/api", **protected)
app.include_router(session_sync.router, prefix="/api", **protected)
app.include_router(admin_sites.router, prefix="/api", **protected)


if WEB_DIST_DIR.exists():
    app.mount("/", SPAStaticFiles(directory=str(WEB_DIST_DIR), html=True), name="web")


def run() -> None:
    import uvicorn

    uvicorn.run(
        app,
        host=settings_value.host,
        port=settings_value.port,
        workers=1,
        log_level="info",
    )
