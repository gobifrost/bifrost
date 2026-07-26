"""Pydantic types for the private-Solution builder surface.

These are deliberately separate from ``contracts/solutions.py``: that file is the
administrator install-management contract, while these describe the owner-scoped
private-builder surface (2026-07-25 private-solution-builder spec, Work Package
1). A private Solution never exposes install-management fields such as git
connection state, so it gets its own read shape rather than a widened one.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class PrivateSolutionCreate(BaseModel):
    """Create-shape for a private builder Solution.

    Scope is not an input: a private Solution is always owned by the caller and
    lives in the caller's own organization. ``slug`` is unique per owner, so two
    users in one org may each hold a private Solution at the same slug.
    """

    slug: str = Field(min_length=1, max_length=255)
    name: str = Field(min_length=1, max_length=255)


class PrivateSolutionDTO(BaseModel):
    """Read-shape for a private builder Solution.

    ``promotion_status`` comes from the Solution's builder-project row, not the
    install row, and is flattened here because the builder UI treats the pair as
    one object.
    """

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    slug: str
    name: str
    visibility: str
    owner_user_id: UUID | None = None
    organization_id: UUID | None = None
    status: str
    promotion_status: str
    created_at: datetime
    updated_at: datetime


class PrivateSolutionsList(BaseModel):
    """List envelope for the caller's own private Solutions."""

    solutions: list[PrivateSolutionDTO]
    total: int


class BuilderProjectDTO(BaseModel):
    """Read-shape for a Solution's builder-project row."""

    model_config = ConfigDict(from_attributes=True)

    solution_id: UUID
    current_revision_id: UUID | None = None
    deployed_revision_id: UUID | None = None
    promotion_status: str
    created_at: datetime
    updated_at: datetime


class CreateSessionRequest(BaseModel):
    """Create-shape for a builder chat session.

    Only the title is an input. The Solution comes from the path and the owner
    from the caller, so a session can never be opened against somebody else's
    Solution or on somebody else's behalf.
    """

    title: str | None = Field(default=None, max_length=500)


class BuilderSessionDTO(BaseModel):
    """Read-shape for one builder chat session.

    ``conversation_id`` is the Conversation the chat transcript hangs off; the
    session row is the typed link between that Conversation and the Solution.
    """

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    solution_id: UUID
    conversation_id: UUID
    user_id: UUID
    created_at: datetime
    updated_at: datetime


class BuilderSessionsList(BaseModel):
    """List envelope for the caller's sessions on one Solution."""

    sessions: list[BuilderSessionDTO]
    total: int


class SourceRevisionDTO(BaseModel):
    """Read-shape for one immutable source revision.

    ``is_current`` and ``is_deployed`` are derived from the project's pointers
    rather than stored on the revision, because a revision's identity is
    immutable while which revision is current or deployed changes every turn.
    """

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    parent_revision_id: UUID | None = None
    restored_from_revision_id: UUID | None = None
    source_sha256: str
    size_bytes: int
    summary: str | None = None
    created_at: datetime
    created_by: UUID | None = None
    is_current: bool
    is_deployed: bool


class SourceRevisionsList(BaseModel):
    """List envelope for a Solution's revision history, newest first."""

    revisions: list[SourceRevisionDTO]
    total: int


class UndoRequest(BaseModel):
    """Restore an earlier revision's content as a new revision.

    ``session_id`` is required because undo is a turn like any other and every
    turn belongs to a chat session — the transcript has to show it happened.
    """

    to_revision_id: UUID
    session_id: UUID


class BuilderTurnDTO(BaseModel):
    """Read-shape for one builder turn."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    session_id: UUID
    requested_by: UUID | None = None
    base_revision_id: UUID | None = None
    output_revision_id: UUID | None = None
    status: str
    error: str | None = None
    created_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None


class BuilderTurnsList(BaseModel):
    """List envelope for a Solution's turns, newest first."""

    turns: list[BuilderTurnDTO]
    total: int
