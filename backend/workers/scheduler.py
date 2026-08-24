"""Process-level monitoring scheduler."""

from __future__ import annotations

import threading
import traceback

from backend.core.config import SCAN_INTERVAL_SECONDS, DEFAULT_INTERVAL_MINUTES
from backend.core.state import STOP_EVENT
from backend.core.time import app_now, utc_now_iso, next_check_iso
from backend.db.connection import db_query_all, db_execute
from backend.services.sync_service import run_due_admin_key_syncs
from backend.legacy_runtime import detect_site


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
            run_due_admin_key_syncs(now)
        except Exception:
            pass
        STOP_EVENT.wait(SCAN_INTERVAL_SECONDS)


class SchedulerWorker:
    def __init__(self) -> None:
        self.thread: threading.Thread | None = None

    def start(self) -> None:
        if self.thread and self.thread.is_alive():
            return
        STOP_EVENT.clear()
        self.thread = threading.Thread(
            target=schedule_worker,
            name="upstream-scheduler",
            daemon=True,
        )
        self.thread.start()

    def stop(self, timeout: float = 5) -> None:
        STOP_EVENT.set()
        if self.thread and self.thread.is_alive():
            self.thread.join(timeout=timeout)
