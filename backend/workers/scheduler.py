"""Process-level monitoring scheduler."""

from __future__ import annotations

import threading

from backend import legacy_runtime as legacy


class SchedulerWorker:
    def __init__(self) -> None:
        self.thread: threading.Thread | None = None

    def start(self) -> None:
        if self.thread and self.thread.is_alive():
            return
        legacy.STOP_EVENT.clear()
        self.thread = threading.Thread(
            target=legacy.schedule_worker,
            name="upstream-scheduler",
            daemon=True,
        )
        self.thread.start()

    def stop(self, timeout: float = 5) -> None:
        legacy.STOP_EVENT.set()
        if self.thread and self.thread.is_alive():
            self.thread.join(timeout=timeout)
