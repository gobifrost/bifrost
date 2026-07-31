"""Pydantic types for the private-Solution builder surface.

These are deliberately separate from ``contracts/solutions.py``: that file is the
administrator install-management contract, while these describe the owner-scoped
private-builder surface (2026-07-25 private-solution-builder spec, Work Package
1). A private Solution never exposes install-management fields such as git
connection state, so it gets its own read shape rather than a widened one.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal
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
    app_origin: str | None = None
    status: str
    promotion_status: str
    created_at: datetime
    updated_at: datetime


class PrivateSolutionsList(BaseModel):
    """List envelope for the caller's own private Solutions."""

    solutions: list[PrivateSolutionDTO]
    total: int
    ai_configured: bool
    is_platform_admin: bool


class BuilderProjectDTO(BaseModel):
    """Read-shape for a Solution's builder-project row."""

    model_config = ConfigDict(from_attributes=True)

    solution_id: UUID
    current_revision_id: UUID | None = None
    deployed_revision_id: UUID | None = None
    promotion_status: str
    promotion_revision_id: UUID | None = None
    promotion_requested_by: UUID | None = None
    promotion_requested_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class PromotionTargetRequest(BaseModel):
    """Administrator approval of one pinned private-Solution revision."""

    target: Literal["company", "global"]
    approve_role_creation: bool = False
    approved_connection_names: list[str] = Field(default_factory=list)
    allow_global_repo_access: bool = False
    role_user_assignments: dict[str, list[UUID]] = Field(default_factory=dict)


class PromotionEntityCounts(BaseModel):
    workflows: int = 0
    tables: int = 0
    apps: int = 0
    forms: int = 0
    agents: int = 0
    claims: int = 0
    configs: int = 0
    files: int = 0
    file_policies: int = 0
    policy_rules: int = 0
    events: int = 0


class PromotionReviewDTO(BaseModel):
    """Pinned source and readiness facts shown on the admin review surface."""

    solution_id: UUID
    slug: str
    name: str
    owner_user_id: UUID | None = None
    organization_id: UUID | None = None
    promotion_status: str
    pinned_revision_id: UUID | None = None
    source_sha256: str | None = None
    source_size_bytes: int | None = None
    prior_deployed_revision_id: UUID | None = None
    changed_paths: list[str] = Field(default_factory=list)
    requested_at: datetime | None = None
    requested_by: UUID | None = None
    current_revision_id: UUID | None = None
    deployed_revision_id: UUID | None = None
    build_job_id: UUID | None = None
    deploy_job_id: UUID | None = None
    build_status: str | None = None
    deploy_status: str | None = None
    entity_counts: PromotionEntityCounts = Field(
        default_factory=PromotionEntityCounts
    )
    unresolved_roles: list[str] = Field(default_factory=list)
    connection_names: list[str] = Field(default_factory=list)
    config_keys_requiring_reentry_for_global: list[str] = Field(
        default_factory=list
    )
    global_repo_access: bool = False
    ready: bool = False
    blockers: list[str] = Field(default_factory=list)


class PromotionReviewsList(BaseModel):
    promotions: list[PromotionReviewDTO]
    total: int


class PromotionResultDTO(BaseModel):
    solution_id: UUID
    target: Literal["company", "global"]
    visibility: Literal["shared"]
    organization_id: UUID | None = None
    promoted_revision_id: UUID
    roles_created: list[str] = Field(default_factory=list)


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


class RevisionFileDTO(BaseModel):
    """One regular file inside an immutable source revision."""

    path: str
    size_bytes: int
    is_text: bool


class RevisionFilesList(BaseModel):
    revision_id: UUID
    files: list[RevisionFileDTO]
    total: int


class RevisionFileContentDTO(BaseModel):
    revision_id: UUID
    path: str
    size_bytes: int
    encoding: Literal["utf-8", "binary"]
    content: str | None = None
    truncated: bool = False


class RevisionDiffFileDTO(BaseModel):
    path: str
    status: Literal["added", "modified", "deleted"]
    additions: int = 0
    deletions: int = 0
    is_binary: bool = False
    diff: str | None = None
    truncated: bool = False


class RevisionDiffDTO(BaseModel):
    revision_id: UUID
    against_revision_id: UUID | None = None
    files: list[RevisionDiffFileDTO]
    total: int
    additions: int = 0
    deletions: int = 0


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
    build_job_id: UUID | None = None
    deploy_job_id: UUID | None = None
    status: str
    error: str | None = None
    created_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None


class BuilderTurnsList(BaseModel):
    """List envelope for a Solution's turns, newest first."""

    turns: list[BuilderTurnDTO]
    total: int


class RunTurnRequest(BaseModel):
    """Ask the builder agent to change the workspace."""

    session_id: UUID
    message: str = Field(min_length=1, max_length=32_000)


class RunTurnResponse(BaseModel):
    """What one agent turn produced.

    ``revision_created`` is False for a turn the model answered without
    editing anything (a question, or an edit that produced identical bytes);
    the preview is unchanged in that case.
    """

    turn: BuilderTurnDTO
    final_text: str
    tool_call_count: int
    revision_created: bool


class BuildOutputEntry(BaseModel):
    """One immutable dist artifact accepted from the build coordinator."""

    path: str = Field(min_length=1, max_length=2048)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    size: int = Field(ge=0)


class BuildJobStatusUpdate(BaseModel):
    """Terminal transition reported by a one-job build capability."""

    status: Literal["succeeded", "failed", "timeout", "cancelled"]
    error: str | None = None
    log_excerpt: str | None = None
    output_manifest: list[BuildOutputEntry] | None = None


class ClaimedBuildJob(BaseModel):
    """The credential-free coordinator's bounded runner instruction."""

    id: UUID
    solution_id: UUID
    app_id: UUID
    timeout_s: int


class BuildJobPublic(BaseModel):
    """Owner-visible build status used by builder polling."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    app_id: UUID | None = None
    status: str
    error: str | None = None
    created_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None
