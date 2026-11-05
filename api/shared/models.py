"""Small shared request and response models used outside a single API domain."""
from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal
from uuid import UUID, uuid4

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator, model_validator


SEMVER_PATTERN = (
    r"^(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)"
    r"(?:-(?:(?:0|[1-9]\d*)|(?:\d*[A-Za-z-][0-9A-Za-z-]*))"
    r"(?:\.(?:(?:0|[1-9]\d*)|(?:\d*[A-Za-z-][0-9A-Za-z-]*)))*)?"
    r"(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?$"
)


class AppConnectionDetails(BaseModel):
    """A user-bound preview of one external app connection."""

    client_name: str
    redirect_uri: str
    consent_token: str


class AppConnectionDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    approved: bool
    consent_token: str = Field(min_length=64, max_length=64)


class AppConnectionResult(BaseModel):
    redirect_url: str | None = None


class MCPClientRegistration(BaseModel):
    client_name: str | None = Field(default=None, max_length=255)
    redirect_uris: list[str] = Field(min_length=1, max_length=20)


class UsageReport(BaseModel):
    """Pseudonymous aggregate activity for one completed UTC day."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    installation_id: UUID
    version: str = Field(min_length=5, max_length=128, pattern=SEMVER_PATTERN)
    period: date
    workflow_runs: int = Field(ge=0, le=1_000_000_000)
    agent_runs: int = Field(ge=0, le=1_000_000_000)
    environment: Literal["production"] = "production"

    @field_validator("installation_id")
    @classmethod
    def require_v4_installation_id(cls, value: UUID) -> UUID:
        if value.version != 4:
            raise ValueError("installation_id must be a UUIDv4")
        return value


class BrowserImpersonationTokenResponse(BaseModel):
    """A short-lived bearer credential for one browser impersonation tab."""
    access_token: str
    token_type: Literal["bearer"] = "bearer"
    expires_in: Literal[1800] = 1800


class SolutionDevContextRequest(BaseModel):
    """The selected portable app identity for a local Solution preview."""

    app_id: UUID


class SolutionDevContextResponse(BaseModel):
    """Short-lived, app-bound context accepted only as a supplemental header."""

    token: str
    expires_at: float
    solution_id: UUID
    organization_id: UUID | None
    app_id: UUID


class RuntimeLogRequest(BaseModel):
    """A line for the authenticated runtime's own log stream."""

    model_config = ConfigDict(extra="forbid")
    level: str = Field(min_length=1, max_length=32)
    message: str = Field(max_length=65536)
    metadata: dict[str, Any] | None = None
    timestamp: datetime | None = None


class RuntimeLogResponse(BaseModel):
    entry_id: str | None


class ServiceControlRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ready: bool = False
    token_expires_at: AwareDatetime


class ServiceControlResponse(BaseModel):
    stop_requested: bool
    access_token: str | None = None
    token_expires_at: datetime | None = None


class ProviderCacheReadRequest(BaseModel):
    """Parameters for the authenticated runtime's own data-provider cache read."""

    model_config = ConfigDict(extra="forbid")
    parameters: dict[str, Any] = Field(default_factory=dict)


class ProviderCacheReadResponse(BaseModel):
    hit: bool
    result: Any | None = None
    expires_at: datetime | None = None


class ProviderCacheWriteRequest(BaseModel):
    """Result for the authenticated runtime's own data-provider cache entry."""

    model_config = ConfigDict(extra="forbid")
    parameters: dict[str, Any] = Field(default_factory=dict)
    result: Any


class ProviderCacheWriteResponse(BaseModel):
    expires_at: datetime | None = None


class DeviceAuthorizeRequest(BaseModel):
    """The code for a CLI device login being approved or denied."""

    user_code: str


class AppSdkUpgradeSettings(BaseModel):
    """Platform-wide policy for automatic App SDK upgrades."""

    enabled: bool = False


class AppSdkUpgradeSettingsUpdate(BaseModel):
    """Requested platform-wide App SDK upgrade policy."""

    enabled: bool


class ApplicationSdkUpdatePayloadV1(BaseModel):
    """Legacy manual App SDK update payload retained for already queued jobs."""

    model_config = ConfigDict(extra="forbid")

    application_id: UUID
    deployment_id: UUID = Field(default_factory=uuid4)
    expected_active_deployment_id: UUID | None = None
    expected_sdk_package_version: str | None = None
    expected_sdk_fingerprint: str | None = None
    expected_sdk_contract_version: int | None = None
    expected_sdk_built_at: datetime | None = None


class HostedAppSdkDescriptor(BaseModel):
    """The current immutable browser SDK factory for one binding API major."""

    api_major: int = Field(ge=1)
    factory_version: int = Field(ge=1)
    factory_id: str = Field(pattern=r"^[a-f0-9]{64}$")
    fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    integrity: str = Field(pattern=r"^sha256-[A-Za-z0-9+/]{43}=$")
    asset_url: str = Field(pattern=r"^/api/app-sdk/assets/[a-f0-9]{64}\.js$")


class ApplicationSdkUpdatePayloadV2(ApplicationSdkUpdatePayloadV1):
    """Pinned-target jobs queued before hosted binding provenance existed."""

    target_sdk_fingerprint: str = Field(min_length=1)
    target_sdk_contract_version: int = Field(ge=1)
    automatic_upgrade: bool = False


class ApplicationSdkUpdatePayload(ApplicationSdkUpdatePayloadV2):
    """Version 3 fences the binding API major as well as the compiled bytes."""

    expected_sdk_binding_api_major: int | None = Field(default=None, ge=1)
    target_sdk_binding_api_major: int | None = Field(default=None, ge=1)


class VersionResponse(BaseModel):
    version: str
    min_cli_version: str
    contract_version: int
    sdk_fingerprint: str
    sdk_contract_version: int
    sdk_binding_api_major: int | None = None
    sdk_supported_binding_api_majors: list[int] = Field(default_factory=list)


class ApplicationBundleManifest(BaseModel):
    """Built App assets and the host contracts needed before entry evaluation."""

    entry: str | None
    css: str | None
    base_url: str
    mode: Literal["preview", "live"]
    dependencies: dict[str, str]
    migrated: bool = False
    organization_id: UUID | None = None
    app_model: Literal["inline_v1", "standalone_v2"] = "inline_v1"
    runtime_contract: Literal["mount-v1"] | None = None
    sdk_binding_api_major: int | None = Field(default=None, ge=1)


class WorkerCapacityProfile(BaseModel):
    """Capabilities of an authenticated external physical-capacity provider."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    protocol_version: Literal[1]
    scope: str = Field(min_length=1, max_length=128, pattern=r"^[a-zA-Z0-9._:-]+$")
    worker_version: str = Field(min_length=1, max_length=100)
    memory_limit_bytes: int = Field(ge=67108864, le=137438953472, multiple_of=1048576, strict=True)
    max_workers: int = Field(ge=1, le=1000, strict=True)
    max_lifetime_seconds: int = Field(ge=60, le=604800, strict=True)


class WorkerCapacityReservation(BaseModel):
    """Immutable preparation of one canonical worker incarnation."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    worker_id: UUID
    worker_version: str = Field(min_length=1, max_length=100)
    memory_limit_bytes: int = Field(ge=67108864, le=137438953472, multiple_of=1048576, strict=True)
    max_lifetime_seconds: int = Field(ge=60, le=604800, strict=True)
    drain_grace_seconds: int = Field(ge=0, le=3600, strict=True)


class WorkerCapacityResource(BaseModel):
    """Provider state; canonical enrollment and work status remain in Postgres."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    scope: str = Field(min_length=1, max_length=128, pattern=r"^[a-zA-Z0-9._:-]+$")
    worker_id: UUID
    worker_version: str = Field(min_length=1, max_length=100)
    resource_uid: str = Field(min_length=1, max_length=255, pattern=r"^[a-zA-Z0-9._:-]+$")
    state: Literal["prepared", "starting", "running", "stopped", "deleted"]
    out_of_memory: bool = Field(default=False, strict=True)


class SolutionAppBuildCandidate(BaseModel):
    """Manifest app identity and a digest computed before local compilation."""

    app_id: UUID
    fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")


class SolutionAppBuildLookup(BaseModel):
    apps: list[SolutionAppBuildCandidate]


class SolutionAppBuildMatches(BaseModel):
    matches: dict[str, UUID]


class MCPPackageBrandingUpdate(BaseModel):
    """Instance-wide identity for downloaded agent plugin packages."""

    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=60)

    @field_validator("name")
    @classmethod
    def validate_package_name(cls, value: str) -> str:
        import re

        value = value.strip()
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9 _-]*", value):
            raise ValueError("Use letters, numbers, spaces, hyphens, or underscores; start with a letter or number")
        return value


class MCPPackageBranding(BaseModel):
    """Saved package display name, installation identifier, and optional logo."""

    name: str
    slug: str
    logo_url: str | None = None


ConversionStatus = Literal["ready", "needs_review", "blocked", "already_declared", "stale"]


class WorkflowConversionAnalyzeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    # UUID-only selection remains bounded while supporting large filtered
    # workflow inventories; analysis itself still pages the durable snapshot.
    workflow_ids: list[UUID] | None = Field(default=None, max_length=10_000)
    scope: str | None = None
    platform_cutover: bool = False

    @field_validator("workflow_ids")
    @classmethod
    def _nonempty_explicit_selection(cls, value: list[UUID] | None) -> list[UUID] | None:
        if value == []:
            raise ValueError("workflow_ids must be omitted or contain at least one workflow")
        if value is not None and len(value) != len(set(value)):
            raise ValueError("workflow_ids must not contain duplicates")
        return value

    @field_validator("scope")
    @classmethod
    def _scope_shape(cls, value: str | None) -> str | None:
        if value in (None, "all", "global"):
            return value
        try:
            UUID(value)
        except ValueError as exc:
            raise ValueError("scope must be all, global, or an organization UUID") from exc
        return value

    @model_validator(mode="after")
    def _platform_cutover_uses_complete_inventory(self) -> "WorkflowConversionAnalyzeRequest":
        if self.platform_cutover and (self.workflow_ids is not None or self.scope not in (None, "all")):
            raise ValueError("platform_cutover requires the whole platform inventory")
        return self


class WorkflowConversionSelection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    workflow_id: UUID
    source_fingerprint: str = Field(min_length=64, max_length=64)
    acknowledged_unknowns: bool = False


class WorkflowConversionApplyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    selections: list[WorkflowConversionSelection] = Field(min_length=1, max_length=10_000)
    acknowledged_solution_requests: bool = False

    @field_validator("selections")
    @classmethod
    def _unique_workflows(cls, value: list[WorkflowConversionSelection]) -> list[WorkflowConversionSelection]:
        if len({selection.workflow_id for selection in value}) != len(value):
            raise ValueError("selections must not contain duplicate workflows")
        return value


class WorkflowConversionCutoverRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    acknowledged_remaining_exceptions: bool = False


class WorkflowConversionEvidence(BaseModel):
    path: str
    line: int | None = None
    call: str
    via: list[str] = Field(default_factory=list)


class WorkflowConversionIdentitySummary(BaseModel):
    id: UUID | None = None
    name: str | None = None
    identity_kind: str | None = None
    organization_id: UUID | None = None
    shared: bool = False


class WorkflowConversionSolutionRequestSummary(BaseModel):
    status: str
    request_digest: str
    approved_digest: str | None = None
    requested_mode: str
    requested_permissions: list[str] = Field(default_factory=list)


class WorkflowConversionReportItem(BaseModel):
    workflow_id: UUID
    workflow_name: str
    organization_id: UUID | None = None
    solution_id: UUID | None = None
    source_fingerprint: str
    status: ConversionStatus
    current_permission_mode: Literal["full", "restricted"]
    proposed_permissions: list[str] = Field(default_factory=list)
    source_permissions: list[str] = Field(default_factory=list)
    observed_permissions: list[str] = Field(default_factory=list)
    observed_operations: list[str] = Field(default_factory=list)
    evidence: list[WorkflowConversionEvidence] = Field(default_factory=list)
    unknowns: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    identity: WorkflowConversionIdentitySummary | None = None
    solution_permission_request: WorkflowConversionSolutionRequestSummary | None = None
    error: str | None = None
    review: dict[str, Any] | None = None


class WorkflowConversionReportSummary(BaseModel):
    platform_cutover: bool = False
    analyzer_version: str
    total: int
    ready: int = 0
    needs_review: int = 0
    blocked: int = 0
    already_declared: int = 0
    stale: int = 0
    observation_window_days: int = 30
    default_permission_mode: Literal["full", "restricted"]
    cutover_ready: bool = False
    remaining_full: int = 0


class WorkflowConversionReport(BaseModel):
    job_id: UUID
    total: int
    offset: int
    limit: int
    items: list[WorkflowConversionReportItem]
    summary: WorkflowConversionReportSummary


class WorkflowConversionAnalyzePayload(WorkflowConversionAnalyzeRequest):
    pass


class WorkflowConversionApplyPayload(WorkflowConversionApplyRequest):
    analysis_job_id: UUID


class WorkflowConversionCutoverPayload(WorkflowConversionCutoverRequest):
    analysis_job_id: UUID
