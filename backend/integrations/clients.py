"""Application-facing adapters for upstream protocol clients.

The concrete HTTP and response handling lives in ``newapi_admin`` and
``sub2api_admin``.  This module keeps the tuple-shaped API consumed by the
service layer and contains two explicitly scoped session adapters:

* NewAPI protected channel-key reads still require browser/session/2FA state;
* sub2api admin token refresh is coordinated by the repository-backed
  ``Sub2ApiAdminSessionService``.

Neither adapter is used for ordinary channel CRUD, pagination, groups, login,
or refresh protocol requests.
"""

from __future__ import annotations

from typing import Any, Callable, Optional, Tuple

from backend.integrations.newapi_admin import (
    NewApiAdminProtocol,
    parse_channel_list,
)
from backend.integrations.sub2api_admin import (
    SessionAdapter,
    Sub2ApiAdminProtocol,
    admin_login,
)
from backend.integrations.transport import normalize_base_url
from backend.repositories.discovery import DiscoveryRepository
from backend.services.sub2api_admin_session_service import (
    ensure_sub2api_admin_session,
)
from backend.services.newapi_protected_key_service import read_newapi_channel_key


class _NewApiKeyAdapter:
    """Session-aware adapter for the protected NewAPI key endpoint."""

    @staticmethod
    def read(
        site: dict[str, Any], channel_id: int, force_refresh: bool = False
    ) -> tuple[bool, str, Optional[str]]:
        return read_newapi_channel_key(
            site, int(channel_id), force_refresh=bool(force_refresh)
        )


class _Sub2ApiSessionAdapter:
    """Database-backed sub2api session refresh adapter."""

    def ensure_session(
        self,
        site: dict[str, Any],
        force_refresh: bool = False,
        rejected_access_token: str = "",
    ) -> tuple[bool, str, Optional[str]]:
        return ensure_sub2api_admin_session(
            site,
            force_refresh=bool(force_refresh),
            rejected_access_token=rejected_access_token,
        )


def _candidate_enricher(
    candidates: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    return DiscoveryRepository().enrich_candidates(candidates)


class NewApiClient:
    """Uniform NewAPI admin client used by ``AdminSiteService``."""

    def __init__(
        self,
        base_url: str = "",
        access_token: str = "",
        access_user_id: str = "",
        site: Optional[dict[str, Any]] = None,
        key_reader: Optional[
            Callable[[dict[str, Any], int, bool], tuple[bool, str, Optional[str]]]
        ] = None,
    ) -> None:
        source = dict(site or {})
        self.base_url = normalize_base_url(source.get("base_url") or base_url or "")
        self.access_token = str(source.get("access_token") or access_token or "")
        self.access_user_id = str(source.get("access_user_id") or access_user_id or "")
        self._site: dict[str, Any] = {
            **source,
            "base_url": self.base_url,
            "access_token": self.access_token,
            "access_user_id": self.access_user_id,
        }
        self._protocol = NewApiAdminProtocol(
            site=self._site,
            key_reader=key_reader or _NewApiKeyAdapter.read,
        )

    def list_channels(
        self, page: int = 0, page_size: int = 100, keyword: str = ""
    ) -> Tuple[bool, list[dict[str, Any]], dict[str, Any], Optional[str]]:
        if keyword:
            ok, payload, error = self._protocol.list_channels(
                page=0, page_size=page_size, keyword=keyword
            )
            if not ok:
                return False, [], {}, error
            items, meta = parse_channel_list(payload)
            return True, items, meta or {"total": len(items)}, None
        ok, items, error = self._protocol.list_all_channels(
            page_size=page_size
        )
        if not ok:
            return False, [], {}, error
        return True, items, {"total": len(items)}, None

    def get_channel(
        self, channel_id: int
    ) -> Tuple[bool, dict[str, Any], Optional[str]]:
        return self._protocol.get_channel(int(channel_id))

    def get_channel_key(
        self, channel_id: int, force_refresh: bool = False
    ) -> Tuple[bool, str, Optional[str]]:
        return self._protocol.get_channel_key(int(channel_id), force_refresh)

    def test_channel(
        self, channel_id: int
    ) -> Tuple[bool, dict[str, Any], Optional[str]]:
        return self._protocol.test_channel(int(channel_id))

    def create_channel(
        self, payload: dict[str, Any]
    ) -> Tuple[bool, dict[str, Any], Optional[str]]:
        return self._protocol.create_channel(payload)

    def update_channel(
        self, channel_id: int, patch: dict[str, Any]
    ) -> Tuple[bool, dict[str, Any], Optional[str]]:
        return self._protocol.update_channel(int(channel_id), patch)

    def delete_channel(
        self, channel_id: int
    ) -> Tuple[bool, dict[str, Any], Optional[str]]:
        return self._protocol.delete_channel(int(channel_id))

    def batch_channel(
        self, action: str, ids: list[int], extra: dict[str, Any]
    ) -> Tuple[bool, dict[str, Any], Optional[str]]:
        return self._protocol.batch_channel(action, [int(item) for item in ids], extra)

    def list_groups(self) -> Tuple[bool, dict[str, Any], Optional[str]]:
        return self._protocol.list_groups()

    def test_connection(self) -> Tuple[bool, dict[str, Any], Optional[str]]:
        return self._protocol.test_connection()

    def channel_candidates(
        self, keyword: str = ""
    ) -> Tuple[bool, list[dict[str, Any]], dict[str, Any], Optional[str]]:
        return self._protocol.channel_candidates(
            keyword=keyword,
            enricher=_candidate_enricher,
        )


class Sub2ApiClient:
    """Uniform sub2api admin client with an injectable session adapter."""

    def __init__(
        self,
        base_url: str = "",
        access_token: str = "",
        refresh_token: str = "",
        site: Optional[dict[str, Any]] = None,
        session_adapter: Optional[SessionAdapter] = None,
    ) -> None:
        source = dict(site or {})
        self.base_url = normalize_base_url(source.get("base_url") or base_url or "")
        self.access_token = str(
            source.get("sub2api_access_token") or access_token or ""
        ).strip()
        self.refresh_token = str(
            source.get("sub2api_refresh_token") or refresh_token or ""
        ).strip()
        self._site: dict[str, Any] = {
            **source,
            "id": source.get("id") or 0,
            "base_url": self.base_url,
            "sub2api_access_token": self.access_token,
            "sub2api_refresh_token": self.refresh_token,
            "sub2api_access_expires_at": source.get("sub2api_access_expires_at"),
            "login_username": source.get("login_username") or "",
            "login_password": source.get("login_password") or "",
        }
        self.access_expires_at = source.get("sub2api_access_expires_at")
        self._session_adapter = session_adapter or _Sub2ApiSessionAdapter()
        self._protocol = Sub2ApiAdminProtocol(
            site=self._site,
            session_adapter=self._session_adapter,
        )

    @classmethod
    def from_login(
        cls, base_url: str, username: str, password: str
    ) -> Tuple[Optional["Sub2ApiClient"], Optional[str]]:
        client, error, _payload = cls.from_login_with_result(base_url, username, password)
        return client, error

    @classmethod
    def from_login_with_result(
        cls, base_url: str, username: str, password: str
    ) -> Tuple[Optional["Sub2ApiClient"], Optional[str], dict[str, Any]]:
        ok, auth, error = admin_login(normalize_base_url(base_url), username, password)
        if not ok:
            return None, error or "sub2api 登录失败", auth if isinstance(auth, dict) else {}
        client = cls(
            base_url=base_url,
            access_token=str(auth.get("access_token") or ""),
            refresh_token=str(auth.get("refresh_token") or ""),
            # A one-shot login has no persisted site row and therefore does
            # not need the database-backed session adapter.
            session_adapter=None,
        )
        client.access_expires_at = auth.get("access_expires_at")
        client._site["sub2api_access_expires_at"] = client.access_expires_at
        client._protocol.site["sub2api_access_expires_at"] = client.access_expires_at
        return client, None, {}

    def list_channels(
        self, keyword: str = ""
    ) -> Tuple[bool, list[dict[str, Any]], dict[str, Any], Optional[str]]:
        if int(self._site.get("id") or 0) > 0:
            return self._protocol.list_site_channels(keyword=keyword)
        if not self.access_token:
            return False, [], {}, "sub2api 未登录"
        ok, items, _meta, error = self._protocol.list_channels(keyword=keyword)
        return ok, items, {}, error

    def list_groups(self) -> Tuple[bool, dict[str, Any], Optional[str]]:
        if int(self._site.get("id") or 0) <= 0:
            return False, {}, "sub2api 主站记录无效"
        return self._protocol.list_groups()

    def get_channel(
        self, channel_id: int
    ) -> Tuple[bool, dict[str, Any], Optional[str]]:
        return self._protocol.get_channel(int(channel_id))

    def update_channel(
        self, channel_id: int, patch: dict[str, Any]
    ) -> Tuple[bool, dict[str, Any], Optional[str]]:
        return self._protocol.update_channel(int(channel_id), patch)

    def test_connection(self) -> Tuple[bool, dict[str, Any], Optional[str]]:
        if int(self._site.get("id") or 0) > 0:
            ok, channels, _meta, error = self._protocol.list_site_channels()
        else:
            ok, channels, _meta, error = self._protocol.list_channels()
        if not ok:
            return False, {"error_source": "upstream", "details": channels}, error
        return True, {"channels_count": len(channels)}, None


__all__ = ["NewApiClient", "Sub2ApiClient"]
