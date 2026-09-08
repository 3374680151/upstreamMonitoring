"""Billing-audit scheduler entry point.

对应设计稿第 10 节：真正的周期任务。挂在现有 SchedulerWorker 上每分钟
tick 一次，tick 内部读设置判断开关与到期时间——这样运行期改设置立即生效，
不用重建调度器；STOP_EVENT 置位后在 tick 边界自然退出。
"""

from __future__ import annotations

from backend.services.billing_audit_service import runBillingAuditTick
from backend.services.billing_price_sync_service import syncOfficialPricesFromAdminSites


def billingAuditSchedulerTick() -> None:
    try:
        runBillingAuditTick()
    except Exception as exc:  # noqa: BLE001 - 周期任务异常不拖垮调度器
        print(f"[计费核对] 调度轮异常：{exc}", flush=True)


def officialPriceSyncTick() -> None:
    """每日官方价同步：只回放本地主站快照，失败不影响其他周期任务。"""
    try:
        summary = syncOfficialPricesFromAdminSites()
        if summary.get("models") or summary.get("errors"):
            print(
                "[官方价同步] "
                f"admin_sites={summary.get('admin_sites', 0)} "
                f"models={summary.get('models', 0)} "
                f"applied={summary.get('applied', 0)} "
                f"errors={summary.get('errors') or '无'}",
                flush=True,
            )
    except Exception as exc:  # noqa: BLE001 - 周期任务异常不拖垮调度器
        print(f"[官方价同步] 定时同步异常：{exc}", flush=True)
