"""Admin-site and channel management routes.

This router owns the FastAPI admin-site HTTP boundary.  It is intentionally
thin:

* Pydantic schemas validate input.
* The service layer owns all business rules.
* DomainError subclasses raised by the service layer bubble up to the
  global ``domain_error_handler`` in ``backend.api.exception_handlers``.

Response envelope (``{success, data, message, code?, upstream?}``) is
preserved so the frontend does not need to change during the migration.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query

from backend.api.schemas.admin_site import (
    AdminSiteCreate,
    AdminSiteCreateResponse,
    AdminSiteConnectionTest,
    AdminSiteListResponse,
    AdminSiteUpdate,
    ChannelBatchRequest,
    ChannelBatchResponse,
    ChannelCreate,
    ChannelDetailResponse,
    ChannelDiscoveryCandidatesResponse,
    ChannelGroupsResponse,
    ChannelKeyRefreshResponse,
    ChannelListResponse,
    ChannelMutationResponse,
    ChannelUpdate,
    ChannelUpstreamBindingListResponse,
    ChannelUpstreamBindingResponse,
    ChannelUpstreamBindingUpdate,
    ConnectionTestResult,
    KeyVerificationRequest,
    ResponseEnvelope,
)
from backend.services.admin_site_service import AdminSiteService


router = APIRouter()
service = AdminSiteService()


def _ok(data: Any = None, **extra: Any) -> dict[str, Any]:
    body: dict[str, Any] = {"success": True}
    if data is not None:
        body["data"] = data
    body.update(extra)
    return body


def _payload(model: Any) -> dict[str, Any]:
    return model.model_dump(exclude_unset=True, mode="json")


# ---------------------------------------------------------------------------
# Admin sites
# ---------------------------------------------------------------------------


@router.get(
    "/admin/sites",
    response_model=AdminSiteListResponse,
    response_model_exclude_unset=True,
)
def list_sites() -> dict[str, Any]:
    """GET /api/admin/sites  — list all configured management sites."""
    return _ok(data=service.list_sites())


@router.post(
    "/admin/sites/test",
    response_model=ConnectionTestResult,
    response_model_exclude_unset=True,
)
def test_connection(payload: AdminSiteConnectionTest) -> dict[str, Any]:
    """POST /api/admin/sites/test  — verify connectivity without saving.

    Upstream errors are translated into a 400-shaped JSON envelope by
    the global ``domain_error_handler``; success returns the legacy
    ``{platform, groups_count|channels_count, ...}`` shape.
    """
    return _ok(**service.test_connection(_payload(payload)))


@router.post(
    "/admin/sites",
    response_model=AdminSiteCreateResponse,
    response_model_exclude_unset=True,
)
def create_site(payload: AdminSiteCreate) -> dict[str, Any]:
    """POST /api/admin/sites  — create a new management site."""
    return _ok(id=service.create_site(_payload(payload)))


@router.put(
    "/admin/sites/{admin_site_id}",
    response_model=ResponseEnvelope,
    response_model_exclude_unset=True,
)
def update_site(admin_site_id: int, payload: AdminSiteUpdate) -> dict[str, Any]:
    """PUT /api/admin/sites/{id}  — patchable update.

    Platform is intentionally not in the schema: the legacy 409 path
    lives in the service layer, which raises ``ConflictError``.
    """
    service.update_site(admin_site_id, _payload(payload))
    return _ok()


@router.delete(
    "/admin/sites/{admin_site_id}",
    response_model=ResponseEnvelope,
    response_model_exclude_unset=True,
)
def delete_site(admin_site_id: int) -> dict[str, Any]:
    """DELETE /api/admin/sites/{id}  — atomic teardown.

    Cascades to ``channel_upstream_bindings`` and ``admin_channel_keys``
    inside a transaction (see ``AdminSiteRepository.delete``).
    """
    service.delete_site(admin_site_id)
    return _ok()


@router.post(
    "/admin/sites/{admin_site_id}/key-verification",
    response_model=ResponseEnvelope,
    response_model_exclude_unset=True,
)
def verify_key(admin_site_id: int, payload: KeyVerificationRequest) -> dict[str, Any]:
    """POST /api/admin/sites/{id}/key-verification  — 2FA proof."""
    service.verify_key(admin_site_id, payload.code)
    return _ok(message="主站 key 读取权限已验证")


# ---------------------------------------------------------------------------
# Channel groups
# ---------------------------------------------------------------------------


@router.get(
    "/admin/sites/{admin_site_id}/groups",
    response_model=ChannelGroupsResponse,
    response_model_exclude_unset=True,
)
def list_groups(admin_site_id: int) -> dict[str, Any]:
    """GET /api/admin/sites/{id}/groups  — group_name -> {ratio, desc}.

    Sub2api and NewAPI both go through their integration clients, which return
    the already-normalised payload.
    """
    return service.list_groups(admin_site_id)


# ---------------------------------------------------------------------------
# Channels
# ---------------------------------------------------------------------------


@router.get(
    "/admin/sites/{admin_site_id}/channels",
    response_model=ChannelListResponse,
    response_model_exclude_unset=True,
)
def list_channels(
    admin_site_id: int,
    keyword: str = Query("", max_length=200),
) -> dict[str, Any]:
    """GET /api/admin/sites/{id}/channels  — list channels (key always masked)."""
    return service.list_channels(admin_site_id, keyword)


# Keep the static ``batch`` path ahead of the dynamic ``{channel_id}`` path.
# Starlette matches routes in declaration order; registering this below the
# detail route makes POST /channels/batch try to parse "batch" as an integer.
@router.post(
    "/admin/sites/{admin_site_id}/channels/batch",
    response_model=ChannelBatchResponse,
    response_model_exclude_unset=True,
)
def batch_channels(
    admin_site_id: int, payload: ChannelBatchRequest
) -> dict[str, Any]:
    """POST /api/admin/sites/{id}/channels/batch  — bulk action."""
    return _ok(data=service.batch_channels(
        admin_site_id, payload.action, payload.ids, _payload(payload)
    ))


@router.get(
    "/admin/sites/{admin_site_id}/channels/{channel_id}",
    response_model=ChannelDetailResponse,
    response_model_exclude_unset=True,
)
def get_channel(admin_site_id: int, channel_id: int) -> dict[str, Any]:
    """GET /api/admin/sites/{id}/channels/{cid}  — single channel detail.

    Detail endpoint may carry a plaintext ``key`` if the upstream allows
    (NewAPI only; sub2api always returns the key the user supplied).
    """
    detail = service.get_channel_detail(
        admin_site_id, channel_id, include_key=True
    )
    return _ok(data=detail)


@router.get(
    "/admin/sites/{admin_site_id}/channels/{channel_id}/test",
    response_model=ChannelMutationResponse,
    response_model_exclude_unset=True,
)
def test_channel(admin_site_id: int, channel_id: int) -> dict[str, Any]:
    """GET /api/admin/sites/{id}/channels/{cid}/test  — connectivity test."""
    return _ok(**service.test_channel(admin_site_id, channel_id))


@router.post(
    "/admin/sites/{admin_site_id}/channels",
    response_model=ChannelMutationResponse,
    response_model_exclude_unset=True,
)
def create_channel(
    admin_site_id: int, payload: ChannelCreate
) -> dict[str, Any]:
    """POST /api/admin/sites/{id}/channels  — NewAPI only; sub2api → 405."""
    return _ok(**(service.create_channel(admin_site_id, _payload(payload)) or {}))


@router.put(
    "/admin/sites/{admin_site_id}/channels/{channel_id}",
    response_model=ChannelMutationResponse,
    response_model_exclude_unset=True,
)
def update_channel(
    admin_site_id: int, channel_id: int, payload: ChannelUpdate
) -> dict[str, Any]:
    """PUT /api/admin/sites/{id}/channels/{cid}  — PATCH-shaped update."""
    return _ok(**(service.update_channel(admin_site_id, channel_id, _payload(payload)) or {}))


@router.delete(
    "/admin/sites/{admin_site_id}/channels/{channel_id}",
    response_model=ChannelMutationResponse,
    response_model_exclude_unset=True,
)
def delete_channel(admin_site_id: int, channel_id: int) -> dict[str, Any]:
    """DELETE /api/admin/sites/{id}/channels/{cid}.

    Cascades the local key cache and the upstream binding so the next
    read returns a clean state.  Legacy handler did not do this on
    single-channel delete; the migration fixes it.
    """
    return _ok(**(service.delete_channel(admin_site_id, channel_id) or {}))


# ---------------------------------------------------------------------------
# Channel upstream bindings
# ---------------------------------------------------------------------------


@router.get(
    "/admin/sites/{admin_site_id}/channel-mappings",
    response_model=ChannelUpstreamBindingListResponse,
    response_model_exclude_unset=True,
)
def list_bindings(admin_site_id: int) -> dict[str, Any]:
    """GET /api/admin/sites/{id}/channel-mappings  — all bindings for a site."""
    return _ok(data=service.list_bindings(admin_site_id))


@router.put(
    "/admin/sites/{admin_site_id}/channels/{channel_id}/mapping",
    response_model=ChannelUpstreamBindingResponse,
    response_model_exclude_unset=True,
)
def save_binding(
    admin_site_id: int, channel_id: int, payload: ChannelUpstreamBindingUpdate
) -> dict[str, Any]:
    """PUT /api/admin/sites/{id}/channels/{cid}/mapping  — upsert a binding."""
    return _ok(data=service.save_binding(admin_site_id, channel_id, _payload(payload)))


@router.post(
    "/admin/sites/{admin_site_id}/channels/{channel_id}/match",
    response_model=ChannelUpstreamBindingResponse,
    response_model_exclude_unset=True,
)
def match_channel(
    admin_site_id: int,
    channel_id: int,
    refresh: bool = Query(False),
) -> dict[str, Any]:
    """POST /api/admin/sites/{id}/channels/{cid}/match  — match the key.

    Note: kept as POST because the request triggers upstream traffic
    even when ``refresh=false`` (cache may be empty on first call).
    The response distinguishes "business failure" via ``success=false``
    and always returns the persisted binding so the UI can keep showing
    the last good state.
    """
    return service.match_channel(admin_site_id, channel_id, force_refresh=refresh)


@router.post(
    "/admin/sites/{admin_site_id}/channels/{channel_id}/key/refresh",
    response_model=ChannelKeyRefreshResponse,
    response_model_exclude_unset=True,
)
def refresh_channel_key(admin_site_id: int, channel_id: int) -> dict[str, Any]:
    """POST /api/admin/sites/{id}/channels/{cid}/key/refresh.

    On failure raises one of three classified DomainError subclasses:
    ``KeyRefreshError`` (rate_limited → 429, security_verification_required
    → 400, key_refresh_failed → 400).  The frontend can branch on
    ``code`` instead of grepping the human-readable message.
    """
    return _ok(data=service.refresh_channel_key(admin_site_id, channel_id))


# ---------------------------------------------------------------------------
# Discovery (channel candidates from a NewAPI admin site)
# ---------------------------------------------------------------------------


@router.get(
    "/admin/sites/{admin_site_id}/channel-candidates",
    response_model=ChannelDiscoveryCandidatesResponse,
    response_model_exclude_unset=True,
)
def list_channel_candidates(
    admin_site_id: int,
    keyword: str = Query("", max_length=200),
) -> dict[str, Any]:
    """GET /api/admin/sites/{id}/channel-candidates  — discovery rows."""
    return service.list_channel_candidates(admin_site_id, keyword)
