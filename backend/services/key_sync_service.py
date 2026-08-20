"""Scheduled refresh of protected NewAPI channel keys.

The scheduler owns timing, while this service owns the refresh workflow:
list channels, choose the least-recently fetched key, persist it, and update
the channel-to-upstream match when the key changed.  Protected key transport
is supplied by the session-aware NewAPI integration; the worker itself only
coordinates the service workflow.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Optional

from backend.core.time import app_now, utc_now_iso
from backend.integrations.clients import NewApiClient
from backend.repositories.admin_sites import (
    AdminSiteRepository,
    ChannelKeyCacheRepository,
)
from backend.services.admin_site_service import AdminSiteService


SCAN_INTERVAL_SECONDS = 10
PROOF_BATCH_SIZE = 3


class KeySyncService:
    """Run due admin-site key refreshes and persist scheduler state."""

    def __init__(
        self,
        admins: AdminSiteRepository | None = None,
        keys: ChannelKeyCacheRepository | None = None,
        admin_service: AdminSiteService | None = None,
    ) -> None:
        self.admins = admins or AdminSiteRepository()
        self.keys = keys or ChannelKeyCacheRepository()
        self.admin_service = admin_service or AdminSiteService(
            admin_repo=self.admins,
            key_cache=self.keys,
        )

    def _refresh_next(self, admin: dict[str, Any]) -> dict[str, Any]:
        admin_site_id = int(admin.get("id") or 0)
        if admin_site_id <= 0:
            return {"success": False, "message": "管理站点 ID 无效"}

        client = NewApiClient(site=admin)
        ok, channels, _meta, error = client.list_channels()
        if not ok:
            return {"success": False, "message": error or "读取主站渠道失败"}

        fetched_at = self.keys.fetched_at_map(admin_site_id)
        candidates: list[int] = []
        for channel in channels:
            if not isinstance(channel, dict):
                continue
            try:
                channel_id = int(channel.get("id") or 0)
            except (TypeError, ValueError):
                continue
            if channel_id > 0:
                candidates.append(channel_id)
        candidates = list(dict.fromkeys(candidates))
        if not candidates:
            return {
                "success": True,
                "batch_remaining": 0,
                "message": "主站暂无可更新 key 的渠道",
            }

        proof_verified_at = str(admin.get("security_proof_verified_at") or "")
        batch_candidates = (
            [
                channel_id
                for channel_id in candidates
                if not fetched_at.get(channel_id)
                or fetched_at[channel_id] < proof_verified_at
            ]
            if proof_verified_at
            else []
        )
        selection = batch_candidates or candidates
        channel_id = min(
            selection,
            key=lambda value: (
                bool(fetched_at.get(value)),
                fetched_at.get(value, ""),
                value,
            ),
        )

        previous = self.keys.get(admin_site_id, channel_id)
        key_ok, key, key_error = client.get_channel_key(
            channel_id, force_refresh=True
        )
        if not key_ok:
            return {
                "success": False,
                "channel_id": channel_id,
                "message": key_error or "读取渠道 key 失败",
            }
        self.keys.upsert(admin_site_id, channel_id, key)

        changed = key != previous
        match_result: dict[str, Any] = {}
        if changed:
            try:
                match_result = self.admin_service.match_channel(
                    admin_site_id, channel_id, force_refresh=False
                )
            except Exception:
                # A key refresh is still useful when the optional upstream
                # matching request is temporarily unavailable.
                match_result = {}
        match_payload = match_result.get("data") if isinstance(match_result, dict) else {}
        if not isinstance(match_payload, dict):
            match_payload = {}
        match_status = str(match_payload.get("match_status") or "")
        match_success = bool(match_result.get("success")) and match_status in {
            "matched",
            "matched_partial",
        }
        return {
            "success": True,
            "channel_id": channel_id,
            "changed": changed,
            "batch_remaining": max(0, len(batch_candidates) - 1),
            "fetched_at": utc_now_iso(),
            "match_success": match_success,
            "match_message": match_payload.get("match_message")
            or (None if match_success else "未匹配到上游分组倍率"),
        }

    def refresh_one(self, admin: dict[str, Any]) -> dict[str, Any]:
        """Refresh one eligible channel key after an explicit 2FA proof."""
        return self._refresh_next(admin)

    @staticmethod
    def _failure_delay_minutes(message: str, failures: int, interval: int) -> int:
        text = str(message or "")
        if "429" in text or "限流" in text:
            return (1, 2, 5, 15, 30)[min(max(0, failures - 1), 4)]
        if any(marker in text for marker in ("安全验证", "2FA", "proof")):
            return 30
        return min(30, max(interval, 5))

    def run_due(self, now: Optional[datetime] = None) -> None:
        current = now or app_now()
        now_iso = current.isoformat(timespec="seconds")
        for admin in self.admins.list_due_key_syncs(now_iso):
            result = self._refresh_next(admin)
            attempts = 1
            while (
                result.get("success")
                and int(result.get("batch_remaining") or 0) > 0
                and attempts < PROOF_BATCH_SIZE
            ):
                result = self._refresh_next(admin)
                attempts += 1

            try:
                interval = max(
                    5,
                    min(1440, int(admin.get("key_sync_interval_minutes") or 5)),
                )
            except (TypeError, ValueError):
                interval = 5

            if result.get("success"):
                batch_remaining = int(result.get("batch_remaining") or 0)
                next_at = (
                    current + timedelta(seconds=SCAN_INTERVAL_SECONDS)
                    if batch_remaining > 0
                    else current + timedelta(minutes=interval)
                ).isoformat(timespec="seconds")
                self.admins.mark_key_sync_success(admin["id"], now_iso, next_at)
                continue

            message = str(result.get("message") or "渠道 key 自动更新失败")
            try:
                failures = int(admin.get("key_sync_failure_count") or 0) + 1
            except (TypeError, ValueError):
                failures = 1
            delay_minutes = self._failure_delay_minutes(message, failures, interval)
            backoff_until = (
                current + timedelta(minutes=delay_minutes)
            ).isoformat(timespec="seconds")
            self.admins.mark_key_sync_failure(
                admin["id"],
                now_iso,
                backoff_until,
                message,
                failures,
            )


__all__ = ["KeySyncService"]
