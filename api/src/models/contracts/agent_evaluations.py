"""Agent Evaluation Studio contract models.

Stable language shared by the REST API, CLI, and future Studio UI
(spec R3): suite, case, candidate, execution, result, assertion,
comparison, and simulation vocabulary is identical everywhere.

Security: fixtures and evidence are redacted before leaving the server
(secret-bearing keys replaced with ``[REDACTED]``). No model here carries
credentials, lease tokens, or unredacted secret values.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

Provenance = Literal["manual", "generated", "historical_inspiration", "finding"]
SuiteStatus = Literal["draft", "published", "archived"]
ExecutionStatus = Literal[
    "queued", "running", "waiting", "succeeded", "failed", "cancelled"
]
ResultStatus = Literal["pending", "running", "passed", "failed", "error"]


# -----------------------------------------------------------------------------
# Assertions
# -----------------------------------------------------------------------------


class EvaluationAssertion(BaseModel):
    """One check evaluated against a finished synthetic AgentRun.

    ``type`` selects the evaluator; ``params`` carries its configuration.
    Unknown types fail closed at case save time, never at result time.
    """

    model_config = ConfigDict(extra="forbid")

    type: str = Field(
        ...,
        description=(
            "Assertion type: terminal_status, output_schema, output_path, "
            "tool_called, tool_not_called, tool_count, tool_order, tool_args, "
            "simulator_state, delegation_tree, max_iterations, max_tokens, "
            "max_cost_usd, max_latency_ms, no_real_tools, and llm_judge. "
            "llm_judge is a platform-admin-only, non-authoritative semantic "
            "observation configured by judge_profile_id and frozen on save."
        ),
    )
    params: dict[str, Any] = Field(default_factory=dict)
    # Optional stable label used in evidence-first comparison views.
    label: str | None = None


class AssertionOutcome(BaseModel):
    """Evaluated assertion: stable code, pass/fail, redacted evidence."""

    model_config = ConfigDict(extra="forbid")

    code: str
    type: str
    label: str | None = None
    passed: bool
    expected: Any = None
    actual: Any = None
    # Journal/tool-record sequence IDs backing the verdict.
    evidence_sequences: list[int] = Field(default_factory=list)
    detail: str | None = None


# -----------------------------------------------------------------------------
# Suites and cases
# -----------------------------------------------------------------------------


class EvaluationSuiteCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=2000)
    agent_id: UUID | None = Field(
        default=None, description="Baseline target Agent for this suite."
    )
    organization_id: UUID | None = None


class EvaluationSuiteUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=2000)
    agent_id: UUID | None = None
    expected_version: int | None = Field(
        default=None,
        description="Optimistic concurrency guard for mutable drafts.",
    )


class EvaluationSuitePublic(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    org_id: UUID | None = None
    agent_id: UUID | None = None
    name: str
    description: str | None = None
    status: str
    version: int
    created_by: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class EvaluationCaseCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., min_length=1, max_length=255)
    position: int = 0
    enabled: bool = True
    input: dict[str, Any] | None = None
    fixture: dict[str, Any] = Field(default_factory=dict)
    simulator_policy: dict[str, Any] = Field(default_factory=dict)
    assertions: list[EvaluationAssertion] = Field(default_factory=list)
    expected_tools: list[str] = Field(default_factory=list)
    forbidden_tools: list[str] = Field(default_factory=list)
    output_schema: dict[str, Any] | None = None
    repetitions: int = Field(default=1, ge=1, le=10)
    scoring_policy: dict[str, Any] = Field(default_factory=dict)
    provenance: Provenance = "manual"
    provenance_run_ids: list[UUID] = Field(default_factory=list)
    finding_id: UUID | None = Field(
        default=None,
        description="Reviewed finding this case reproduces. Forces provenance to finding.",
    )
    tags: list[str] = Field(default_factory=list)


class EvaluationCaseUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = None
    position: int | None = None
    enabled: bool | None = None
    input: dict[str, Any] | None = None
    fixture: dict[str, Any] | None = None
    simulator_policy: dict[str, Any] | None = None
    assertions: list[EvaluationAssertion] | None = None
    expected_tools: list[str] | None = None
    forbidden_tools: list[str] | None = None
    output_schema: dict[str, Any] | None = None
    repetitions: int | None = Field(default=None, ge=1, le=10)
    scoring_policy: dict[str, Any] | None = None
    tags: list[str] | None = None
    expected_version: int | None = None


class EvaluationCasePublic(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    suite_id: UUID
    name: str
    position: int
    enabled: bool
    version: int
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
    accepted: bool = True
    created_at: datetime | None = None
    updated_at: datetime | None = None


# -----------------------------------------------------------------------------
# Candidates
# -----------------------------------------------------------------------------


class CandidateOverlay(BaseModel):
    """Optional overrides applied to the base Agent inside test runs only."""

    model_config = ConfigDict(extra="forbid")

    system_prompt: str | None = None
    llm_profile_id: UUID | None = None
    llm_max_tokens: int | None = Field(default=None, ge=1, le=200000)
    tool_ids: list[UUID] | None = None
    delegated_agent_ids: list[UUID] | None = None
    system_tools: list[str] | None = None
    max_iterations: int | None = Field(default=None, ge=1, le=200)
    max_token_budget: int | None = Field(default=None, ge=1)
    max_run_timeout: int | None = Field(default=None, ge=0)
    output_schema: dict[str, Any] | None = None


class CandidateCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    base_agent_id: UUID
    name: str | None = Field(default=None, max_length=255)
    overlays: CandidateOverlay = Field(default_factory=CandidateOverlay)
    organization_id: UUID | None = None


class CandidatePublic(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    org_id: UUID | None = None
    base_agent_id: UUID | None = None
    base_agent_updated_at: datetime | None = None
    name: str | None = None
    overlays: dict[str, Any] = Field(default_factory=dict)
    snapshot: dict[str, Any] = Field(default_factory=dict)
    snapshot_hash: str | None = None
    evaluation_only: bool = True
    created_by: str | None = None
    created_at: datetime | None = None


class DesignerDraftRequest(BaseModel):
    """Server-authorized asynchronous Test Designer request."""

    model_config = ConfigDict(extra="forbid")

    suite_goal: str = Field(..., min_length=1, max_length=4000)
    requested_count: int = Field(default=4, ge=1, le=10)
    historical_run_ids: list[UUID] = Field(default_factory=list, max_length=20)


class DesignerDraftAccepted(BaseModel):
    run_id: UUID
    status: Literal["queued"] = "queued"


# -----------------------------------------------------------------------------
# Executions and results
# -----------------------------------------------------------------------------


class EvaluationExecutionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    suite_id: UUID
    candidate_id: UUID | None = Field(
        default=None,
        description="Null runs the baseline Agent alone.",
    )
    repetitions_override: int | None = Field(default=None, ge=1, le=10)


class EvaluationExecutionBatchCreate(BaseModel):
    """Saved multi-profile matrix admission.

    Fans out to one atomic execution per (candidate-or-baseline, profile)
    cell. Every candidate cell pairs with the baseline-only cell under the
    same profile; the selected profile is frozen into both snapshots at
    admission and never falls back to another profile.
    """

    model_config = ConfigDict(extra="forbid")

    suite_id: UUID
    candidate_ids: list[UUID] = Field(default_factory=list, max_length=10)
    profile_ids: list[UUID] = Field(min_length=1, max_length=10)
    repetitions_override: int | None = Field(default=None, ge=1, le=10)


class MatrixCellPublic(BaseModel):
    model_config = ConfigDict(extra="forbid")

    execution_id: UUID
    candidate_id: UUID | None = None
    profile_id: UUID | None = None
    status: str
    total_cases: int = 0
    completed_cases: int = 0
    passed_cases: int = 0
    failed_cases: int = 0
    platform_job_id: UUID | None = None
    reused: bool = False


class EvaluationMatrixPublic(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    suite_id: UUID
    suite_version: int
    candidate_ids: list[str] = Field(default_factory=list)
    profile_ids: list[str] = Field(default_factory=list)
    repetitions_override: int | None = None
    cell_execution_ids: list[str] = Field(default_factory=list)
    org_id: UUID | None = None
    created_by: str | None = None
    created_at: datetime | None = None


class EvaluationBatchResult(BaseModel):
    """Matrix admission aggregate. Status is computed from live cells."""

    model_config = ConfigDict(extra="forbid")

    matrix: EvaluationMatrixPublic
    cells: list[MatrixCellPublic]
    planned_cells: int
    planned_runs: int
    cost_estimate_usd: float | None = None
    cost_note: str


class EvaluationExecutionPublic(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    suite_id: UUID
    suite_version: int
    candidate_id: UUID | None = None
    baseline_agent_id: UUID | None = None
    matrix_id: UUID | None = None
    profile_id: UUID | None = None
    status: str
    total_cases: int = 0
    completed_cases: int = 0
    passed_cases: int = 0
    failed_cases: int = 0
    platform_job_id: UUID | None = None
    error: str | None = None
    created_by: str | None = None
    created_at: datetime | None = None
    completed_at: datetime | None = None


class EvaluationResultPublic(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    execution_id: UUID
    case_id: UUID
    case_version: int
    repetition_index: int = 0
    baseline_run_id: UUID | None = None
    candidate_run_id: UUID | None = None
    status: str
    assertion_results: list[dict[str, Any]] = Field(default_factory=list)
    comparison: dict[str, Any] | None = None
    tokens_used: int | None = None
    turns_used: int | None = None
    duration_ms: int | None = None
    cost_usd: str | None = None
    simulator_state_hash: str | None = None
    error: str | None = None
    created_at: datetime | None = None


class EvaluationComparison(BaseModel):
    """Baseline-versus-candidate delta: evidence first, prose never gates."""

    model_config = ConfigDict(extra="forbid")

    regressions: list[str] = Field(default_factory=list)
    improvements: list[str] = Field(default_factory=list)
    unchanged_failures: list[str] = Field(default_factory=list)
    tool_trajectory_differences: list[dict[str, Any]] = Field(default_factory=list)
    output_differences: list[dict[str, Any]] = Field(default_factory=list)
    usage_delta: dict[str, Any] = Field(default_factory=dict)
    verdict: Literal["regression", "improvement", "unchanged", "mixed"] = "unchanged"
