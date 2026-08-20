"""Console authentication service."""
from __future__ import annotations

from backend.core import console_auth
from backend.core.errors import AuthenticationError
from backend.core.security import bearer_token


class AuthService:
    def status(self, request) -> dict[str, object]:
        return {
            "success": True,
            "auth_required": console_auth.enabled(),
            "authenticated": (
                not console_auth.enabled()
                or console_auth.sessions.valid(bearer_token(request))
            ),
        }

    def login(self, password: str) -> dict[str, object]:
        """Validate the supplied password and create a console session.

        Mirrors the legacy ``do_POST /api/auth/login`` semantics: when
        no password is configured, success-without-token is returned
        so the UI never has to special-case a blank password.
        """
        if not console_auth.enabled():
            return {"success": True, "auth_required": False, "token": ""}
        if not console_auth.password_matches(password):
            raise AuthenticationError("密码错误")
        token = console_auth.sessions.create()
        return {"success": True, "token": token}

    def logout(self, token: str) -> dict[str, object]:
        console_auth.sessions.drop(token)
        return {"success": True}
