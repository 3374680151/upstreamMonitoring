"""Process-level monitoring scheduler."""

from __future__ import annotations

import threading
import traceback
from datetime import timedelta

from backend.core.time import app_now, utc_now_iso
from backend.repositories.sites import SiteRepository
from backend.services.check_service import CheckService
from backend.services.key_sync_service import KeySyncService


class SchedulerWorker:
    def __init__(self) -> None:
        self.thread: threading.Thread | None = None
        self.stop_event = threading.Event()
        self.sites = SiteRepository()
        self.checks = CheckService(sites=self.sites)
        self.key_sync = KeySyncService()

    def start(self) -> None:
        if self.thread and self.thread.is_alive():
            return
        self.stop_event.clear()
        self.thread = threading.Thread(
            target=self._run,
            name="upstream-scheduler",
            daemon=True,
        )
        self.thread.start()

    def _run(self) -> None:
        while not self.stop_event.is_set():
            try:
                now = app_now().isoformat(timespec="seconds")
                due_sites = [
                    site
                    for site in self.sites.list()
                    if site.get("enabled")
                    and (
                        not site.get("next_check_at")
                        or str(site.get("next_check_at")) <= now
                    )
                ]
                for site in due_sites:
                    if self.stop_event.is_set():
                        break
                    try:
                        self.checks.check(int(site["id"]))
                    except Exception:
                        checked_at = utc_now_iso()
                        failures = int(site.get("consecutive_failures") or 0) + 1
                        interval = max(1, int(site.get("interval_minutes") or 3))
                        self.sites.update_fields(
                            int(site["id"]),
                            {
                                "status": "failed" if failures >= 3 else "warning",
                                "last_error": traceback.format_exc(limit=2),
                                "last_check_at": checked_at,
                                "next_check_at": (
                                    app_now() + timedelta(minutes=interval)
                                ).isoformat(timespec="seconds"),
                                "consecutive_failures": failures,
                                "updated_at": checked_at,
                            },
                        )
                try:
                    self.key_sync.run_due(app_now())
                except Exception:
                    pass
            except Exception:
                pass
            self.stop_event.wait(30)

    def stop(self, timeout: float = 5) -> None:
        self.stop_event.set()
        if self.thread and self.thread.is_alive():
            self.thread.join(timeout=timeout)
