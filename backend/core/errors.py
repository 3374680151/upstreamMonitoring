"""Domain exception types raised by the service layer.

These exceptions carry the structured payload the legacy JSON envelope
expected (``success: False``, ``message``, optional ``code`` / ``upstream``)
so the FastAPI layer can surface them without re-parsing strings.
"""

from __future__ import annotations

from typing import Any, Optional


class DomainError(Exception):
    """Base class for all service-layer business errors.

    Subclasses set ``status_code`` and ``code``; the FastAPI layer maps
    ``status_code`` to HTTP and uses ``code`` for stable client branching.
    """

    status_code: int = 400
    code: str = ""

    def __init__(self, message: str, payload: Optional[dict[str, Any]] = None) -> None:
        super().__init__(message)
        self.message = message
        self.payload = payload or {}

    def to_envelope(self) -> dict[str, Any]:
        body: dict[str, Any] = {"success": False, "message": self.message}
        if self.code:
            body["code"] = self.code
        if self.payload:
            body.update(self.payload)
        return body


class NotFoundError(DomainError):
    status_code = 404
    code = "not_found"


class ValidationError(DomainError):
    status_code = 400
    code = "validation_error"


class AuthenticationError(DomainError):
    status_code = 401
    code = "unauthorized"


class InternalError(DomainError):
    status_code = 500
    code = "internal_error"


class CapabilityError(DomainError):
    """Platform does not support the requested operation (HTTP 405)."""

    status_code = 405
    code = "capability_unsupported"


class ConflictError(DomainError):
    """Mutating a field that is locked (e.g. platform on an existing site)."""

    status_code = 409
    code = "conflict"


class UpstreamError(DomainError):
    """Upstream NewAPI/sub2api call failed (HTTP 502).

    The original upstream payload is preserved so the UI can surface
    provider-specific diagnostics without re-fetching.
    """

    status_code = 502
    code = "upstream_error"

    def __init__(
        self,
        message: str,
        upstream: Optional[dict[str, Any]] = None,
        code: str = "upstream_error",
    ) -> None:
        payload: dict[str, Any] = {}
        if upstream is not None:
            payload["upstream"] = upstream
        super().__init__(message, payload)
        self.code = code
        self.upstream = upstream


class KeyRefreshError(DomainError):
    """A `channel key refresh` request failed with a classified cause.

    ``code`` is one of: ``rate_limited`` (HTTP 429),
    ``security_verification_required`` (HTTP 400),
    ``key_refresh_failed`` (HTTP 400).
    """

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message, {"code": code})
        self.code = code
        if code == "rate_limited":
            self.status_code = 429
        else:
            self.status_code = 400
