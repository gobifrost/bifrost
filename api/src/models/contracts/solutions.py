"""Pydantic types for Solutions — installable surfaces (success-criteria §3)."""

from __future__ import annotations

from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, computed_field

SolutionScope = Literal["org", "global"]


class SolutionBase(BaseModel):
    slug: str = Field(min_length=1, max_length=255, description="Definition identity (shared across installs)")
    name: str = Field(min_length=1, max_length=255)
    scope: SolutionScope = "org"
    global_repo_access: bool = False
    git_connected: bool = False
    git_repo_url: str | None = None


class SolutionCreate(SolutionBase):
    """Create-shape for an install.

    For ``scope=org`` the install's org is taken from the caller's context (or
    an explicit ``organization_id`` for cross-org admins); ``scope=global``
    means ``organization_id IS NULL``.
    """

    organization_id: UUID | None = None


class SolutionUpdate(BaseModel):
    """Partial update; install identity (slug/scope) is immutable here."""

    name: str | None = None
    global_repo_access: bool | None = None
    git_connected: bool | None = None
    git_repo_url: str | None = None


class Solution(BaseModel):
    """Read-shape returned by REST.

    ``scope`` is DERIVED from ``organization_id`` (NULL == global), not stored
    on the ORM row — so it always reflects the install's true scope.
    """

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    slug: str
    name: str
    organization_id: UUID | None = None
    global_repo_access: bool = False
    git_connected: bool = False
    git_repo_url: str | None = None

    @computed_field  # type: ignore[prop-decorator]
    @property
    def scope(self) -> SolutionScope:
        return "org" if self.organization_id is not None else "global"


class SolutionsList(BaseModel):
    solutions: list[Solution] = Field(default_factory=list)


class SolutionConfigStatus(BaseModel):
    """A config DECLARATION on an install, paired with whether a value is set in
    the install's org scope (values are instance-owned Config rows, never part of
    the declaration)."""

    id: UUID
    key: str
    type: str
    required: bool
    description: str | None = None
    value_set: bool


class SolutionEntitySummary(BaseModel):
    """Lightweight (id, name) entry for an owned entity — the detail UI links by id."""

    id: UUID
    name: str


class SolutionEntities(BaseModel):
    """Everything one install owns + its config declaration/value status."""

    solution: Solution
    workflows: list[SolutionEntitySummary] = Field(default_factory=list)
    apps: list[SolutionEntitySummary] = Field(default_factory=list)
    forms: list[SolutionEntitySummary] = Field(default_factory=list)
    agents: list[SolutionEntitySummary] = Field(default_factory=list)
    tables: list[SolutionEntitySummary] = Field(default_factory=list)
    configs: list[SolutionConfigStatus] = Field(default_factory=list)
    required_configs_unset: list[str] = Field(default_factory=list)


class SolutionInstallPreview(BaseModel):
    """Parse-only preview of a Solution install zip — what it would create + its
    declared configs. Nothing is persisted by the preview endpoint."""

    slug: str | None = None
    name: str | None = None
    scope: SolutionScope | None = None
    workflows: list[dict[str, Any]] = Field(default_factory=list)
    tables: list[dict[str, Any]] = Field(default_factory=list)
    apps: list[dict[str, Any]] = Field(default_factory=list)
    forms: list[dict[str, Any]] = Field(default_factory=list)
    agents: list[dict[str, Any]] = Field(default_factory=list)
    config_schemas: list[dict[str, Any]] = Field(default_factory=list)


class SolutionDeployRequest(BaseModel):
    """Full-replace deploy bundle for one install.

    ``python_files`` maps relative paths (e.g. ``workflows/w.py``,
    ``modules/x.py``) to UTF-8 source text, installed verbatim under the
    install's ``_solutions/{id}/`` prefix. ``workflows`` are manifest-shaped
    entity dicts to upsert (apps/forms/agents/tables join in later sub-plans).
    Deploy is non-interactive by contract — it always applies the full bundle.
    """

    python_files: dict[str, str] = Field(default_factory=dict)
    workflows: list[dict[str, Any]] = Field(default_factory=list)
    tables: list[dict[str, Any]] = Field(default_factory=list)
    # Each app: {id, slug, name, app_model, dependencies, access_level,
    # src_files: {rel: text} | dist_files: {rel: text}}. dist_files is the
    # disconnected fast-path (skip the server build).
    apps: list[dict[str, Any]] = Field(default_factory=list)
    # Each form: {id, name, description?, workflow_id?, fields: [...]}.
    forms: list[dict[str, Any]] = Field(default_factory=list)
    # Each agent: {id, name, system_prompt, description?, channels?, llm_model?}.
    agents: list[dict[str, Any]] = Field(default_factory=list)
    # Each config schema: {id, key, type, required, description?, default?, position}.
    # DECLARATIONS only — never a value (values are instance-owned Config rows).
    config_schemas: list[dict[str, Any]] = Field(default_factory=list)


class SolutionDeployResponse(BaseModel):
    solution_id: UUID
    workflows_upserted: int = 0
    workflows_deleted: int = 0
    tables_upserted: int = 0
    tables_deleted: int = 0
    apps_upserted: int = 0
    apps_deleted: int = 0
    forms_upserted: int = 0
    forms_deleted: int = 0
    agents_upserted: int = 0
    agents_deleted: int = 0
