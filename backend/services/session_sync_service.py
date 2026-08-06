"""Browser-session synchronization service facade."""

from __future__ import annotations

from typing import Any

from backend import legacy_runtime as legacy


class SessionSyncService:
    def create_site(self, site_id: int):
        return legacy.create_site_session_sync_request(site_id)

    def complete(self, request_id: str, secret: str, payload: Any):
        return legacy.complete_session_sync_request(request_id, secret, payload)
