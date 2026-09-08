"""Billing-audit red-alert push (email + WeCom).

对应设计稿 ``docs/billing-audit/billing-audit-方案.md`` 5.3 节
``billing_audit_push_on_red``（P2 生效）。每轮核对结束后调用：本轮新判出的
红行聚合成一条摘要，经邮件/企业微信通道发送；发送结果由 integrations 层
自行写入 ``notification_logs``。只读本地核对结果，不再触上游。
"""

from __future__ import annotations

from typing import Any, Dict, List

from backend.integrations.email import send_email_message
from backend.integrations.wecom import send_wecom_message
from backend.core.time import app_now, fmt_local_time_for_message

MAX_ALERT_ROWS = 8


def formatRedAlertMessage(
    adminSiteName: str, rows: List[Dict[str, Any]]
) -> tuple[str, str]:
    """红行摘要：每行给出 模型 / 原因码 / 主站实收 vs 上游实扣。"""
    count = len(rows)
    subject = f"【Upstream】计费核对：{adminSiteName} 本轮新增 {count} 条红色异常"
    lines: List[str] = [
        f"主站 {adminSiteName} 于 {fmt_local_time_for_message(app_now().isoformat(timespec='seconds'))} 的核对轮中新判出 {count} 条红色异常：",
        "",
    ]
    shown = rows[:MAX_ALERT_ROWS]
    for row in shown:
        reasons = "、".join(row.get("reason_codes") or []) or "mismatch"
        model = str(row.get("model_name") or "unknown")
        mainUsd = row.get("main_usd")
        upstreamUsd = row.get("upstream_actual_usd")
        amounts = f"主站 ${mainUsd:g} vs 上游 ${upstreamUsd:g}" if isinstance(mainUsd, (int, float)) and isinstance(upstreamUsd, (int, float)) else "金额缺失"
        lines.append(f"· {model}（{reasons}）：{amounts}")
    hidden = count - len(shown)
    if hidden > 0:
        lines.append("")
        lines.append(f"其余 {hidden} 条异常请在计费核对面板查看")
    lines.append("")
    lines.append("明细与倍率证据见计费核对页（含详情抽屉）。")
    return subject, "\n".join(lines)


def maybePushRedAlert(
    adminSite: Dict[str, Any], settings: Dict[str, Any], redRows: List[Dict[str, Any]]
) -> bool:
    """push_on_red 开启且本轮有新红行时推送；返回是否真的发送。"""
    if not settings.get("push_on_red") or not redRows:
        return False
    subject, message = formatRedAlertMessage(
        str(adminSite.get("name") or f"#{adminSite.get('id')}"), redRows
    )
    send_email_message(subject, message)
    send_wecom_message(subject, message)
    return True
