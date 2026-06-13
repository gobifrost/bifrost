"""Pydantic types for Solutions — installable surfaces (success-criteria §3)."""

from __future__ import annotations

from datetime import datetime
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
    """Partial-update (PATCH) of an install's INSTALL-LOCAL fields only.

    ``slug`` is identity and is NOT editable here. Portable content
    (workflows/apps/forms/agents/tables/config declarations) is owned by the
    bundle/git and is read-only on this surface.

    PATCH semantics: ``organization_id=None`` is a legitimate value (global
    scope), so it is distinguished from "not provided" via
    ``model_fields_set`` — the endpoint applies only fields present in the
    request (``model_dump(exclude_unset=True)``).
    """

    name: str | None = None
    organization_id: UUID | None = None
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
    # Version bookkeeping (Task 20): the deployed bundle's declared version and
    # what the last version-changing deploy replaced. Deploy-recorded, not
    # caller-settable — version rides in the BUNDLE (descriptor), not a request.
    version: str | None = None
    upgraded_from_version: str | None = None

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
    """Lightweight entity row for Solution-owned/capturable entity lists."""

    id: UUID
    name: str
    description: str | None = None
    organization_id: UUID | None = None
    slug: str | None = None
    path: str | None = None
    function_name: str | None = None
    type: str | None = None
    category: str | None = None
    access_level: str | None = None
    app_model: str | None = None
    is_active: bool | None = None
    logo: str | None = None
    source_table: str | None = None
    select: str | None = None
    created_at: datetime | None = None


class SolutionEntities(BaseModel):
    """Everything one install owns + its config declaration/value status."""

    solution: Solution
    workflows: list[SolutionEntitySummary] = Field(default_factory=list)
    apps: list[SolutionEntitySummary] = Field(default_factory=list)
    forms: list[SolutionEntitySummary] = Field(default_factory=list)
    agents: list[SolutionEntitySummary] = Field(default_factory=list)
    claims: list[SolutionEntitySummary] = Field(default_factory=list)
    tables: list[SolutionEntitySummary] = Field(default_factory=list)
    configs: list[SolutionConfigStatus] = Field(default_factory=list)
    required_configs_unset: list[str] = Field(default_factory=list)


class SolutionCaptureCandidates(BaseModel):
    """Loose same-scope entities that can be adopted into an install."""

    workflows: list[SolutionEntitySummary] = Field(default_factory=list)
    apps: list[SolutionEntitySummary] = Field(default_factory=list)
    forms: list[SolutionEntitySummary] = Field(default_factory=list)
    agents: list[SolutionEntitySummary] = Field(default_factory=list)
    claims: list[SolutionEntitySummary] = Field(default_factory=list)
    tables: list[SolutionEntitySummary] = Field(default_factory=list)
    configs: list[SolutionConfigStatus] = Field(default_factory=list)


# ── Dependency preview (capture + export) — §3.2/§3.3 ───────────────────────

# Entity kinds the dependency walker reasons about. ``module`` is a Python file
# under ``modules/`` (no DB row); the rest are DB entities keyed by id, except
# ``config`` which is keyed by its string key.
DependencyKind = Literal[
    "workflow", "table", "config", "form", "app", "agent", "module"
]


class DependencyRef(BaseModel):
    """One entity the walker pulled in or warned about.

    ``ref`` is the natural handle: a UUID for DB entities, a key for configs, a
    relative path for modules. ``name`` is the display label; ``in_selection``
    is true when the seed selection already includes this entity (so the UI can
    show it as "already selected" vs "will be pulled in").
    """

    kind: DependencyKind
    ref: str
    name: str
    in_selection: bool = False


class OutsideReference(BaseModel):
    """An entity OUTSIDE the selection that references something INSIDE it.

    The capture/export preview surfaces these as non-blocking warnings: the
    referenced entity is being adopted by the install while ``referencer`` is
    left loose and will keep pointing at it across the scope boundary.
    """

    referencer_kind: DependencyKind
    referencer_ref: str
    referencer_name: str
    target_kind: DependencyKind
    target_ref: str
    target_name: str


class SolutionDependencyPreview(BaseModel):
    """What a capture/export selection actually grabs, for human review.

    ``pulled_in`` is the forward dependency closure beyond the seed selection
    (e.g. a captured workflow's ``modules/`` imports when ``include_imports`` is
    on, the tables/configs it reads, the workflow a captured form launches).
    ``outside_references`` are reverse-dependency warnings. The preview is the
    guard: every item is deselectable, nothing is silently blocked.
    """

    pulled_in: list[DependencyRef] = Field(default_factory=list)
    outside_references: list[OutsideReference] = Field(default_factory=list)
    # True when the static scan can't see everything (dynamic imports / computed
    # refs) — the UI nudges the human to add any missed file manually.
    scan_is_static: bool = True


class SolutionDependencyPreviewRequest(BaseModel):
    """Seed selection to preview, mirroring SolutionCaptureRequest's selectors."""

    workflows: list[UUID] = Field(default_factory=list)
    tables: list[UUID] = Field(default_factory=list)
    apps: list[UUID] = Field(default_factory=list)
    forms: list[UUID] = Field(default_factory=list)
    agents: list[UUID] = Field(default_factory=list)
    claims: list[UUID] = Field(default_factory=list)
    configs: list[str] = Field(default_factory=list)
    include_imports: bool = False


class SolutionEntityDiff(BaseModel):
    """Added/removed display names for ONE entity type in an upgrade preview.

    Identity is the deployer's per-install uuid5 remap (``solution_entity_id``),
    so "kept" (in neither list) means the deploy would UPDATE that row in place.
    """

    added: list[str] = Field(default_factory=list)
    removed: list[str] = Field(default_factory=list)


class SolutionConfigSchemaState(BaseModel):
    """The compared portion of a config declaration (type + required)."""

    type: str
    required: bool


class SolutionConfigSchemaChange(BaseModel):
    """One config declaration whose type/required changed between versions."""

    model_config = ConfigDict(populate_by_name=True)

    key: str
    from_: SolutionConfigSchemaState = Field(alias="from")
    to: SolutionConfigSchemaState


class SolutionConfigSchemaDiff(BaseModel):
    """Config DECLARATION diff (by key) for an upgrade preview."""

    added: list[str] = Field(default_factory=list)
    removed: list[str] = Field(default_factory=list)
    changed: list[SolutionConfigSchemaChange] = Field(default_factory=list)


class SolutionUpgradeDiff(BaseModel):
    """What deploying the previewed zip would change on the existing install."""

    workflows: SolutionEntityDiff = Field(default_factory=SolutionEntityDiff)
    tables: SolutionEntityDiff = Field(default_factory=SolutionEntityDiff)
    forms: SolutionEntityDiff = Field(default_factory=SolutionEntityDiff)
    agents: SolutionEntityDiff = Field(default_factory=SolutionEntityDiff)
    apps: SolutionEntityDiff = Field(default_factory=SolutionEntityDiff)
    claims: SolutionEntityDiff = Field(default_factory=SolutionEntityDiff)
    config_schemas: SolutionConfigSchemaDiff = Field(default_factory=SolutionConfigSchemaDiff)


class SolutionExistingInstall(BaseModel):
    """The install a previewed zip would UPGRADE (matched by slug + scope)."""

    id: UUID
    name: str
    version: str | None = None


class SolutionInstallPreview(BaseModel):
    """Parse-only preview of a Solution install zip — what it would create + its
    declared configs. Nothing is persisted by the preview endpoint.

    When an install already exists for the zip's slug at the requested scope,
    ``existing_install`` + ``diff`` describe the upgrade the install would
    perform (Task 22) — drag-drop routes to UPGRADE, never a second install."""

    slug: str | None = None
    name: str | None = None
    scope: SolutionScope | None = None
    version: str | None = None
    workflows: list[dict[str, Any]] = Field(default_factory=list)
    tables: list[dict[str, Any]] = Field(default_factory=list)
    apps: list[dict[str, Any]] = Field(default_factory=list)
    forms: list[dict[str, Any]] = Field(default_factory=list)
    agents: list[dict[str, Any]] = Field(default_factory=list)
    claims: list[dict[str, Any]] = Field(default_factory=list)
    config_schemas: list[dict[str, Any]] = Field(default_factory=list)
    existing_install: SolutionExistingInstall | None = None
    diff: SolutionUpgradeDiff | None = None


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
    claims: list[dict[str, Any]] = Field(default_factory=list)
    # Each config schema: {id, key, type, required, description?, default?, position}.
    # DECLARATIONS only — never a value (values are instance-owned Config rows).
    config_schemas: list[dict[str, Any]] = Field(default_factory=list)
    # The bundle's declared version (bifrost.solution.yaml ``version:``).
    # Recorded on the install; an older version than installed is refused
    # unless ``force`` is set (Task 20 downgrade gate).
    version: str | None = None
    # Solution-level icon declared by ``logo:`` in bifrost.solution.yaml,
    # carried base64 by the CLI; deploy validates and stamps it on the install
    # (absent => cleared).
    logo_b64: str | None = None
    logo_content_type: str | None = None
    force: bool = False


class SolutionDeleteSummary(BaseModel):
    """Counts of what a DELETE did. Pure-code entities (workflows/apps/forms/
    agents) and the install's config DECLARATIONS are deleted via DB cascade.
    Data-bearing entities are ORPHANED, not deleted: owned tables (and their
    documents) are detached and survive as ordinary org tables, and the
    install's config VALUES are stamped with orphan provenance and survive.
    The UI echoes these back to the operator."""

    solution_id: UUID
    workflows_deleted: int = 0
    apps_deleted: int = 0
    forms_deleted: int = 0
    agents_deleted: int = 0
    claims_deleted: int = 0
    config_declarations_deleted: int = 0
    tables_orphaned: int = 0
    config_values_orphaned: int = 0


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
    claims_upserted: int = 0
    claims_deleted: int = 0


class SolutionCaptureRequest(BaseModel):
    """Move existing loose entities into an install in place.

    Entity ids must currently be unowned (``solution_id`` is null) and scoped
    the same way as the install. Config keys become declarations; their values
    stay in the install scope.
    """

    workflows: list[UUID] = Field(default_factory=list)
    tables: list[UUID] = Field(default_factory=list)
    apps: list[UUID] = Field(default_factory=list)
    forms: list[UUID] = Field(default_factory=list)
    agents: list[UUID] = Field(default_factory=list)
    claims: list[UUID] = Field(default_factory=list)
    configs: list[str] = Field(default_factory=list)
    include_imports: bool = Field(
        default=False,
        description=(
            "When false (default), bundle only the captured workflows' own "
            "source files. When true, also bundle the transitive import "
            "closure of `modules/` they reference (never the whole modules/ "
            "tree — only what is actually imported)."
        ),
    )


class SolutionCaptureResponse(BaseModel):
    solution_id: UUID
    workflows_captured: int = 0
    tables_captured: int = 0
    apps_captured: int = 0
    forms_captured: int = 0
    agents_captured: int = 0
    claims_captured: int = 0
    config_declarations_captured: int = 0
