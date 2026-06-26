"""Pydantic types for Solutions — installable surfaces (success-criteria §3)."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, computed_field, model_validator

SolutionScope = Literal["org", "global"]


class SolutionBase(BaseModel):
    slug: str = Field(min_length=1, max_length=255, description="Definition identity (shared across installs)")
    name: str = Field(min_length=1, max_length=255)
    global_repo_access: bool = False
    git_connected: bool = False
    git_repo_url: str | None = None
    repo_subpath: str | None = None
    git_ref: str | None = None


class SolutionCreate(SolutionBase):
    """Create-shape for an install.

    Install kind is DERIVED from ``organization_id``, per the unified --org
    standard — there is no ``scope`` input:

    - ``organization_id`` absent (HOME) => the caller's own org.
    - ``organization_id`` explicit null  => global (org NULL).
    - ``organization_id`` a UUID         => that org (cross-org admins).

    ``model_fields_set`` distinguishes absent (HOME) from explicit null (global).
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
    repo_subpath: str | None = None
    git_ref: str | None = None


class SolutionReadmeUpdate(BaseModel):
    """PUT body for an install's README markdown (Task 6).

    ``readme=None`` clears the README; a string sets it. The README is normally
    repo-sourced (deploy reads it from the repo-root README.md), but the UI can
    edit it directly on a disconnected install via this endpoint.
    """

    readme: str | None = None


class SolutionReadme(BaseModel):
    """GET/PUT response shape for an install's README markdown."""

    readme: str | None = None


class SolutionEntityCounts(BaseModel):
    """Per-install inventory counts for lightweight list/catalog views."""

    workflows: int = 0
    apps: int = 0
    forms: int = 0
    agents: int = 0
    tables: int = 0
    claims: int = 0
    files: int = 0


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
    # Subfolder within the connected repo holding this install's descriptor, and
    # the git ref (branch/tag) it tracks. Both None => repo root / default branch.
    repo_subpath: str | None = None
    git_ref: str | None = None
    # Version bookkeeping (Task 20): the deployed bundle's declared version and
    # what the last version-changing deploy replaced. Deploy-recorded, not
    # caller-settable — version rides in the BUNDLE (descriptor), not a request.
    version: str | None = None
    upgraded_from_version: str | None = None
    # Newest descriptor version available at the connected repo's ref HEAD when
    # it is PEP-440-greater than `version` (set by the update-check scheduler).
    # None => up to date / not git-connected / not yet checked. Drives the
    # "Update Available" badge.
    update_available_version: str | None = None
    # Persisted setup-completeness (Task 5/7): True when every required config
    # declaration has a matching Config value in the install's org scope.
    # Recomputed by install_zip after each deploy so it reflects the install's
    # state without a separate /setup call. Defaults True (no declarations = complete).
    setup_complete: bool = True
    # Lifecycle status. "active" = installed & live. "inactive" = uninstalled
    # (status flip only — data frozen in place under solution_id, dormant).
    status: str = "active"
    entity_counts: SolutionEntityCounts = Field(default_factory=SolutionEntityCounts)

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


class SolutionFileSummary(BaseModel):
    """Lightweight summary of one file owned by a solution install."""

    location: str
    path: str
    size: int | None = None


class SolutionEntities(BaseModel):
    """Everything one install owns + its config declaration/value status."""

    solution: Solution
    workflows: list[SolutionEntitySummary] = Field(default_factory=list)
    apps: list[SolutionEntitySummary] = Field(default_factory=list)
    forms: list[SolutionEntitySummary] = Field(default_factory=list)
    agents: list[SolutionEntitySummary] = Field(default_factory=list)
    claims: list[SolutionEntitySummary] = Field(default_factory=list)
    tables: list[SolutionEntitySummary] = Field(default_factory=list)
    files: list[SolutionFileSummary] = Field(default_factory=list)
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
    "workflow", "table", "config", "form", "app", "agent", "module", "integration"
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


class UnmetNeed(BaseModel):
    """One thing a solution needs that isn't satisfied in the install target.

    ``kind`` is "module" (a modules/*.py import not present in the bundle) or
    "solution_dep" (a cross-solution reference not installed). Surfaced at
    install/upgrade so the install can BLOCK rather than fail at runtime.
    """

    kind: Literal["module", "solution_dep"]
    ref: str
    detail: str | None = None


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


class SolutionRepoPreviewRequest(BaseModel):
    """Resolve an install plan from a git repo (+ optional subfolder/ref).
    Parse-only — no DB write, no S3, no build."""

    repo_url: str = Field(min_length=1, max_length=1024)
    repo_subpath: str | None = None
    git_ref: str | None = None
    # Install kind for install-from-repo, per the unified --org standard:
    # absent => the caller's own org (HOME); explicit null => global; a UUID =>
    # that org. The descriptor no longer carries scope.
    organization_id: UUID | None = Field(
        default=None,
        description="Target org for the install (absent => caller's org, null => global).",
    )


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
    perform (Task 22) — drag-drop routes to UPGRADE, never a second install.

    ``requires_password`` is True when the zip contains ``.bifrost/secrets.enc``
    (a full-backup export). The install endpoint requires a password to decrypt
    it; the UI should prompt for the password before the install POST."""

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
    # Each: {integration_name, template, position}. Secret-scrubbed skeletons
    # (no client_id/secret). Declared from integrations.get("X") refs.
    connection_schemas: list[dict[str, Any]] = Field(default_factory=list)
    existing_install: SolutionExistingInstall | None = None
    diff: SolutionUpgradeDiff | None = None
    requires_password: bool = False
    # Long-form README markdown read from the zip's repo-root README.md (Task 6).
    readme: str | None = None


class SolutionDeleteSummary(BaseModel):
    """Counts returned by a confirmed hard-delete (DELETE /{id}?confirm=<slug>).

    All owned rows are removed via the existing ``solution_id ondelete=CASCADE``
    FKs when the Solution row is deleted. The S3 ``solutions/{id}/`` prefix is
    swept after the DB commit. No data is orphaned — this is the destructive path.
    """

    solution_id: UUID
    workflows_deleted: int = 0
    apps_deleted: int = 0
    forms_deleted: int = 0
    agents_deleted: int = 0
    claims_deleted: int = 0
    config_declarations_deleted: int = 0
    tables_deleted: int = 0
    files_swept: int = 0


class SolutionDeletionSummary(BaseModel):
    """Preview of what a hard-delete would destroy (GET /{id}/deletion-summary).

    Returns counts per owned entity type so the confirmation modal can show the
    operator what they are about to destroy before they type the slug.
    """

    solution_id: UUID
    files: int = 0
    tables: int = 0
    workflows: int = 0
    apps: int = 0
    forms: int = 0
    agents: int = 0
    claims: int = 0
    config_declarations: int = 0
    events: int = 0


class SolutionDeployEnqueued(BaseModel):
    """Returned by ``POST /{id}/deploy`` — the deploy runs as a background job;
    the caller polls ``GET /deploy-jobs/{deploy_job_id}`` for the result."""

    deploy_job_id: UUID


class SolutionDeployJobStatus(BaseModel):
    """Current state of an async deploy job."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    install_id: UUID
    status: Literal["queued", "running", "succeeded", "failed"]
    error: str | None = None
    # Per-entity upsert/delete counts (and auto-created role names), present once
    # the job ``succeeded``. None while queued/running or on failure.
    result: dict[str, Any] | None = None
    created_at: datetime
    updated_at: datetime


SolutionExportJobStatus = Literal["pending", "running", "completed", "failed", "expired"]


class SolutionExportOptions(BaseModel):
    """Options for a durable async backup export of a solution install."""

    include_configs: bool = True
    include_secrets: bool = False
    include_tables: bool = False
    include_files: bool = False
    password: str | None = None

    @model_validator(mode="after")
    def require_at_least_one_include(self) -> "SolutionExportOptions":
        if not any(
            (
                self.include_configs,
                self.include_secrets,
                self.include_tables,
                self.include_files,
            )
        ):
            raise ValueError("At least one include_* option must be true")
        return self


class SolutionExportJobCreate(BaseModel):
    """Request body for enqueueing a solution backup export job."""

    options: SolutionExportOptions


class SolutionExportJobPublic(BaseModel):
    """Public state for a durable async solution backup export job."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    solution_id: UUID
    organization_id: UUID | None = None
    requested_by_id: UUID | None = None
    status: SolutionExportJobStatus
    progress_percent: int = 0
    message: str | None = None
    failure_message: str | None = None
    artifact_size_bytes: int | None = None
    artifact_sha256: str | None = None
    expires_at: datetime | None = None
    completed_at: datetime | None = None
    created_at: datetime
    updated_at: datetime
    download_url: str | None = None


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


class PullAckEntity(BaseModel):
    """One captured entity the client has materialized into source `.bifrost/`."""

    entity_type: str  # table|form|agent|config|event|claim
    entity_id: str  # entity id; for config, its key


class PullAckRequest(BaseModel):
    """Tell the server which pending_captures rows `bifrost solution pull`
    materialized into source, so it can clear exactly those rows
    (server-authoritative — a stale client can't clear what it didn't pull)."""

    entities: list[PullAckEntity] = Field(default_factory=list)


class PullAckResponse(BaseModel):
    cleared: int = 0


class SolutionSetupItem(BaseModel):
    """One declared requirement paired with whether it's satisfied.

    For ``kind="config"``: a config declaration; ``is_set`` = a Config value
    exists in the install scope. For ``kind="connection"``: an integration
    declaration; ``is_set`` = a global Integration with that name exists.
    """

    key: str  # config key OR integration name
    type: str  # config value type for kind="config"; the literal "integration" for kind="connection"
    required: bool
    is_set: bool
    description: str | None = None
    default: str | None = None
    kind: Literal["config", "connection"] = "config"
    # Connection-only meta (defaults for config items):
    has_oauth: bool = False  # template carried OAuth shape — WARN-ONLY, never gates
    connected: bool = False  # informational: a token/mapping resolves


class SolutionSetupStatus(BaseModel):
    """Required-config setup status for a Solution install.

    ``setup_complete`` is True when every required declaration has a matching
    Config value in the install's org scope.
    """

    setup_complete: bool
    items: list[SolutionSetupItem]


# ---------------------------------------------------------------------------
# File job contracts
# ---------------------------------------------------------------------------
