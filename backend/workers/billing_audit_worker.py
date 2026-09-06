"""Billing-audit scheduler entry point.

对应设计稿第 10 节：真正的周期任务。挂在现有 SchedulerWorker 上每分钟
tick 一次，tick 内部读设置判断开关与到期时间——这样运行期改设置立即生效，
不用重建调度器；STOP_EVENT 置位后在 tick 边界自然退出。
"""

from __future__ import annotations

from backend.services.billing_audit_service import runBillingAuditTick


def billingAuditSchedulerTick() -> None:
    try:
        runBillingAuditTick()
    except Exception as exc:  # noqa: BLE001 - 周期任务异常不拖垮调度器
        print(f"[计费核对] 调度轮异常：{exc}", flush=True)
