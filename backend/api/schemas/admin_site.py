"""Pydantic schemas for the admin-site / channel API surface.

These models describe the HTTP wire format only.  The service layer works
with plain ``dict`` rows from the repository; the router layer converts
between dict and schema at the boundary.

Two design rules are baked in here:

1. **List endpoints never carry plaintext secrets.** ``ChannelSummary`` exposes
   only ``key_masked`` / ``has_key``; plaintext ``key`` only appears on
   ``ChannelDetail`` and only when the caller asked for ``include_key=True``.
2. **Platform capabilities travel with the admin-site payload.**  Frontend
   decides button visibility from ``site.capabilities.*`` instead of
   hard-coding ``if (site.platform === "sub2api")`` branches.
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Literal, Optional, Union

from pydantic import BaseModel, ConfigDict, Field, StrictInt, conint


class StrictInputModel(BaseModel):
    """Base for request bodies: unknown fields must fail validation.

    The old stdlib handler silently ignored misspelled fields.  FastAPI
    request models are the boundary where that data loss must stop.
    """

    model_config = ConfigDict(extra="forbid")


class ResponseEnvelope(BaseModel):
    """Common successful-response fields without discarding legacy payloads.

    Some admin APIs proxy an upstream success body whose additional fields are
    useful to the existing frontend.  ``extra='allow'`` lets a response model
    document the stable envelope while preserving those fields during the
    migration.
    """

    model_config = ConfigDict(extra="allow")

    success: bool = True
    message: Optional[str] = None


# NewAPI represents channel state with numeric values while sub2api uses the
# explicit strings below.  A shared update endpoint accepts the disjoint union
# and the platform adapter performs the final platform-specific validation.
NewApiChannelStatus = conint(strict=True, ge=1, le=3)
Sub2ApiChannelStatus = Literal["active", "disabled"]
ChannelStatus = Union[NewApiChannelStatus, Sub2ApiChannelStatus]


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class Platform(str, Enum):
    NEWAPI = "newapi"
    SUB2API = "sub2api"


class AuthMode(str, Enum):
    PASSWORD = "password"
    TOKEN = "token"
    BROWSER = "browser"


class MatchStatus(str, Enum):
    UNMATCHED = "unmatched"
    MATCHED = "matched"
    MATCHED_PARTIAL = "matched_partial"
    NEEDS_KEY_VERIFICATION = "needs_key_verification"
    MISSING_KEY = "missing_key"
    KEY_NOT_FOUND = "key_not_found"
    NO_GROUP = "no_group"
    UNSUPPORTED = "unsupported"
    REFRESH_ERROR = "refresh_error"
    ERROR = "error"


class KeyRefreshCode(str, Enum):
    RATE_LIMITED = "rate_limited"
    SECURITY_VERIFICATION_REQUIRED = "security_verification_required"
    KEY_REFRESH_FAILED = "key_refresh_failed"


# ---------------------------------------------------------------------------
# Admin site
# ---------------------------------------------------------------------------


class AdminSiteCapabilities(BaseModel):
    """Per-platform capability map consumed by the frontend.

    All keys are required (so a JSON consumer can rely on presence) and
    default to ``False`` for unknown platforms.
    """

    list_channels: bool = False
    read_channel_detail: bool = False
    edit_channel: bool = False
    toggle_channel: bool = False
    create_channel: bool = False
    delete_channel: bool = False
    batch_channel: bool = False
    channel_key: bool = False
    channel_key_match: bool = False
    channel_key_fetch: bool = False
    key_verification: bool = False
    key_refresh: bool = False
    channel_priority: bool = False
    channel_weight: bool = False
    group_rates: bool = False
    model_pricing: bool = False


class AdminSiteBase(BaseModel):
    name: str = ""
    platform: Platform = Platform.NEWAPI
    base_url: str = ""
    auth_mode: Optional[AuthMode] = None
    login_username: Optional[str] = None
    has_login_password: bool = False
    has_access_token: bool = False
    has_refresh_token: bool = False
    access_user_id: Optional[str] = None
    token_expires_at: Optional[str] = None
    has_sub2api_session: bool = False
    # Redacted browser-session state for the admin-site dialog.  The actual
    # cookie/token material never crosses this response boundary.
    has_browser_session: bool = False
    login_last_error: Optional[str] = None
    login_last_check_at: Optional[str] = None
    has_security_proof: bool = False
    security_proof_verified_at: Optional[str] = None
    key_sync_enabled: bool = False
    key_sync_interval_minutes: int = 5
    key_sync_last_at: Optional[str] = None
    key_sync_next_at: Optional[str] = None
    key_sync_last_error: Optional[str] = None
    key_sync_backoff_until: Optional[str] = None
    key_sync_failure_count: int = 0
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


class AdminSite(AdminSiteBase):
    id: int
    platform_label: str = "NewAPI"
    capabilities: AdminSiteCapabilities = Field(default_factory=AdminSiteCapabilities)


class AdminSiteCreate(StrictInputModel):
    # Used by the edit dialog's connection test to reuse saved credentials.
    # It is ignored by create_site's repository mapping and is never returned.
    admin_site_id: Optional[int] = None
    name: str
    platform: Platform = Platform.NEWAPI
    base_url: str
    access_token: Optional[str] = None
    access_user_id: Optional[str] = None
    login_username: Optional[str] = None
    login_password: Optional[str] = None
    key_sync_enabled: bool = False
    key_sync_interval_minutes: int = 5


class AdminSiteConnectionTest(StrictInputModel):
    """Credentials accepted by the unsaved admin-site connection probe.

    Unlike creation, a connection test has no identity requirement.  The
    optional form-only fields are accepted so the current edit dialog can use
    this endpoint without first reshaping its payload; the service ignores
    values that do not participate in the upstream probe.
    """

    admin_site_id: Optional[int] = None
    platform: Optional[Platform] = None
    base_url: Optional[str] = None
    access_token: Optional[str] = None
    access_user_id: Optional[str] = None
    login_username: Optional[str] = None
    login_password: Optional[str] = None
    name: Optional[str] = None
    key_sync_enabled: Optional[bool] = None
    key_sync_interval_minutes: Optional[int] = None


class AdminSiteUpdate(StrictInputModel):
    # Accepted so a client attempting to change platform receives the stable
    # service-level 409 contract instead of an opaque validation error.
    platform: Optional[Platform] = None
    name: Optional[str] = None
    base_url: Optional[str] = None
    access_token: Optional[str] = None
    access_user_id: Optional[str] = None
    login_username: Optional[str] = None
    login_password: Optional[str] = None
    key_sync_enabled: Optional[bool] = None
    key_sync_interval_minutes: Optional[int] = None
    # platform is intentionally NOT updatable; it is set at create time.
    # The legacy handler returned HTTP 409 when callers tried to flip it.


# Backward-compatible import name used by early migration code.  New routes
# should use the explicit create/update models above.
AdminSiteRequest = AdminSiteCreate


# ---------------------------------------------------------------------------
# Channel
# ---------------------------------------------------------------------------


class ChannelSummary(BaseModel):
    """Channel row returned by the list endpoint.  Key is always masked."""

    id: int
    name: str
    type: Optional[int] = None
    description: Optional[str] = None
    base_url: Optional[str] = None
    models: Optional[str] = None
    group: Optional[str] = None
    group_ids: Optional[list[int]] = None
    weight: Optional[int] = None
    priority: Optional[int] = None
    # NewAPI returns numeric status; sub2api returns active/disabled/error.
    status: Optional[Union[int, str]] = None
    normalized_status: Optional[str] = None
    key_masked: Optional[bool] = None
    has_key: bool = False
    source_platform: Optional[Platform] = None
    groups: Optional[list[dict[str, Any]]] = None  # sub2api rich group refs
    model_mapping: Optional[Union[str, dict[str, Any]]] = None
    model_pricing: Optional[list[dict[str, Any]]] = None
    billing_model_source: Optional[str] = None
    restrict_models: Optional[bool] = None
    features: Optional[Union[str, list[str]]] = None
    features_config: Optional[dict[str, Any]] = None
    apply_pricing_to_account_stats: Optional[bool] = None
    account_stats_pricing_rules: Optional[list[dict[str, Any]]] = None
    capabilities: Optional[dict[str, bool]] = None
    response_time: Optional[float] = None
    test_time: Optional[float] = None
    balance: Optional[float] = None
    used_quota: Optional[float] = None


class ChannelDetail(ChannelSummary):
    """Channel row returned by the detail endpoint.  ``key`` may be plaintext."""

    key: Optional[str] = None
    key_error: Optional[str] = None
    tag: Optional[str] = None
    test_model: Optional[str] = None
    auto_ban: Optional[int] = None
    group: Optional[str] = None  # already on summary, repeated for clarity
    group_names: Optional[list[str]] = None
    channel_key: Optional[str] = None


# ``Channel`` is the public response name used by the migration plan.  Keep
# the more explicit summary/detail classes for callers that need to state the
# masking boundary, while exposing a stable unified type for OpenAPI clients.
Channel = ChannelDetail


class ChannelListResponse(ResponseEnvelope):
    data: list[ChannelSummary] = Field(default_factory=list)
    meta: dict[str, Any] = Field(default_factory=dict)


class ChannelDetailResponse(ResponseEnvelope):
    data: Optional[ChannelDetail] = None
    key_error: Optional[str] = None


class ChannelMutationResponse(ResponseEnvelope):
    """Successful passthrough response from an upstream channel mutation."""

    id: Optional[int] = None
    data: Any = None
    code: Optional[str] = None
    upstream: Any = None


class ChannelCreate(StrictInputModel):
    """Payload accepted by POST /admin/sites/{id}/channels.

    Only NewAPI supports creation through this surface; sub2api returns 405.
    """

    name: str
    type: StrictInt = 1
    key: str = ""
    base_url: str = ""
    models: str = ""
    group: str = "default"
    weight: StrictInt = 1
    priority: StrictInt = 0
    status: NewApiChannelStatus = 1
    model_mapping: Optional[Union[str, dict[str, dict[str, str]]]] = None
    tag: str = ""
    test_model: str = ""
    auto_ban: StrictInt = 1


class ChannelUpdate(StrictInputModel):
    """PATCH-shaped update for a single channel.

    Empty / null fields mean "do not touch".  The service layer enforces
    the field allowlist for each platform.
    """

    name: Optional[str] = None
    type: Optional[StrictInt] = None
    key: Optional[str] = None
    base_url: Optional[str] = None
    models: Optional[str] = None
    group: Optional[str] = None
    weight: Optional[StrictInt] = None
    priority: Optional[StrictInt] = None
    status: Optional[ChannelStatus] = None
    model_mapping: Optional[Union[str, dict[str, dict[str, str]]]] = None
    tag: Optional[str] = None
    test_model: Optional[str] = None
    auto_ban: Optional[StrictInt] = None
    # sub2api rich fields.  These names mirror the upstream PUT allowlist;
    # empty arrays/objects are valid updates and therefore remain optional
    # rather than being filtered by truthiness.
    description: Optional[str] = None
    group_ids: Optional[list[int]] = None
    model_pricing: Optional[list[dict[str, Any]]] = None
    billing_model_source: Optional[Literal["requested", "upstream", "channel_mapped"]] = None
    restrict_models: Optional[bool] = None
    features: Optional[Union[str, list[str]]] = None
    features_config: Optional[dict[str, Any]] = None
    apply_pricing_to_account_stats: Optional[bool] = None
    account_stats_pricing_rules: Optional[list[dict[str, Any]]] = None


class ChannelBatchRequest(StrictInputModel):
    action: str  # "enable" | "disable" | "delete" | "set_group" | "set_tag"
    ids: list[int] = Field(default_factory=list)
    group: Optional[str] = None
    tag: Optional[str] = None
    extra: dict[str, Any] = Field(default_factory=dict)


class ChannelBatchItem(BaseModel):
    id: int
    ok: bool
    message: Optional[str] = None


class ChannelBatchResult(BaseModel):
    action: str
    ok_count: int
    fail_count: int
    total: int
    results: list[ChannelBatchItem] = Field(default_factory=list)


class ChannelBatchResponse(ResponseEnvelope):
    data: ChannelBatchResult


# ---------------------------------------------------------------------------
# Channel upstream binding
# ---------------------------------------------------------------------------


class MatchedGroup(BaseModel):
    model_config = ConfigDict(extra="allow")

    name: str = ""
    ratio: Optional[Union[float, str]] = None
    available_to_login: bool = True
    desc: Optional[str] = None


class ChannelUpstreamBinding(BaseModel):
    model_config = ConfigDict(extra="allow")

    # Legacy payloads intentionally omit these IDs for an unconfigured
    # binding.  They remain optional until the service owns that enrichment.
    admin_site_id: Optional[int] = None
    channel_id: Optional[int] = None
    upstream_base_url: Optional[str] = None
    upstream_platform: Optional[Platform] = None
    auth_mode: Optional[AuthMode] = None
    access_user_id: Optional[str] = None
    has_login_username: bool = False
    has_login_password: bool = False
    has_access_token: bool = False
    has_refresh_token: bool = False
    has_channel_key: bool = False
    match_status: MatchStatus = MatchStatus.UNMATCHED
    match_message: Optional[str] = None
    matched_groups: list[MatchedGroup] = Field(default_factory=list)
    configured: bool = False
    inherited_from_monitor: bool = False
    matched_at: Optional[str] = None
    last_attempted_at: Optional[str] = None
    last_success_at: Optional[str] = None


class ChannelUpstreamBindingResponse(ResponseEnvelope):
    data: ChannelUpstreamBinding


class ChannelUpstreamBindingListResponse(ResponseEnvelope):
    data: dict[str, ChannelUpstreamBinding] = Field(default_factory=dict)


class ChannelUpstreamBindingUpdate(StrictInputModel):
    upstream_base_url: Optional[str] = None
    upstream_platform: Optional[Platform] = None
    auth_mode: Optional[AuthMode] = None
    login_username: Optional[str] = None
    login_password: Optional[str] = None
    access_token: Optional[str] = None
    access_user_id: Optional[str] = None
    refresh_token: Optional[str] = None
    channel_key: Optional[str] = None


# ---------------------------------------------------------------------------
# Channel key refresh
# ---------------------------------------------------------------------------


class ChannelKeyRefreshResult(BaseModel):
    channel_id: int
    changed: bool
    first_fetch: bool
    fetched_at: str
    match_success: bool
    match_message: Optional[str] = None
    binding: ChannelUpstreamBinding


class ChannelKeyRefreshResponse(ResponseEnvelope):
    data: ChannelKeyRefreshResult


# ---------------------------------------------------------------------------
# Discovery (channel candidates from a NewAPI admin site)
# ---------------------------------------------------------------------------


class ChannelDiscoveryCandidate(BaseModel):
    base_url: str
    name: str
    channel_ids: list[int] = Field(default_factory=list)
    channel_names: list[str] = Field(default_factory=list)
    channel_count: int = 0
    existing_site_id: Optional[int] = None
    existing_site_auth_mode: Optional[str] = None
    # Non-sensitive local metadata used by the discovery UI to decide whether
    # an imported row may reuse the browser-session bridge.  Keep credentials
    # and browser tokens out of this response.
    existing_site_status: Optional[str] = None
    existing_site_enabled: Optional[bool] = None
    existing_site_session_sync_status: Optional[str] = None
    importable: Optional[bool] = None


class ChannelDiscoveryImportResult(BaseModel):
    base_url: str
    status: str
    site_id: Optional[int] = None
    message: Optional[str] = None


# ---------------------------------------------------------------------------
# 2FA / connection test
# ---------------------------------------------------------------------------


class KeyVerificationRequest(StrictInputModel):
    code: str = ""


class ConnectionTestResult(ResponseEnvelope):
    platform: Platform
    groups_count: int = 0
    channels_count: int = 0


# ---------------------------------------------------------------------------
# Channel groups (group_name -> ratio/desc, for key matching)
# ---------------------------------------------------------------------------


class GroupItem(BaseModel):
    """A single upstream group's ratio metadata, consumed by the frontend.

    Mirrors the shape produced by ``sub2api_admin_groups_payload`` /
    ``parse_groups_payload`` so the legacy contract is preserved.
    """

    model_config = ConfigDict(extra="allow")

    id: Optional[int] = None
    # NewAPI puts the group name in the map key, not in this value object.
    name: Optional[str] = None
    ratio: Optional[Union[float, str]] = None
    rate_multiplier: Optional[Union[float, str]] = None
    ratio_type: Optional[str] = None
    desc: Optional[str] = None
    platform: Optional[str] = None
    status: Optional[str] = None


class ChannelGroupsResponse(ResponseEnvelope):
    data: dict[str, GroupItem] = Field(default_factory=dict)


class AdminSiteListResponse(ResponseEnvelope):
    data: list[AdminSite] = Field(default_factory=list)


class AdminSiteCreateResponse(ResponseEnvelope):
    id: int


class ChannelDiscoveryCandidatesResponse(ResponseEnvelope):
    data: list[ChannelDiscoveryCandidate] = Field(default_factory=list)
    meta: dict[str, Any] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def row_to_admin_site(row: dict[str, Any]) -> AdminSite:
    """Convert a repository row into the schema consumed by the frontend.

    Capability injection lives here so the schema owns the "what does the
    UI need" knowledge, and the repository stays a pure SQL layer.
    """
    from backend.core.capabilities import capabilities_for

    platform_value = (row.get("platform") or "newapi").strip().lower()
    platform = Platform.SUB2API if platform_value == "sub2api" else Platform.NEWAPI
    cap_map = capabilities_for(platform_value)

    return AdminSite(
        id=int(row["id"]),
        name=str(row.get("name") or ""),
        platform=platform,
        platform_label=("sub2api" if platform == Platform.SUB2API else "NewAPI"),
        base_url=str(row.get("base_url") or ""),
        auth_mode=_coerce_auth(row.get("auth_mode")),
        login_username=row.get("login_username") or None,
        has_login_password=bool(row.get("login_password")),
        has_access_token=bool(row.get("access_token")),
        has_refresh_token=bool(row.get("refresh_token")),
        access_user_id=row.get("access_user_id") or None,
        token_expires_at=row.get("token_expires_at") or None,
        has_sub2api_session=bool(
            row.get("sub2api_access_token") and row.get("sub2api_refresh_token")
        ),
        has_browser_session=bool(
            row.get("browser_access_token")
            or row.get("browser_refresh_cookie")
            or row.get("browser_session_id")
        ),
        login_last_error=row.get("browser_login_last_error"),
        login_last_check_at=row.get("browser_login_last_check_at"),
        has_security_proof=bool(row.get("security_proof")),
        security_proof_verified_at=row.get("security_proof_verified_at"),
        key_sync_enabled=bool(row.get("key_sync_enabled")),
        key_sync_interval_minutes=int(row.get("key_sync_interval_minutes") or 5),
        key_sync_last_at=row.get("key_sync_last_at"),
        key_sync_next_at=row.get("key_sync_next_at"),
        key_sync_last_error=row.get("key_sync_last_error"),
        key_sync_backoff_until=row.get("key_sync_backoff_until"),
        key_sync_failure_count=int(row.get("key_sync_failure_count") or 0),
        created_at=row.get("created_at"),
        updated_at=row.get("updated_at"),
        capabilities=AdminSiteCapabilities(**cap_map),
    )


def _coerce_auth(value: Any) -> Optional[AuthMode]:
    if value is None:
        return None
    text = str(value).strip().lower()
    if text == "password":
        return AuthMode.PASSWORD
    if text == "token":
        return AuthMode.TOKEN
    if text == "browser":
        return AuthMode.BROWSER
    return None
