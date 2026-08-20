"""Small shared API schema primitives."""

from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, ConfigDict


class CompatibilityModel(BaseModel):
    """Allow upstream platform fields to pass through unchanged."""

    model_config = ConfigDict(extra="allow")


class SuccessResponse(BaseModel):
    success: bool
    # Keep schema imports compatible with the project's supported Python 3.9
    # runtime.  Pydantic evaluates postponed ``X | None`` annotations while
    # building model fields, which is not supported by that interpreter.
    message: Optional[str] = None
    data: Optional[Any] = None
