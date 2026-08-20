"""Pure notification subject and message formatting."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from backend.core.time import application_timezone


def _change_value(raw: Any) -> str:
    if raw is None:
        return "-"
    if isinstance(raw, dict) and "ratio" in raw:
        try:
            return f"{float(raw.get('ratio')):.2f}x"
        except (TypeError, ValueError):
            return str(raw.get("ratio"))
    return str(raw)


def _ratio_number(raw: Any) -> float | None:
    if isinstance(raw, dict):
        raw = raw.get("ratio")
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def _ratio_direction(change: dict[str, Any]) -> str:
    old_ratio = _ratio_number(change.get("old_value"))
    new_ratio = _ratio_number(change.get("new_value"))
    if old_ratio is None or new_ratio is None:
        return "changed"
    if new_ratio > old_ratio:
        return "up"
    if new_ratio < old_ratio:
        return "down"
    return "changed"


def _percent_text(change: dict[str, Any]) -> str:
    percent = change.get("change_percent")
    if isinstance(percent, (int, float)):
        return f"{abs(percent):.2f}".rstrip("0").rstrip(".") + "%"
    return ""


def _format_time(value: str) -> str:
    try:
        parsed = datetime.fromisoformat(str(value or ""))
    except ValueError:
        return str(value or "")
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    local = parsed.astimezone(application_timezone())
    suffix = f" {local.tzname()}" if local.tzname() else ""
    return local.strftime("%Y-%m-%d %H:%M:%S") + suffix


def _platform_label(site: dict[str, Any]) -> str:
    return "sub2api" if str(site.get("platform") or "newapi") == "sub2api" else "NewAPI"


def format_subject(site: dict[str, Any], changes: list[dict[str, Any]]) -> str:
    site_name = str(site.get("name") or "")
    platform = _platform_label(site)
    ratio_changes = [item for item in changes if item.get("change_type") == "ratio_changed"]
    if len(ratio_changes) == 1:
        change = ratio_changes[0]
        direction = _ratio_direction(change)
        label = "倍率上涨" if direction == "up" else "倍率下降" if direction == "down" else "倍率变动"
        return (
            f"【{platform} {label}】{site_name} / {change.get('group_name') or '-'}："
            f"{_change_value(change.get('old_value'))} -> {_change_value(change.get('new_value'))}"
        )
    if len(ratio_changes) > 1:
        return f"【{platform} 倍率变动】{site_name}：{len(ratio_changes)} 个分组有变化"
    added = [item for item in changes if item.get("change_type") == "group_added"]
    removed = [item for item in changes if item.get("change_type") == "group_removed"]
    if len(added) == 1 and not removed:
        change = added[0]
        return (
            f"【{platform} 新增分组】{site_name} / {change.get('group_name') or '-'}："
            f"{_change_value(change.get('new_value'))}"
        )
    if len(removed) == 1 and not added:
        return f"【{platform} 删除分组】{site_name} / {removed[0].get('group_name') or '-'}"
    return f"【{platform} 分组变化】{site_name}：{len(changes)} 条变化"


def format_message(
    site: dict[str, Any], changes: list[dict[str, Any]], checked_at: str
) -> str:
    up = [
        item
        for item in changes
        if item.get("change_type") == "ratio_changed" and _ratio_direction(item) == "up"
    ]
    down = [
        item
        for item in changes
        if item.get("change_type") == "ratio_changed" and _ratio_direction(item) == "down"
    ]
    changed = [
        item
        for item in changes
        if item.get("change_type") == "ratio_changed" and _ratio_direction(item) == "changed"
    ]
    added = [item for item in changes if item.get("change_type") == "group_added"]
    removed = [item for item in changes if item.get("change_type") == "group_removed"]
    desc_changed = [item for item in changes if item.get("change_type") == "desc_changed"]
    other = [
        item
        for item in changes
        if item.get("change_type")
        not in {"ratio_changed", "group_added", "group_removed", "desc_changed"}
    ]
    lines = [
        "上游倍率监控提醒",
        f"站点：{site.get('name') or ''}",
        f"平台：{_platform_label(site)}",
        f"时间：{_format_time(checked_at)}",
        f"本次共 {len(changes)} 条变化",
    ]

    def append_ratio_block(title: str, entries: list[dict[str, Any]], suffix: str) -> None:
        if not entries:
            return
        lines.extend(["", title])
        for change in entries[:6]:
            percent = _percent_text(change)
            extra = f"，{suffix} {percent}" if percent else f"，{suffix}"
            lines.append(
                f"- {change.get('group_name') or '-'}："
                f"{_change_value(change.get('old_value'))} -> "
                f"{_change_value(change.get('new_value'))}{extra}"
            )

    append_ratio_block("涨价了，钱包先别眨眼：", up, "上涨")
    append_ratio_block("降价了，这波可以多看两眼：", down, "下降")
    if changed:
        lines.extend(["", "倍率变了，但方向不太好判断："])
        for change in changed[:6]:
            lines.append(
                f"- {change.get('group_name') or '-'}："
                f"{_change_value(change.get('old_value'))} -> {_change_value(change.get('new_value'))}"
            )
    if added:
        lines.extend(["", "新分组上线："])
        for change in added[:6]:
            lines.append(f"- {change.get('group_name') or '-'}：{_change_value(change.get('new_value'))}")
    if removed:
        lines.extend(["", "分组下线了："])
        for change in removed[:6]:
            lines.append(f"- {change.get('group_name') or '-'}：原倍率 {_change_value(change.get('old_value'))}")
    if desc_changed:
        lines.extend(["", "描述有变化："])
        for change in desc_changed[:6]:
            lines.append(f"- {change.get('group_name') or '-'}")
    if other:
        lines.extend(["", "其他配置变化："])
        for change in other[:8]:
            lines.append(
                f"- {change.get('group_name') or '-'}："
                f"{_change_value(change.get('old_value'))} -> {_change_value(change.get('new_value'))}"
            )
    if len(changes) > 8:
        lines.extend(["", f"其余 {len(changes) - 8} 条变化请在面板查看"])
    return "\n".join(lines)


__all__ = ["format_message", "format_subject"]
