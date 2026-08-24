"""Notification service: change formatting and push dispatch.

The formatting helpers (``percent_text``, ``format_change_subject``,
``format_change_notification``) and the dispatch entry point
``notify_changes`` were moved here from ``backend.legacy_runtime``.
The legacy runtime re-exports every public name below so existing
``legacy.<fn>`` callers keep working unchanged.
"""

from __future__ import annotations

from typing import Any, Dict, List

from backend.core.normalize import (
    format_change_value,
    platform_label,
    ratio_direction,
)
from backend.core.time import app_now, fmt_local_time_for_message
from backend.integrations.email import send_email_message
from backend.integrations.wecom import send_wecom_message
from backend.repositories.notifications import (
    NotificationRepository,
    get_notification_settings,
    log_notification,
    update_notification_settings,
)


def percent_text(change: Dict[str, Any]) -> str:
    percent = change.get("change_percent")
    if isinstance(percent, (int, float)):
        return f"{abs(percent):.2f}".rstrip("0").rstrip(".") + "%"
    return ""


def format_change_subject(site: Dict[str, Any], changes: List[Dict[str, Any]]) -> str:
    site_name = site["name"]
    platform = platform_label(site)
    ratio_changes = [item for item in changes if item.get("change_type") == "ratio_changed"]
    if len(ratio_changes) == 1:
        change = ratio_changes[0]
        label = "倍率上涨" if ratio_direction(change) == "up" else "倍率下降" if ratio_direction(change) == "down" else "倍率变动"
        return f"【{platform} {label}】{site_name} / {change.get('group_name') or '-'}：{format_change_value(change.get('old_value'))} -> {format_change_value(change.get('new_value'))}"
    if len(ratio_changes) > 1:
        return f"【{platform} 倍率变动】{site_name}：{len(ratio_changes)} 个分组有变化"

    added = [item for item in changes if item.get("change_type") == "group_added"]
    removed = [item for item in changes if item.get("change_type") == "group_removed"]
    if len(added) == 1 and not removed:
        change = added[0]
        return f"【{platform} 新增分组】{site_name} / {change.get('group_name') or '-'}：{format_change_value(change.get('new_value'))}"
    if len(removed) == 1 and not added:
        change = removed[0]
        return f"【{platform} 删除分组】{site_name} / {change.get('group_name') or '-'}"
    return f"【{platform} 分组变化】{site_name}：{len(changes)} 条变化"


def format_change_notification(site: Dict[str, Any], changes: List[Dict[str, Any]], checked_at: str) -> str:
    up_changes = [item for item in changes if item.get("change_type") == "ratio_changed" and ratio_direction(item) == "up"]
    down_changes = [item for item in changes if item.get("change_type") == "ratio_changed" and ratio_direction(item) == "down"]
    changed_ratio = [item for item in changes if item.get("change_type") == "ratio_changed" and ratio_direction(item) == "changed"]
    added = [item for item in changes if item.get("change_type") == "group_added"]
    removed = [item for item in changes if item.get("change_type") == "group_removed"]
    desc_changed = [item for item in changes if item.get("change_type") == "desc_changed"]
    other_changed = [
        item for item in changes
        if item.get("change_type") not in {"ratio_changed", "group_added", "group_removed", "desc_changed"}
    ]

    lines = [
        "上游倍率监控提醒",
        f"站点：{site['name']}",
        f"平台：{platform_label(site)}",
        f"时间：{fmt_local_time_for_message(checked_at)}",
        f"本次共 {len(changes)} 条变化",
    ]

    def append_ratio_block(title: str, items: List[Dict[str, Any]], suffix: str) -> None:
        if not items:
            return
        lines.extend(["", title])
        for change in items[:6]:
            percent = percent_text(change)
            extra = f"，{suffix} {percent}" if percent else f"，{suffix}"
            lines.append(
                f"- {change.get('group_name') or '-'}：{format_change_value(change.get('old_value'))} -> {format_change_value(change.get('new_value'))}{extra}"
            )

    append_ratio_block("涨价了，钱包先别眨眼：", up_changes, "上涨")
    append_ratio_block("降价了，这波可以多看两眼：", down_changes, "下降")

    if changed_ratio:
        lines.extend(["", "倍率变了，但方向不太好判断："])
        for change in changed_ratio[:6]:
            lines.append(f"- {change.get('group_name') or '-'}：{format_change_value(change.get('old_value'))} -> {format_change_value(change.get('new_value'))}")

    if added:
        lines.extend(["", "新分组上线："])
        for change in added[:6]:
            lines.append(f"- {change.get('group_name') or '-'}：{format_change_value(change.get('new_value'))}")

    if removed:
        lines.extend(["", "分组下线了："])
        for change in removed[:6]:
            lines.append(f"- {change.get('group_name') or '-'}：原倍率 {format_change_value(change.get('old_value'))}")

    if desc_changed:
        lines.extend(["", "描述有变化："])
        for change in desc_changed[:6]:
            lines.append(f"- {change.get('group_name') or '-'}")

    if other_changed:
        lines.extend(["", "其他配置变化："])
        for change in other_changed[:8]:
            lines.append(
                f"- {change.get('group_name') or '-'}：{format_change_value(change.get('old_value'))} -> {format_change_value(change.get('new_value'))}"
            )

    if len(changes) > 8:
        lines.append("")
        lines.append(f"其余 {len(changes) - 8} 条变化请在面板查看")
    return "\n".join(lines)


def notify_changes(site: Dict[str, Any], changes: List[Dict[str, Any]], checked_at: str) -> None:
    if not changes:
        return
    subject = format_change_subject(site, changes)
    message = format_change_notification(site, changes, checked_at)
    send_email_message(subject, message)
    send_wecom_message(subject, message)


class NotificationService:
    def __init__(self, repository: NotificationRepository | None = None) -> None:
        self.repository = repository or NotificationRepository()

    def settings_payload(self) -> dict[str, Any]:
        return self.repository.payload()

    def logs(self, limit: int = 30):
        return self.repository.logs(limit)

    def update(self, payload: dict[str, Any]) -> None:
        update_notification_settings(payload)

    def send_email(self, subject: str, message: str):
        return send_email_message(subject, message)

    def send_wecom(self, subject: str, message: str):
        return send_wecom_message(subject, message)
