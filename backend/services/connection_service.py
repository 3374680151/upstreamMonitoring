"""Connection / login probe service.

Wraps the legacy probe helpers used by ``/api/check-connection`` and
``/api/check-login`` — these run before any site row exists, so the
Service has no Repository to talk to and no Integration client; it
just relays the probe result to the caller.
"""
from __future__ import annotations

from typing import Any, Tuple

from backend.core.errors import ValidationError
from backend.integrations.newapi import NewApiClient, auth_failure_message
from backend.integrations.newapi_admin import parse_groups_payload
from backend.integrations.sub2api import parse_sub2api_groups
from backend.integrations.transport import normalize_base_url
from backend.services.newapi_site_auth_service import NewApiSiteAuthService
from backend.services.sub2api_auth_service import Sub2ApiSiteAuthService


class ConnectionService:
    def __init__(self) -> None:
        self.newapi = NewApiClient()
        self.newapi_auth = NewApiSiteAuthService()
        self.sub2api_auth = Sub2ApiSiteAuthService()

    def check_connection(self, body: dict[str, Any]) -> dict[str, Any]:
        base_url = normalize_base_url(str(body.get("base_url") or ""))
        platform = str(body.get("platform") or "newapi").strip().lower()
        if not base_url:
            raise ValidationError("base_url required")
        if platform == "sub2api":
            probe_site = {
                **body,
                "id": 0,
                "base_url": base_url,
                "platform": "sub2api",
            }
            ok, payload, error = self.sub2api_auth.fetch_groups(probe_site)
            if not ok:
                return {
                    "success": False,
                    "message": error or "request failed",
                    "groups_count": 0,
                    "groups": {},
                    "raw": payload,
                }
            groups = parse_sub2api_groups(
                payload.get("data") if isinstance(payload, dict) else [],
                payload.get("user_rates") if isinstance(payload, dict) else {},
            )
            return {
                "success": True,
                "message": "ok",
                "groups_count": len(groups),
                "groups": groups,
            }
        ok, payload, error = self.newapi.fetch_groups(base_url)
        if not ok:
            return {
                "success": False,
                "message": error or "request failed",
                "groups_count": 0,
                "groups": {},
                "raw": payload,
            }, 200
        groups = parse_groups_payload(payload)
        return {
            "success": True,
            "message": "ok",
            "groups_count": len(groups),
            "groups": groups,
        }, 200

    def check_login(self, body: dict[str, Any]) -> dict[str, Any]:
        base_url = normalize_base_url(str(body.get("base_url") or ""))
        auth_mode = str(body.get("auth_mode") or "token").strip().lower()
        if auth_mode == "password":
            username = str(body.get("login_username") or "").strip()
            password = str(body.get("login_password") or "")
            verification_code = str(body.get("two_factor_code") or "").strip()
            if not base_url or not username or not password:
                raise ValidationError("Base URL、用户名和密码都需要填写")
            groups_ok, result, groups_error = self.newapi_auth.probe_password_login(
                base_url, username, password, verification_code
            )
            return {
                "success": groups_ok,
                "requires_2fa": bool(result.get("requires_2fa")),
                "message": groups_error or result.get("warning") or "用户名密码验证成功",
                "groups_count": result.get("groups_count", 0),
            }
        access_token = str(body.get("access_token") or "").strip()
        access_user_id = str(body.get("access_user_id") or "").strip()
        if not base_url or not access_token or not access_user_id:
            raise ValidationError("Base URL、系统访问令牌、NewAPI 用户 ID 都需要填写")
        probe_site = {
            **body,
            "id": 0,
            "base_url": base_url,
            "platform": "newapi",
            "auth_mode": "token",
            "access_token": access_token,
            "access_user_id": access_user_id,
        }
        groups_ok, groups_payload, groups_error = self.newapi.fetch_groups_for_site(
            probe_site
        )
        groups = parse_groups_payload(groups_payload) if groups_ok else {}
        return {
            "success": groups_ok,
            "message": (
                auth_failure_message(groups_payload, groups_error)
                if not groups_ok
                else "访问令牌验证成功"
            ),
            "groups_count": len(groups),
            "groups": groups,
        }
