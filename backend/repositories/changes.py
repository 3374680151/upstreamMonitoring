"""Change/snapshot repository: reads and diff helpers for ``changes``/``snapshots``.

Functions moved out of ``backend.legacy_runtime``.  The legacy runtime
re-exports every public name below so existing ``legacy.*`` callers (and the
``from app import diff_groups`` test helper) keep working unchanged.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from backend.core.normalize import format_change_value
from backend.db.connection import db_execute, db_query_all, db_query_one


def list_snapshots(site_id: int) -> List[Dict[str, Any]]:
    return db_query_all(
        """
        SELECT * FROM snapshots
        WHERE site_id = ?
        ORDER BY id DESC
        LIMIT 100
        """,
        (site_id,),
    )


def list_changes(limit: int = 100) -> List[Dict[str, Any]]:
    return db_query_all(
        "SELECT * FROM changes ORDER BY id DESC LIMIT ?",
        (limit,),
    )


def list_site_changes(site_id: int, limit: int = 100) -> List[Dict[str, Any]]:
    return db_query_all(
        """
        SELECT * FROM changes
        WHERE site_id = ?
        ORDER BY id DESC
        LIMIT ?
        """,
        (site_id, limit),
    )


def get_last_success_snapshot(site_id: int) -> Optional[Dict[str, Any]]:
    return db_query_one(
        """
        SELECT * FROM snapshots
        WHERE site_id = ? AND status = 'success'
        ORDER BY id DESC
        LIMIT 1
        """,
        (site_id,),
    )


def diff_groups(old_groups: Dict[str, Dict[str, Any]], new_groups: Dict[str, Dict[str, Any]]) -> List[Dict[str, Any]]:
    changes: List[Dict[str, Any]] = []
    old_names = set(old_groups.keys())
    new_names = set(new_groups.keys())

    for name in sorted(new_names - old_names):
        new_item = new_groups[name]
        message = f"新增分组 {name}"
        if new_item.get("ratio") is not None:
            message += f" · 倍率 {format_change_value(new_item)}"
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
            if isinstance(old_ratio, (int, float)) and isinstance(new_ratio, (int, float)) and old_ratio != 0:
                change_percent = round((float(new_ratio) - float(old_ratio)) / float(old_ratio) * 100, 2)

            if isinstance(old_ratio, (int, float)) and isinstance(new_ratio, (int, float)):
                message = f"{name} 倍率 {old_ratio} -> {new_ratio}"
            else:
                message = f"{name} 倍率 {old_ratio} -> {new_ratio}"

            changes.append({
                "change_type": "ratio_changed",
                "group_name": name,
                "old_value": old_item,
                "new_value": new_item,
                "change_percent": change_percent,
                "message": message,
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
                        "message": f"{name} {label}变化：{old_item.get(field)} -> {new_item.get(field)}",
                    })

        # 模型上/下架：仅当新旧快照都带有 models 名单时才比较，避免误报整组增删
        old_models = old_item.get("models")
        new_models = new_item.get("models")
        if isinstance(old_models, list) and isinstance(new_models, list):
            old_model_set = {str(m).strip() for m in old_models if str(m).strip()}
            new_model_set = {str(m).strip() for m in new_models if str(m).strip()}
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


def persist_snapshot(
    site_id: int,
    *,
    status: str,
    source: str,
    groups_json: Optional[str] = None,
    raw_json: Optional[str] = None,
    hash_value: Optional[str] = None,
    error_message: Optional[str] = None,
    checked_at: str,
) -> None:
    """Insert a snapshot row for either a successful or failed detection.

    Unified over the two ``INSERT INTO snapshots`` shapes that used to live
    inline in ``detect_site``: a failure passes ``groups_json=None`` /
    ``hash_value=None`` while a success supplies both.
    """
    db_execute(
        """
        INSERT INTO snapshots (site_id, status, source, groups_json, raw_json, hash, error_message, checked_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (site_id, status, source, groups_json, raw_json, hash_value, error_message, checked_at),
    )


class ChangeRepository:
    """Thin OO facade retained for callers that prefer object-style access."""

    def list(self, limit: int = 100) -> List[Dict[str, Any]]:
        return list_changes(limit)

    def list_for_site(self, site_id: int, limit: int = 100) -> List[Dict[str, Any]]:
        return list_site_changes(site_id, limit)

    def snapshots_for_site(self, site_id: int) -> List[Dict[str, Any]]:
        return list_snapshots(site_id)
