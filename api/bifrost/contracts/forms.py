"""Minimal CLI-side mirror of form create/update DTOs.

The server-side ``FormCreate`` / ``FormUpdate`` accept either a ``dict``
or a typed ``FormSchema`` for the ``form_schema`` field. The CLI only
needs to detect that the field is a dict to surface it as a
``--form-schema @file.yaml`` flag, so the mirror types ``form_schema`` as
``dict`` (union with ``None`` on the update DTO).
"""

from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel, Field

from bifrost.contracts.enums import FormAccessLevel

DEFAULT_FORM_CONFIRMATION_MARKDOWN = "## Form submitted\n\nThank you!"


class FormCreate(BaseModel):
    """Input for creating a form (CLI mirror)."""

    name: str
    description: str | None = None
    confirmation_markdown: str = DEFAULT_FORM_CONFIRMATION_MARKDOWN
    workflow_id: str | None = None
    launch_workflow_id: str | None = None
    default_launch_params: dict | None = None
    allowed_query_params: list[str] | None = None
    form_schema: dict
    access_level: FormAccessLevel | None = FormAccessLevel.ROLE_BASED
    organization_id: UUID | None = Field(default=None)
    role_ids: list[UUID] = Field(default_factory=list)


class FormUpdate(BaseModel):
    """Input for updating a form (CLI mirror)."""

    name: str | None = None
    description: str | None = None
    confirmation_markdown: str | None = None
    workflow_id: str | None = None
    launch_workflow_id: str | None = None
    default_launch_params: dict | None = None
    allowed_query_params: list[str] | None = None
    form_schema: dict | None = None
    is_active: bool | None = None
    access_level: FormAccessLevel | None = None
    organization_id: UUID | None = Field(default=None)
    clear_roles: bool = False
    role_ids: list[UUID] | None = Field(default=None)
