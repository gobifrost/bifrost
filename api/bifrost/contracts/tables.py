"""Minimal CLI-side mirror of table create/update DTOs.

Server-side ``TableBase`` uses ``schema`` as a field name; Pydantic v2
warns because it shadows the ``BaseModel.schema`` attribute. Same
warning-suppression applies here — the field name is part of the wire
contract, so renaming is not an option.
"""

from __future__ import annotations

import warnings
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field


warnings.filterwarnings("ignore", message='Field name "schema"')


class TableCreate(BaseModel):
    """Input for creating a table (CLI mirror)."""

    name: str = Field(max_length=255, pattern=r"^[a-z][a-z0-9_-]*$")
    description: str | None = Field(default=None)
    schema: dict[str, Any] | None = Field(default=None)
    organization_id: UUID | None = Field(default=None)
    policies: dict[str, Any] | None = Field(default=None)


class TableUpdate(BaseModel):
    """Input for updating a table (CLI mirror)."""

    name: str | None = Field(default=None, max_length=255, pattern=r"^[a-z][a-z0-9_-]*$")
    description: str | None = None
    schema: dict[str, Any] | None = None
    policies: dict[str, Any] | None = None
