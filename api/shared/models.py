"""Small shared request and response models used outside a single API domain."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator, model_validator


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


ConversionStatus = Literal["ready", "needs_review", "blocked", "already_declared", "stale"]
IdentityStrategy = Literal["dedicated", "keep_current"]


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
    identity_strategy: IdentityStrategy = "dedicated"
    acknowledged_unknowns: bool = False
    acknowledged_broad_identity: bool = False


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
    broad_power_warning: bool = False


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
