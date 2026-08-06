"""Small shared API schema primitives."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict


class CompatibilityModel(BaseModel):
    """Allow upstream platform fields to pass through unchanged."""

    model_config = ConfigDict(extra="allow")


class SuccessResponse(BaseModel):
    success: bool
    message: str | None = None
    data: Any | None = None
