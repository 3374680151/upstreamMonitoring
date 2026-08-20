"""Platform capability dispatch table.

The strings in :data:`CAPABILITIES` are part of the HTTP contract.  Keep this
table as the single source of truth for both the API schema and the frontend;
service code must use these names instead of platform-specific aliases.
"""

from __future__ import annotations

from typing import FrozenSet


# Capability identifiers are stable strings shared between backend and
# frontend.  Keep the complete list here even when a platform currently does
# not implement a capability; ``capabilities_for`` always emits every key.
CAPABILITIES: tuple[str, ...] = (
    "list_channels",
    "read_channel_detail",
    "edit_channel",
    "toggle_channel",
    "create_channel",
    "delete_channel",
    "batch_channel",
    "channel_key",
    "channel_key_match",
    "channel_key_fetch",
    "key_verification",
    "key_refresh",
    "channel_priority",
    "channel_weight",
    "group_rates",
    "model_pricing",
)


PLATFORM_CAPABILITIES: dict[str, FrozenSet[str]] = {
    # Mirrors legacy ``ADMIN_SITE_CAPABILITIES["newapi"]`` so the channel
    # editor can hide buttons it knows the upstream cannot service.
    "newapi": frozenset(
        {
            "list_channels",
            "read_channel_detail",
            "edit_channel",
            "toggle_channel",
            "create_channel",
            "delete_channel",
            "batch_channel",
            "channel_key",
            "channel_key_match",
            "channel_key_fetch",
            "key_verification",
            "key_refresh",
            "channel_priority",
            "channel_weight",
            "group_rates",
        }
    ),
    "sub2api": frozenset(
        {
            "list_channels",
            "read_channel_detail",
            "edit_channel",
            "toggle_channel",
            "model_pricing",
            "group_rates",
        }
    ),
}


def supports(platform: str, capability: str) -> bool:
    return capability in PLATFORM_CAPABILITIES.get(platform, frozenset())


def capabilities_for(platform: str) -> dict[str, bool]:
    """Return a JSON-serialisable capability map for the frontend.

    All known capabilities are always present; unknown platforms get
    every flag set to ``False`` so the frontend never has to defend
    against ``undefined``.
    """
    owned = PLATFORM_CAPABILITIES.get(platform, frozenset())
    return {cap: (cap in owned) for cap in CAPABILITIES}
