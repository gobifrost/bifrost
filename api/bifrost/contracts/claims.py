"""Minimal CLI-side mirror of custom claim DTOs."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel


class CustomClaimCreate(BaseModel):
    """Input for creating a custom claim (CLI mirror)."""

    name: str
    description: str | None = None
    type: Literal["list", "scalar"] = "list"
    query: dict[str, Any]


class CustomClaimUpdate(BaseModel):
    """Input for updating a custom claim (CLI mirror)."""

    description: str | None = None
    type: Literal["list", "scalar"] | None = None
    query: dict[str, Any] | None = None
