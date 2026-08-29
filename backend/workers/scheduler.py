"""Process-level monitoring scheduler, backed by APScheduler."""

from __future__ import annotations

import traceback
from datetime import datetime, timezone

from apscheduler.schedulers.background import BackgroundScheduler

from backend.core.config import DEFAULT_INTERVAL_MINUTES, SCAN_INTERVAL_SECONDS
from backend.core.state import STOP_EVENT
from backend.core.time import app_now, next_check_iso, utc_now_iso
from backend.db.connection import db_execute, db_query_all
from backend.services.monitoring_service import detect_site
from backend.services.retention_service import pruneExpiredMonitoringData
from backend.services.sync_service import run_due_admin_key_syncs


def run_scheduler_tick(now: datetime | None = None) -> None:
    """单轮调度：检测到期站点并触发主站 key 同步（由 APScheduler 周期调用）。"""
    now = now or app_now()
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


class SchedulerWorker:
    """APScheduler 封装：替代手写调度线程，负责启停与防重入。

    - ``max_instances=1``：同一时刻最多一轮扫描在跑，不会叠加；
    - ``coalesce=True``：停顿期间错过的多次触发只补跑一次；
    - 停止时先 ``STOP_EVENT.set()``（让在跑的一轮尽快在站点间隙退出），
      再 ``shutdown(wait=False)`` 立即返回，不阻塞 lifespan 关闭路径。
    """

    def __init__(self) -> None:
        self._scheduler = BackgroundScheduler(
            job_defaults={
                "coalesce": True,
                "max_instances": 1,
                "misfire_grace_time": SCAN_INTERVAL_SECONDS,
            },
            daemon=True,
        )
        self._started = False

    def start(self) -> None:
        if self._started:
            return
        STOP_EVENT.clear()
        self._scheduler.add_job(
            run_scheduler_tick,
            "interval",
            seconds=SCAN_INTERVAL_SECONDS,
            id="upstream-due-scan",
            # 立即先跑一轮，保持与旧线程实现一致的启动行为。
            next_run_time=datetime.now(tz=timezone.utc),
        )
        # 快照/变化保留清理：按各主站的 retention_days 每天清一次（0 = 永久）。
        self._scheduler.add_job(
            pruneExpiredMonitoringData,
            "interval",
            hours=24,
            id="monitoring-retention-prune",
            next_run_time=datetime.now(tz=timezone.utc),
        )
        self._scheduler.start()
        self._started = True

    def stop(self, timeout: float = 5) -> None:  # noqa: ARG002 - 兼容旧签名
        STOP_EVENT.set()
        if self._started:
            self._scheduler.shutdown(wait=False)
            self._started = False
