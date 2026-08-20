"""Channel-key to upstream-group matching.

This service owns the cross-platform matching flow that used to live in the
legacy HTTP runtime.  It deliberately keeps database writes in repositories
and upstream requests in integration clients.
"""

from __future__ import annotations

from typing import Any, Callable, Optional

from backend.core.sanitize import is_masked_key
from backend.core.time import utc_now_iso
from backend.integrations.clients import NewApiClient as NewApiAdminClient
from backend.integrations.newapi import NewApiClient as NewApiUserClient
from backend.integrations.newapi import login_password
from backend.integrations.newapi_admin import parse_groups_payload
from backend.integrations.sub2api import (
    Sub2ApiClient as Sub2ApiUserClient,
    classify_auth_failure,
    parse_sub2api_groups,
)
from backend.integrations.transport import normalize_base_url
from backend.repositories.admin_sites import (
    ChannelKeyCacheRepository,
    ChannelUpstreamBindingRepository,
)
from backend.repositories.sites import SiteRepository


def _split_channel_groups(value: Any) -> list[str]:
    raw = value if isinstance(value, list) else str(value or "").replace("\n", ",").split(",")
    return [str(item).strip() for item in raw if str(item).strip()]


def _sub2api_key_group_name(
    key_item: dict[str, Any], groups: dict[str, dict[str, Any]]
) -> str:
    group_info = key_item.get("group") if isinstance(key_item.get("group"), dict) else {}
    for candidate in (
        group_info.get("name"),
        group_info.get("id"),
        key_item.get("group_name"),
        key_item.get("group_id"),
    ):
        value = str(candidate or "").strip()
        if not value:
            continue
        if value in groups:
            return value
        for name, info in groups.items():
            if isinstance(info, dict) and str(info.get("id") or "").strip() == value:
                return name
    fallback = str(group_info.get("name") or key_item.get("group_name") or "").strip()
    if fallback:
        return fallback
    fallback_id = group_info.get("id") or key_item.get("group_id")
    return f"分组 #{fallback_id}" if fallback_id not in (None, "") else ""


PersistAuth = Callable[[dict[str, Any], str, str, bool], None]
FetchWithToken = Callable[[str, str], tuple[bool, dict[str, Any], Optional[str]]]


class _Sub2ApiMatchSession:
    """One upstream user session shared by group and key lookups."""

    def __init__(
        self, source: dict[str, Any], persist: PersistAuth
    ) -> None:
        self.source = source
        self.persist = persist
        self.client = Sub2ApiUserClient()
        self.base_url = str(source.get("base_url") or "")
        self.mode = str(source.get("auth_mode") or "token").strip().lower()
        self.access_token = str(source.get("access_token") or "").strip()
        self.refresh_token = str(source.get("refresh_token") or "").strip()
        self.username = str(source.get("login_username") or "").strip()
        self.password = str(source.get("login_password") or "")
        self._password_logged_in = False

    def _login(self) -> tuple[bool, Optional[str]]:
        ok, auth, error = self.client.login(self.base_url, self.username, self.password)
        if not ok:
            return False, error or "登录失败"
        access_token = str(auth.get("access_token") or "").strip()
        if not access_token:
            return False, "登录成功但没有返回 access_token"
        self.access_token = access_token
        self.refresh_token = str(auth.get("refresh_token") or self.refresh_token or "").strip()
        self._password_logged_in = True
        return True, None

    def _refresh(self, expected_access: str, expected_refresh: str) -> tuple[bool, Optional[str]]:
        if not self.refresh_token:
            return False, "refresh_token 为空"
        ok, auth, error = self.client.refresh(self.base_url, self.refresh_token)
        if not ok:
            return False, error or "登录态刷新失败"
        access_token = str(auth.get("access_token") or "").strip()
        if not access_token:
            return False, "刷新成功但没有返回 access_token"
        refreshed = {
            "access_token": access_token,
            "refresh_token": str(auth.get("refresh_token") or self.refresh_token).strip(),
            "expires_in": auth.get("expires_in"),
        }
        self.access_token = refreshed["access_token"]
        self.refresh_token = refreshed["refresh_token"]
        # Password-mode matching is deliberately ephemeral.  Storing a token
        # obtained from its one-shot login would contradict the configured
        # credential source and its CAS cursor is not a stored session.
        if self.mode in {"token", "browser"}:
            self.persist(
                refreshed,
                expected_access,
                expected_refresh,
                self.mode == "browser",
            )
        return True, None

    def _login_and_fetch(
        self, fetcher: FetchWithToken
    ) -> tuple[bool, dict[str, Any], Optional[str]]:
        logged_in, login_error = self._login()
        if not logged_in:
            return False, {"login": {}}, login_error
        return fetcher(self.base_url, self.access_token)

    def fetch(
        self, fetcher: FetchWithToken
    ) -> tuple[bool, dict[str, Any], Optional[str]]:
        if self.mode == "password":
            if not self._password_logged_in:
                ok, payload, error = self._login_and_fetch(fetcher)
            else:
                ok, payload, error = fetcher(self.base_url, self.access_token)
            if ok or classify_auth_failure(payload, error) != "auth":
                return ok, payload, error
            previous_access = self.access_token
            previous_refresh = self.refresh_token
            if self.refresh_token:
                refreshed, _refresh_error = self._refresh(
                    previous_access, previous_refresh
                )
                if refreshed:
                    ok, payload, error = fetcher(self.base_url, self.access_token)
                    if ok or classify_auth_failure(payload, error) != "auth":
                        return ok, payload, error
            # Matching used to log in independently for group and key reads.
            # With a shared session, retain that recovery path when a freshly
            # logged-in or refreshed token is rejected by the next endpoint.
            return self._login_and_fetch(fetcher)
        if self.mode not in {"token", "browser"}:
            return False, {}, "auth_mode invalid"

        previous_access = self.access_token
        previous_refresh = self.refresh_token
        if self.access_token:
            ok, payload, error = fetcher(self.base_url, self.access_token)
            if ok:
                return True, payload, None
            if classify_auth_failure(payload, error) != "auth":
                return False, payload, error

        if self.refresh_token:
            refreshed, refresh_error = self._refresh(previous_access, previous_refresh)
            if refreshed:
                ok, payload, error = fetcher(self.base_url, self.access_token)
                if ok:
                    return True, payload, None
                if classify_auth_failure(payload, error) != "auth":
                    return False, payload, error
                # The refresh write won its CAS before this request.  A
                # browser/password fallback must use that rotated pair as its
                # next CAS cursor, otherwise it can never persist its result.
                previous_access = self.access_token
                previous_refresh = self.refresh_token
            elif self.mode == "token":
                return False, {"refresh": {}}, refresh_error or "登录态刷新失败"

        if self.mode == "token":
            return False, {}, "登录态已过期"
        if not self.username or not self.password:
            return False, {}, "请先在浏览器登录并同步"
        ok, payload, error = self._login_and_fetch(fetcher)
        if ok:
            self.persist(
                {
                    "access_token": self.access_token,
                    "refresh_token": self.refresh_token,
                },
                previous_access,
                previous_refresh,
                True,
            )
        return ok, payload, error


class ChannelMatchService:
    """Match one NewAPI main-site channel against its upstream user groups."""

    def __init__(
        self,
        keys: ChannelKeyCacheRepository | None = None,
        bindings: ChannelUpstreamBindingRepository | None = None,
        sites: SiteRepository | None = None,
    ) -> None:
        self.keys = keys or ChannelKeyCacheRepository()
        self.bindings = bindings or ChannelUpstreamBindingRepository()
        self.sites = sites or SiteRepository()

    def _find_monitor_site(self, base_url: str) -> Optional[dict[str, Any]]:
        normalized = normalize_base_url(base_url)
        if not normalized:
            return None
        for site in self.sites.list_enabled():
            if normalize_base_url(str(site.get("base_url") or "")) == normalized:
                return site
        return None

    @staticmethod
    def _verification_guidance(error: Optional[str]) -> Optional[str]:
        text = str(error or "")
        markers = (
            "尚未完成 key 读取安全验证",
            "安全验证 proof 已失效",
            "key 读取安全验证已过期",
            "网页登录 Session 已过期",
            "网页登录 Session 已失效",
            "网页登录 Session 或安全验证",
            "网页登录需要 2FA",
            "需要重新完成 2FA",
        )
        if not any(marker in text for marker in markers):
            return None
        return (
            "渠道运行正常，但本地尚未保存该渠道 key。请点击“编辑主站”，"
            "输入当前 2FA 验证码完成一次 key 读取安全验证。"
            "验证成功后会保存渠道 key，后续查询将优先复用；"
            "缓存缺失或验证状态失效时仍需重新验证。"
        )

    @staticmethod
    def _result_payload(
        upstream: dict[str, Any],
        *,
        inherited_from_monitor: bool,
        status: str,
        message: str,
        matched_groups: list[dict[str, Any]],
        matched_at: Optional[str],
    ) -> dict[str, Any]:
        return {
            "configured": True,
            "inherited_from_monitor": inherited_from_monitor,
            "upstream_base_url": str(upstream.get("base_url") or ""),
            "upstream_platform": str(upstream.get("platform") or "newapi"),
            "auth_mode": str(upstream.get("auth_mode") or "password"),
            "has_login_username": bool(upstream.get("login_username")),
            "has_login_password": bool(upstream.get("login_password")),
            "has_access_token": bool(upstream.get("access_token")),
            "has_refresh_token": bool(upstream.get("refresh_token")),
            "access_user_id": upstream.get("access_user_id") or "",
            "match_status": status,
            "match_message": message,
            "matched_groups": matched_groups,
            "matched_at": matched_at,
        }

    def _persist(
        self,
        admin_site_id: int,
        channel_id: int,
        status: str,
        message: str,
        groups: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        return self.bindings.persist_match_result(
            admin_site_id, channel_id, status, message, groups
        )

    def _verification_payload(
        self,
        upstream: dict[str, Any],
        binding: Optional[dict[str, Any]],
        inherited_from_monitor: bool,
        message: str,
        matched_groups: list[dict[str, Any]],
    ) -> dict[str, Any]:
        return self._result_payload(
            upstream,
            inherited_from_monitor=inherited_from_monitor,
            status="needs_key_verification",
            message=message,
            matched_groups=matched_groups,
            matched_at=binding.get("matched_at") if binding else None,
        )

    def _sub2api_session(
        self,
        admin_site_id: int,
        channel_id: int,
        upstream: dict[str, Any],
        inherited_from_monitor: bool,
        monitor_site: Optional[dict[str, Any]],
    ) -> _Sub2ApiMatchSession:
        def persist(
            auth: dict[str, Any],
            expected_access_token: str,
            expected_refresh_token: str,
            restore_browser_session: bool,
        ) -> None:
            if inherited_from_monitor and monitor_site:
                try:
                    monitor_id = int(monitor_site.get("id") or 0)
                except (TypeError, ValueError):
                    monitor_id = 0
                if monitor_id > 0:
                    self.sites.persist_sub2api_refreshed_auth(
                        monitor_id,
                        auth,
                        expected_access_token=expected_access_token,
                        expected_refresh_token=expected_refresh_token,
                        restore_browser_session=restore_browser_session,
                    )
                return
            self.bindings.persist_refreshed_auth(
                admin_site_id,
                channel_id,
                auth,
                expected_access_token=expected_access_token,
                expected_refresh_token=expected_refresh_token,
            )

        return _Sub2ApiMatchSession(upstream, persist)

    def match(
        self,
        site: dict[str, Any],
        channel_id: int,
        force_refresh: bool = False,
    ) -> tuple[bool, dict[str, Any], Optional[str]]:
        admin_site_id = int(site["id"])
        channel_id = int(channel_id)
        binding = self.bindings.get(admin_site_id, channel_id)
        detail_ok, detail_payload, _detail_error = NewApiAdminClient(site=site).get_channel(
            channel_id
        )
        detail = detail_payload.get("data") if detail_ok and isinstance(detail_payload, dict) else {}
        detail = detail if isinstance(detail, dict) else {}

        monitor_site = self._find_monitor_site(str(detail.get("base_url") or ""))
        has_binding = bool(binding and binding.get("upstream_base_url"))
        source = binding if has_binding else monitor_site
        source_base_url = (
            str(source.get("upstream_base_url") or source.get("base_url") or "")
            if source
            else ""
        )
        if not source or not source_base_url:
            return True, {
                "configured": False,
                "match_status": "unmatched",
                "match_message": "未配置对应的上游登录态，请优先配置渠道",
                "matched_groups": [],
            }, None

        inherited_from_monitor = not has_binding
        if has_binding:
            upstream = {
                "id": 0,
                "base_url": source_base_url,
                "platform": str(binding.get("upstream_platform") or "newapi").strip().lower(),
                "auth_mode": binding.get("auth_mode") or "token",
                "login_username": binding.get("login_username") or "",
                "login_password": binding.get("login_password") or "",
                "access_token": binding.get("access_token") or "",
                "access_user_id": binding.get("access_user_id") or "",
                "refresh_token": binding.get("refresh_token") or "",
            }
        else:
            upstream = dict(monitor_site or {})
            upstream["base_url"] = source_base_url
            upstream["platform"] = str(upstream.get("platform") or "newapi").strip().lower()

        platform = str(upstream.get("platform") or "newapi").strip().lower()
        if (
            platform == "newapi"
            and inherited_from_monitor
            and (not upstream.get("access_token") or not upstream.get("access_user_id"))
        ):
            return True, {
                "configured": False,
                "inherited_from_monitor": True,
                "upstream_base_url": source_base_url,
                "upstream_platform": platform,
                "match_status": "unmatched",
                "match_message": "同 Base URL 的渠道监控未配置 NewAPI 普通用户认证，请优先配置渠道",
                "matched_groups": [],
            }, None

        channel_key = ""
        key_error: Optional[str] = None
        if not force_refresh:
            channel_key = self.keys.get(admin_site_id, channel_id)
        if not channel_key and not force_refresh and binding and not is_masked_key(
            binding.get("channel_key")
        ):
            channel_key = str(binding.get("channel_key") or "").strip()
            self.keys.upsert(admin_site_id, channel_id, channel_key)
        if not channel_key and not is_masked_key(detail.get("key")):
            channel_key = str(detail.get("key") or "").strip()
            self.keys.upsert(admin_site_id, channel_id, channel_key)
        if not channel_key:
            key_ok, main_site_key, key_error = NewApiAdminClient(site=site).get_channel_key(
                channel_id, force_refresh=force_refresh
            )
            if key_ok:
                channel_key = main_site_key

        if platform == "newapi":
            if str(upstream.get("auth_mode") or "").strip().lower() == "password":
                login_ok, session, login_error = login_password(
                    source_base_url,
                    str(upstream.get("login_username") or "").strip(),
                    str(upstream.get("login_password") or ""),
                )
                if not login_ok:
                    message = login_error or "上游 NewAPI 用户名密码登录失败"
                    status = "needs_key_verification" if session.get("requires_2fa") else "error"
                    stale_groups = self._persist(
                        admin_site_id, channel_id, status, message, []
                    )
                    return False, self._verification_payload(
                        upstream,
                        binding,
                        inherited_from_monitor,
                        message,
                        stale_groups,
                    ), message
                upstream.update(session)
                upstream["auth_mode"] = "browser"
                upstream["id"] = 0

            if not channel_key:
                guidance = self._verification_guidance(key_error)
                if guidance:
                    stale_groups = self._persist(
                        admin_site_id,
                        channel_id,
                        "needs_key_verification",
                        guidance,
                        [],
                    )
                    return True, self._verification_payload(
                        upstream,
                        binding,
                        inherited_from_monitor,
                        guidance,
                        stale_groups,
                    ), None
                message = f"主站渠道 key 读取失败：{key_error or '未返回真实 key'}"
                self._persist(admin_site_id, channel_id, "missing_key", message, [])
                return False, {}, message
            if not upstream.get("access_token") or not upstream.get("access_user_id"):
                message = "NewAPI 上游未配置用户认证令牌或用户 ID，无法读取用户 API 密钥列表"
                self._persist(admin_site_id, channel_id, "error", message, [])
                return False, {}, message

            matched, match_error = NewApiUserClient().find_user_token_by_key(
                upstream, channel_key
            )
            if not matched:
                message = match_error or "当前 key 未在上游 NewAPI 用户 API 密钥列表中找到"
                status = (
                    "key_not_found"
                    if message == "当前 key 未在上游 NewAPI 用户 API 密钥列表中找到"
                    else "error"
                )
                self._persist(admin_site_id, channel_id, status, message, [])
                return False, {}, message
            group_names = _split_channel_groups(matched.get("group"))
            if not group_names:
                message = "上游 NewAPI 已找到当前 key，但该用户 API 密钥没有配置分组"
                self._persist(admin_site_id, channel_id, "no_group", message, [])
                return False, {}, message
            groups_ok, groups_payload, groups_error = NewApiUserClient().fetch_groups_for_site(
                upstream
            )
            groups = parse_groups_payload(groups_payload) if groups_ok else {}
            matched_groups = [
                {
                    "name": name,
                    "ratio": (groups.get(name) or {}).get("ratio"),
                    "ratio_type": (groups.get(name) or {}).get("ratio_type") or "text",
                    "desc": (groups.get(name) or {}).get("desc") or "",
                    "available_to_login": name in groups,
                }
                for name in group_names
            ]
            if groups_ok:
                status = "matched" if all(
                    item["available_to_login"] for item in matched_groups
                ) else "matched_partial"
                message = "已按key 精确匹配读取上游分组倍率"
            else:
                status = "refresh_error"
                message = f"已读取分组，但倍率请求失败：{groups_error or '未知错误'}"
                matched_groups = []
        elif platform == "sub2api":
            session = self._sub2api_session(
                admin_site_id,
                channel_id,
                upstream,
                inherited_from_monitor,
                monitor_site,
            )
            groups_ok, groups_payload, groups_error = session.fetch(
                session.client.fetch_groups_by_token
            )
            if not groups_ok:
                message = f"读取 sub2api 登录态分组失败：{groups_error or '未知错误'}"
                self._persist(admin_site_id, channel_id, "error", message, [])
                return False, {}, message
            groups = parse_sub2api_groups(
                groups_payload.get("data"), groups_payload.get("user_rates")
            )
            if not channel_key:
                guidance = self._verification_guidance(key_error)
                if guidance:
                    stale_groups = self._persist(
                        admin_site_id,
                        channel_id,
                        "needs_key_verification",
                        guidance,
                        [],
                    )
                    return True, self._verification_payload(
                        upstream,
                        binding,
                        inherited_from_monitor,
                        guidance,
                        stale_groups,
                    ), None
                if key_error:
                    security_hint = "；请完成主站网页登录和安全验证后刷新" if any(
                        marker in key_error
                        for marker in (
                            "主站需要重新完成 2FA",
                            "主站网页登录需要 2FA",
                            "安全验证状态无效",
                        )
                    ) else ""
                    message = f"主站渠道 key 读取失败：{key_error}{security_hint}"
                else:
                    message = "主站没有返回当前渠道 key，无法查询 sub2api key 所属分组"
                self._persist(admin_site_id, channel_id, "missing_key", message, [])
                return False, {}, message

            keys_ok, keys_payload, keys_error = session.fetch(
                session.client.fetch_keys_by_token
            )
            if not keys_ok:
                message = f"读取 sub2api key 列表失败：{keys_error or '未知错误'}"
                self._persist(admin_site_id, channel_id, "error", message, [])
                return False, {}, message
            key_items = (keys_payload.get("data") or {}).get("items") or []
            key_match = next(
                (
                    item
                    for item in key_items
                    if isinstance(item, dict)
                    and str(item.get("key") or item.get("value") or "").strip()
                    == channel_key
                ),
                None,
            )
            if not isinstance(key_match, dict):
                message = "当前渠道 key 未在 sub2api 登录账号的 key 列表中找到"
                self._persist(admin_site_id, channel_id, "key_not_found", message, [])
                return False, {}, message
            key_group = key_match.get("group") if isinstance(key_match.get("group"), dict) else {}
            group_name = _sub2api_key_group_name(key_match, groups)
            if not group_name:
                message = "sub2api 已找到当前 key，但该 key 没有返回所属分组"
                self._persist(admin_site_id, channel_id, "no_group", message, [])
                return False, {}, message
            group_info = groups.get(group_name) or {}
            ratio = group_info.get("ratio")
            ratio_type = (
                group_info.get("ratio_type") or "text"
                if ratio is not None
                else "number"
                if key_group.get("rate_multiplier") is not None
                else "text"
            )
            matched_groups = [
                {
                    "name": group_name,
                    "ratio": ratio
                    if ratio is not None
                    else key_group.get("rate_multiplier"),
                    "ratio_type": ratio_type,
                    "desc": key_group.get("description") or group_info.get("desc") or "",
                    "available_to_login": group_name in groups,
                }
            ]
            status = "matched" if matched_groups[0]["available_to_login"] else "matched_partial"
            message = "已按key 精确匹配读取 sub2api 分组倍率"
            upstream["access_token"] = session.access_token
            upstream["refresh_token"] = session.refresh_token
        else:
            message = f"暂不支持上游平台：{platform}"
            self._persist(admin_site_id, channel_id, "unsupported", message, [])
            return False, {}, message

        persisted_groups = self._persist(
            admin_site_id, channel_id, status, message, matched_groups
        )
        return True, self._result_payload(
            upstream,
            inherited_from_monitor=inherited_from_monitor,
            status=status,
            message=message,
            matched_groups=persisted_groups,
            matched_at=utc_now_iso(),
        ), None


__all__ = ["ChannelMatchService"]
