"""Snapshot difference calculations.

This module is deliberately free of database, HTTP, and notification code.
It is safe to call from both the manual-check service and the scheduler.
"""

from __future__ import annotations

from typing import Any


def _format_change_value(value: Any) -> str:
    if value is None:
        return "-"
    if isinstance(value, dict) and "ratio" in value:
        ratio = value.get("ratio")
        try:
            return f"{float(ratio):.2f}x"
        except (TypeError, ValueError):
            return str(ratio)
    return str(value)


def diff_groups(
    old_groups: dict[str, dict[str, Any]],
    new_groups: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    """Return stable group/config/model changes between two snapshots."""
    changes: list[dict[str, Any]] = []
    old_names = set(old_groups)
    new_names = set(new_groups)

    for name in sorted(new_names - old_names):
        new_item = new_groups[name]
        message = f"新增分组 {name}"
        if new_item.get("ratio") is not None:
            message += f" · 倍率 {_format_change_value(new_item)}"
        changes.append({
            "change_type": "group_added",
            "group_name": name,
            "old_value": None,
            "new_value": new_item,
            "change_percent": None,
            "message": message,
        })

    for name in sorted(old_names - new_names):
        changes.append({
            "change_type": "group_removed",
            "group_name": name,
            "old_value": old_groups[name],
            "new_value": None,
            "change_percent": None,
            "message": f"删除分组 {name}",
        })

    for name in sorted(old_names & new_names):
        old_item = old_groups[name]
        new_item = new_groups[name]
        if old_item.get("ratio") != new_item.get("ratio"):
            old_ratio = old_item.get("ratio")
            new_ratio = new_item.get("ratio")
            change_percent = None
            if (
                isinstance(old_ratio, (int, float))
                and isinstance(new_ratio, (int, float))
                and old_ratio != 0
            ):
                change_percent = round(
                    (float(new_ratio) - float(old_ratio)) / float(old_ratio) * 100,
                    2,
                )
            changes.append({
                "change_type": "ratio_changed",
                "group_name": name,
                "old_value": old_item,
                "new_value": new_item,
                "change_percent": change_percent,
                "message": f"{name} 倍率 {old_ratio} -> {new_ratio}",
            })

        if old_item.get("desc") != new_item.get("desc"):
            changes.append({
                "change_type": "desc_changed",
                "group_name": name,
                "old_value": old_item.get("desc"),
                "new_value": new_item.get("desc"),
                "change_percent": None,
                "message": f"{name} 描述变化",
            })

        for field, label in (
            ("status", "状态"),
            ("is_exclusive", "专属分组"),
            ("subscription_type", "订阅类型"),
            ("rpm_limit", "RPM 限制"),
            ("platform", "平台"),
        ):
            if field in old_item or field in new_item:
                if old_item.get(field) != new_item.get(field):
                    changes.append({
                        "change_type": f"{field}_changed",
                        "group_name": name,
                        "old_value": old_item.get(field),
                        "new_value": new_item.get(field),
                        "change_percent": None,
                        "message": (
                            f"{name} {label}变化：{old_item.get(field)} "
                            f"-> {new_item.get(field)}"
                        ),
                    })

        old_models = old_item.get("models")
        new_models = new_item.get("models")
        if isinstance(old_models, list) and isinstance(new_models, list):
            old_model_set = {str(model).strip() for model in old_models if str(model).strip()}
            new_model_set = {str(model).strip() for model in new_models if str(model).strip()}
            for model_name in sorted(new_model_set - old_model_set):
                changes.append({
                    "change_type": "model_added_to_group",
                    "group_name": name,
                    "old_value": None,
                    "new_value": model_name,
                    "change_percent": None,
                    "message": f"{name} 上架模型 {model_name}",
                })
            for model_name in sorted(old_model_set - new_model_set):
                changes.append({
                    "change_type": "model_removed_from_group",
                    "group_name": name,
                    "old_value": model_name,
                    "new_value": None,
                    "change_percent": None,
                    "message": f"{name} 下架模型 {model_name}",
                })

    return changes


__all__ = ["diff_groups"]
