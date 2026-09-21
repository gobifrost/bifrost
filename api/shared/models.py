"""Shared additive DTOs for recorded agent evaluation."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any, Generic, Literal, TypeVar
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_serializer

RecordedApplicability = Literal["applicable", "not_applicable", "unknown"]
RecordedJudgeMode = Literal["exact", "semantic"]
AgentReviewStatus = Literal["active", "disabled"]
FindingStatus = Literal["open", "dismissed"]
FindingSourceKind = Literal["run", "manual", "external"]
FindingKind = Literal["problem", "opportunity"]
AgentReviewFindingKind = FindingKind


class RecordedApplicabilityOverride(BaseModel):
    model_config = ConfigDict(extra="forbid")

    case_id: UUID
    run_id: UUID
    applicability: RecordedApplicability


class RecordedEvaluationCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    agent_id: UUID
    run_ids: list[UUID] = Field(..., min_length=1, max_length=20)
    case_ids: list[UUID] | None = Field(default=None, max_length=100)
    all_tests: bool = False
    applicability: RecordedApplicability = "unknown"
    judge_mode: RecordedJudgeMode = "exact"
    applicability_overrides: list[RecordedApplicabilityOverride] = Field(
        default_factory=list, max_length=1000
    )


class RecordedEvaluationResultPublic(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    evaluation_id: UUID
    case_id: UUID
    case_version: int
    run_id: UUID
    applicability: RecordedApplicability
    applicability_source: str
    outcome: str
    complete: bool
    assertion_outcomes: list[dict[str, Any]]
    counts: dict[str, Any]
    evidence_refs: list[dict[str, Any]]
    limitations: list[str]
    error: str | None = None
    created_at: datetime


class RecordedEvaluationResultsPage(BaseModel):
    evaluation_id: UUID
    job_id: UUID
    aggregate: dict[str, Any] | None = None
    results: list[RecordedEvaluationResultPublic]
    total: int
    limit: int
    offset: int


class AgentReviewDefinitionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    agent_id: UUID
    organization_id: UUID | None = None
    name: str = Field(min_length=1, max_length=200)
    review_statement: str = Field(min_length=1, max_length=8000)
    evidence_format_instructions: str | None = Field(default=None, max_length=4000)
    model_profile_id: UUID | None = None


class AgentReviewDefinitionUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=200)
    status: AgentReviewStatus | None = None


class AgentReviewVersionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    review_statement: str = Field(min_length=1, max_length=8000)
    evidence_format_instructions: str | None = Field(default=None, max_length=4000)
    model_profile_id: UUID | None = None


class AgentReviewVersionPublic(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    review_id: UUID
    version: int
    review_statement: str
    evidence_format_instructions: str | None = None
    model_profile_id: UUID | None = None
    created_by: UUID | None = None
    created_at: datetime


class AgentReviewDefinitionPublic(BaseModel):
    id: UUID
    agent_id: UUID
    org_id: UUID | None = None
    name: str
    status: AgentReviewStatus
    latest_version: int
    latest_version_id: UUID
    latest_version_created_at: datetime
    created_by: UUID | None = None
    created_at: datetime
    updated_at: datetime


class AgentReviewDefinitionsPage(BaseModel):
    items: list[AgentReviewDefinitionPublic]
    total: int
    limit: int
    offset: int


class AgentReviewVersionsPage(BaseModel):
    items: list[AgentReviewVersionPublic]
    total: int
    limit: int
    offset: int


class AgentReviewRunCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_ids: list[UUID] = Field(min_length=1, max_length=20)


class AgentReviewRunAccepted(BaseModel):
    review_run_id: UUID
    job_id: UUID
    reused: bool
    notification_id: UUID | None = None


class AgentReviewSourceRef(BaseModel):
    run_id: UUID
    agent_id: UUID | None
    org_id: UUID | None = None
    root_run_id: UUID | None = None
    parent_run_id: UUID | None = None
    trigger_type: str | None = None


class AgentReviewRunPublic(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    review_id: UUID
    review_version_id: UUID
    review_version: int
    agent_id: UUID
    org_id: UUID | None = None
    platform_job_id: UUID | None = None
    selected_run_ids: list[UUID]
    source_refs: list[AgentReviewSourceRef]
    result_summary: str | None = None
    created_at: datetime


class FindingCreate(BaseModel):
    """Body for ``POST /api/agent-findings``."""

    model_config = ConfigDict(extra="forbid")

    agent_id: UUID
    description: str = Field(min_length=1, max_length=4000)
    expected_behavior: str | None = Field(default=None, max_length=4000)
    source_kind: FindingSourceKind = "manual"
    source_run_id: UUID | None = None
    source_sequence: int | None = Field(default=None, ge=0)
    external_ref: str | None = Field(default=None, max_length=500)
    finding_kind: FindingKind = "problem"
    evidence_markdown: str | None = Field(default=None, max_length=20000)


class FindingUpdate(BaseModel):
    """Body for ``PATCH /api/agent-findings/{id}``."""

    model_config = ConfigDict(extra="forbid")

    description: str | None = Field(default=None, min_length=1, max_length=4000)
    expected_behavior: str | None = Field(default=None, max_length=4000)
    status: FindingStatus | None = None
    finding_kind: FindingKind | None = None
    evidence_markdown: str | None = Field(default=None, max_length=20000)


class FindingPublic(BaseModel):
    """A finding with its linked regression case ids (computed, not stored)."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    agent_id: UUID
    org_id: UUID | None = None
    status: str = "open"
    description: str
    expected_behavior: str | None = None
    source_kind: str = "manual"
    source_run_id: UUID | None = None
    source_sequence: int | None = None
    external_ref: str | None = None
    finding_kind: str = "problem"
    evidence_markdown: str | None = None
    source_review_id: UUID | None = None
    source_review_version_id: UUID | None = None
    source_review_run_id: UUID | None = None
    source_review_version: int | None = None
    source_run_refs: list[dict[str, Any]] = Field(default_factory=list)
    source_ordinal: int | None = None
    linked_case_ids: list[UUID] = Field(default_factory=list)
    created_by: UUID | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class FindingSearchPage(BaseModel):
    items: list[FindingPublic]
    total: int
    limit: int
    offset: int


class AgentReviewRunResults(BaseModel):
    review_run: AgentReviewRunPublic
    findings: list[FindingPublic]
    usage_operation_type: Literal["agent_review"] = "agent_review"
    usage_operation_id: UUID


class AgentRecordedEvaluationPayload(BaseModel):
    evaluation_id: UUID


class SyntheticSemanticJudgePayload(BaseModel):
    execution_id: UUID
    result_id: UUID


class AgentReviewJobPayload(BaseModel):
    review_run_id: UUID


class QualityUsageTotals(BaseModel):
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cache_write_tokens: int
    call_count: int
    duration_ms: int
    duration_missing_count: int
    observed_provider_cost: Decimal
    estimated_cost: Decimal
    known_cost: Decimal
    missing_cost_call_count: int
    legacy_call_count: int

    @field_serializer(
        "observed_provider_cost",
        "estimated_cost",
        "known_cost",
        when_used="json",
    )
    def _serialize_decimal(self, value: Decimal) -> str:
        return str(value)


class QualityUsageCoverage(BaseModel):
    started_attempt_count: int
    unobserved_attempt_count: int
    missing_cost_call_count: int
    unassigned_operation_call_count: int
    legacy_coverage_unknown: bool | None = None
    legacy_call_count: int | None = None


class QualityUsagePurposeRow(BaseModel):
    purpose: str | None
    totals: QualityUsageTotals
    coverage: QualityUsageCoverage


class QualityUsageProviderModelRow(BaseModel):
    purpose: str | None
    provider: str | None
    model: str | None
    totals: QualityUsageTotals
    coverage: QualityUsageCoverage


class QualityUsageProfileRow(BaseModel):
    purpose: str | None
    profile_id: UUID | None
    profile_name: str | None
    profile_fingerprint: str | None
    provider: str | None
    model: str | None
    totals: QualityUsageTotals
    coverage: QualityUsageCoverage


class QualityUsageOrganizationRow(BaseModel):
    organization_id: UUID | None
    organization_name: str | None
    totals: QualityUsageTotals
    coverage: QualityUsageCoverage


class QualityUsageOperationRow(BaseModel):
    operation_type: str | None
    operation_id: UUID | None
    purpose: str | None
    totals: QualityUsageTotals
    coverage: QualityUsageCoverage


QualityUsageRowT = TypeVar("QualityUsageRowT")


class QualityUsagePage(BaseModel, Generic[QualityUsageRowT]):
    items: list[QualityUsageRowT]
    total_groups: int
    limit: int
    offset: int
    omitted_group_count: int


class QualityUsageBreakdownResponse(BaseModel):
    overall: QualityUsageTotals
    coverage: QualityUsageCoverage
    by_purpose: QualityUsagePage[QualityUsagePurposeRow]
    by_provider_model: QualityUsagePage[QualityUsageProviderModelRow]
    by_profile: QualityUsagePage[QualityUsageProfileRow]
    by_organization: QualityUsagePage[QualityUsageOrganizationRow]
    by_operation: QualityUsagePage[QualityUsageOperationRow]

QualityUsageOperationType = Literal[
    "recorded_evaluation",
    "synthetic_evaluation",
    "agent_review",
    "quality_designer",
]
QualityUsagePurpose = Literal[
    "recorded_semantic_judge",
    "synthetic_semantic_judge",
    "agent_review",
    "test_designer",
]
QualityUsageUnobservedReason = Literal[
    "provider_error",
    "missing_usage",
    "ambiguous_after_call",
    "cancelled_before_call",
    "runner_lost",
]


RecurringTriggerOperationType = Literal["agent_review", "agent_evaluation_suite"]
RecurringTriggerOverlapPolicy = Literal["skip", "queue", "replace"]
TriggerFireStatus = Literal["claimed", "admitted", "skipped", "failed"]


class RecurringTriggerCreate(BaseModel):
    """Body for POST /api/recurring-triggers. Requester is always the caller.

    Organization is derived from the operation target and immutable after
    creation; there is no caller-supplied org scope.
    """

    model_config = ConfigDict(extra="forbid")

    operation_type: RecurringTriggerOperationType
    operation_id: UUID
    operation_params: dict[str, Any] = Field(default_factory=dict)
    cron_expression: str = Field(min_length=1, max_length=120)
    timezone: str = Field(default="UTC", max_length=80)
    overlap_policy: RecurringTriggerOverlapPolicy = "skip"


class RecurringTriggerUpdate(BaseModel):
    """Body for PATCH /api/recurring-triggers/{id}. Identity is immutable."""

    model_config = ConfigDict(extra="forbid")

    cron_expression: str | None = Field(default=None, min_length=1, max_length=120)
    timezone: str | None = Field(default=None, max_length=80)
    enabled: bool | None = None
    operation_params: dict[str, Any] | None = None


class RecurringTriggerPublic(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    org_id: UUID | None = None
    operation_type: str
    operation_id: UUID
    operation_params: dict[str, Any] = Field(default_factory=dict)
    cron_expression: str
    timezone: str
    enabled: bool
    overlap_policy: str
    requested_by_user_id: UUID
    requested_by_email: str
    requested_by_name: str
    created_at: datetime | None = None
    updated_at: datetime | None = None


class RecurringTriggerPage(BaseModel):
    items: list[RecurringTriggerPublic]
    total: int
    limit: int
    offset: int


class TriggerFirePublic(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    trigger_id: UUID
    scheduled_for: datetime
    status: str
    reason: str | None = None
    platform_job_id: UUID | None = None
    domain_run_id: UUID | None = None
    attempt_count: int = 1
    created_at: datetime | None = None
    updated_at: datetime | None = None


class TriggerFirePage(BaseModel):
    items: list[TriggerFirePublic]
    total: int
    limit: int
    offset: int


class AgentTestCreate(BaseModel):
    """Body for agent-wide test creation (lands in the default collection)."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., min_length=1, max_length=255)
    position: int = 0
    enabled: bool = True
    input: dict[str, Any] | None = None
    fixture: dict[str, Any] = Field(default_factory=dict)
    simulator_policy: dict[str, Any] = Field(default_factory=dict)
    assertions: list[dict[str, Any]] = Field(default_factory=list)
    expected_tools: list[str] = Field(default_factory=list)
    forbidden_tools: list[str] = Field(default_factory=list)
    output_schema: dict[str, Any] | None = None
    repetitions: int = Field(default=1, ge=1, le=10)
    scoring_policy: dict[str, Any] = Field(default_factory=dict)
    provenance: str = "manual"
    provenance_run_ids: list[UUID] = Field(default_factory=list)
    finding_id: UUID | None = None
    tags: list[str] = Field(default_factory=list)


class AgentTestUpdate(BaseModel):
    """Body for agent-wide test edits (inserts the next accepted version)."""

    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=255)
    position: int | None = None
    enabled: bool | None = None
    input: dict[str, Any] | None = None
    fixture: dict[str, Any] | None = None
    simulator_policy: dict[str, Any] | None = None
    assertions: list[dict[str, Any]] | None = None
    expected_tools: list[str] | None = None
    forbidden_tools: list[str] | None = None
    output_schema: dict[str, Any] | None = None
    repetitions: int | None = Field(default=None, ge=1, le=10)
    scoring_policy: dict[str, Any] | None = None
    tags: list[str] | None = None
    expected_version: int | None = None


class AgentTestPublic(BaseModel):
    """Current accepted version of one logical agent-wide test."""

    logical_test_id: UUID
    version: int
    origin_suite_id: UUID
    origin_suite_name: str
    origin_is_default: bool
    case_id: UUID
    name: str
    position: int
    enabled: bool
    input: dict[str, Any] | None = None
    fixture: dict[str, Any] = Field(default_factory=dict)
    simulator_policy: dict[str, Any] = Field(default_factory=dict)
    assertions: list[dict[str, Any]] = Field(default_factory=list)
    expected_tools: list[str] = Field(default_factory=list)
    forbidden_tools: list[str] = Field(default_factory=list)
    output_schema: dict[str, Any] | None = None
    repetitions: int = 1
    scoring_policy: dict[str, Any] = Field(default_factory=dict)
    provenance: str = "manual"
    provenance_run_ids: list[str] = Field(default_factory=list)
    finding_id: UUID | None = None
    tags: list[str] = Field(default_factory=list)
    created_at: datetime | None = None
    updated_at: datetime | None = None


class AgentTestPage(BaseModel):
    items: list[AgentTestPublic]
    total: int
    limit: int
    offset: int


class SimulationLatestPublic(BaseModel):
    execution_id: UUID
    case_version: int
    profile_id: UUID | None = None
    candidate_id: UUID | None = None
    status: str
    created_at: datetime | None = None


class RecordedLatestPublic(BaseModel):
    evaluation_id: UUID
    run_id: UUID
    case_version: int
    outcome: str
    applicability: str
    judge_mode: str | None = None
    created_at: datetime | None = None


class AgentTestLatestPublic(BaseModel):
    logical_test_id: UUID
    version: int
    origin_suite_name: str
    simulation: SimulationLatestPublic | None = None
    recorded: RecordedLatestPublic | None = None


class AgentTestLatestPage(BaseModel):
    items: list[AgentTestLatestPublic]
    total: int
    limit: int
    offset: int


class TestRunSelection(BaseModel):
    """One explicitly selected accepted test version."""

    model_config = ConfigDict(extra="forbid")

    case_id: UUID
    case_version: int = Field(ge=1)


class AgentTestsRunCreate(BaseModel):
    """Body for running explicitly selected test versions (one suite)."""

    model_config = ConfigDict(extra="forbid")

    selections: list[TestRunSelection] = Field(min_length=1, max_length=100)
    candidate_id: UUID | None = None
    profile_id: UUID | None = None
    repetitions_override: int | None = Field(default=None, ge=1, le=10)


class AgentTestsGenerateCreate(BaseModel):
    """Body for generating tests from explicitly selected findings."""

    model_config = ConfigDict(extra="forbid")

    finding_ids: list[UUID] = Field(min_length=1, max_length=10)
    requested_count: int = Field(default=3, ge=1, le=10)
    suite_goal: str | None = Field(default=None, max_length=4000)
