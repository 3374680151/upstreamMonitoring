"""Site repository using the existing PyMySQL query helpers."""

from __future__ import annotations

from typing import Any

from backend import legacy_runtime as legacy


class SiteRepository:
    def get(self, site_id: int) -> dict[str, Any] | None:
        return legacy.db_query_one("SELECT * FROM sites WHERE id = ?", (site_id,))

    def list(self) -> list[dict[str, Any]]:
        return legacy.db_query_all("SELECT * FROM sites ORDER BY id DESC")

    def delete(self, site_id: int) -> int:
        return legacy.db_execute_rowcount("DELETE FROM sites WHERE id = ?", (site_id,))
