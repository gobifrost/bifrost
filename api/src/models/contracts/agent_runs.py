"""Agent run contract models."""
from datetime import datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from src.models.contracts.executions import AIUsagePublicSimple, AIUsageTotalsSimple


class AgentRunStepResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    run_id: UUID
    step_number: int
    type: str
    content: dict | None = None
    tokens_used: int | None = None
    duration_ms: int | None = None
    created_at: datetime


class AgentRunResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    agent_id: UUID
    agent_name: str | None = None
    trigger_type: str
    trigger_source: str | None = None
    conversation_id: UUID | None = None
    event_delivery_id: UUID | None = None
    input: dict | None = None
    output: dict | None = None
    status: str
    error: str | None = None
    org_id: UUID | None = None
    caller_user_id: str | None = None
    caller_email: str | None = None
    caller_name: str | None = None
    iterations_used: int
    tokens_used: int
    budget_max_iterations: int | None = None
    budget_max_tokens: int | None = None
    duration_ms: int | None = None
    llm_model: str | None = None
    asked: str | None = None
    did: str | None = None
    answered: str | None = None
    metadata: dict[str, str] = Field(default_factory=dict)
    confidence: float | None = None
    confidence_reason: str | None = None
    summary_status: str = "pending"
    summary_error: str | None = None
    summary_prompt_version: str | None = None
    verdict: str | None = None
    verdict_note: str | None = None
    verdict_set_at: datetime | None = None
    verdict_set_by: UUID | None = None
    created_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None
    parent_run_id: UUID | None = None


class AgentRunChildResponse(BaseModel):
    """User-facing summary of a delegated child run."""

    id: UUID
    agent_id: UUID
    agent_name: str
    status: str
    asked: str | None = None
    did: str | None = None
    answered: str | None = None
    duration_ms: int | None = None
    created_at: datetime


class AgentRunDetailResponse(AgentRunResponse):
    steps: list[AgentRunStepResponse] = Field(default_factory=list)
    child_run_ids: list[UUID] = Field(default_factory=list)
    child_runs: list[AgentRunChildResponse] = Field(default_factory=list)
    ai_usage: list[AIUsagePublicSimple] | None = None
    ai_totals: AIUsageTotalsSimple | None = None


class AgentRunListResponse(BaseModel):
    items: list[AgentRunResponse]
    total: int
    next_cursor: str | None = None


class AgentRunCreateRequest(BaseModel):
    agent_name: str
    input: dict | None = None
    output_schema: dict | None = None
    timeout: int = 1800


class AgentRunRerunResponse(BaseModel):
    run_id: UUID


class VerdictRequest(BaseModel):
    verdict: Literal["up", "down"]
    note: str | None = Field(default=None, max_length=2000)


class VerdictResponse(BaseModel):
    run_id: UUID
    verdict: str | None = None
    verdict_note: str | None = None
    verdict_set_at: datetime | None = None
    verdict_set_by: UUID | None = None


class DryRunRequest(BaseModel):
    """Evaluate a proposed system prompt against a single completed run."""

    proposed_prompt: str = Field(min_length=1, max_length=20000)


class DryRunResponse(BaseModel):
    """Result of a single-run dry-run evaluation."""

    run_id: UUID
    would_still_decide_same: bool
    reasoning: str
    alternative_action: str | None = None
    confidence: float


class PausedResponse(BaseModel):
    """Returned by /agent-runs/execute when the target agent is paused.

    Returned with HTTP 200 — pause is a graceful, expected state, not an error.
    Downstream consumers discriminate on ``status == "paused"``.
    """
    status: Literal["paused"] = "paused"
    accepted: Literal[False] = False
    message: str
    agent_id: UUID


class BackfillSummariesRequest(BaseModel):
    """Admin-triggered bulk backfill of run summaries."""

    agent_id: UUID | None = Field(
        default=None,
        description="Scope to a single agent. None means platform-wide.",
    )
    statuses: list[Literal["pending", "failed", "completed"]] = Field(
        default_factory=lambda: ["pending", "failed"],
        description=(
            "Which summary_status values to re-run. Include 'completed' to "
            "re-summarize already-summarized runs (use with "
            "``prompt_version_below`` to target old prompt versions)."
        ),
    )
    prompt_version_below: str | None = Field(
        default=None,
        description=(
            "Only re-summarize runs whose ``summary_prompt_version`` is "
            "less than this value (lexicographic), or NULL. Lets admins "
            "roll forward runs tagged with older prompt versions after a "
            "prompt change. NULL-versioned runs always match."
        ),
    )
    limit: int = Field(
        default=500,
        ge=1,
        le=5000,
        description="Max runs to enqueue in one backfill.",
    )
    dry_run: bool = Field(
        default=False,
        description="If true, return eligible count + cost estimate without enqueuing.",
    )


class BackfillSummariesResponse(BaseModel):
    """Result of a backfill request."""

    job_id: UUID | None = Field(
        default=None,
        description="Orchestration row ID. None when dry_run=true.",
    )
    queued: int = Field(description="Number of runs enqueued (0 if dry_run).")
    eligible: int = Field(description="Total matched by the filter.")
    estimated_cost_usd: Decimal = Field(
        description="Best-effort cost prediction based on recent summarizer history."
    )
    cost_basis: Literal["history", "fallback"] = Field(
        description="Whether the estimate is derived from past runs or a flat fallback.",
    )


class SummaryBackfillJobResponse(BaseModel):
    """Snapshot of a SummaryBackfillJob orchestration row."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    agent_id: UUID | None = None
    requested_by: UUID
    status: str
    total: int
    succeeded: int
    failed: int
    estimated_cost_usd: Decimal
    actual_cost_usd: Decimal
    created_at: datetime
    completed_at: datetime | None = None


class SummaryBackfillJobListResponse(BaseModel):
    items: list[SummaryBackfillJobResponse]


class MetadataKeysResponse(BaseModel):
    """Distinct top-level keys observed in agent-run metadata for an agent.

    Powers the key combobox on the runs-list captured-data filter.
    """
    keys: list[str] = Field(default_factory=list)


class MetadataValuesResponse(BaseModel):
    """Distinct values observed for a given metadata key on an agent's runs.

    Powers the value combobox when the user picks the 'eq' operator.
    """
    values: list[str] = Field(default_factory=list)


class BackfillEligibleResponse(BaseModel):
    """Lightweight count + estimate used by the UI to decide whether to
    surface the Backfill button at all. Mirrors the shape of the dry-run
    POST but cacheable and cheaper (no queue touch)."""

    eligible: int = Field(description="Number of runs that would be backfilled.")
    estimated_cost_usd: Decimal = Field(
        description="Best-effort cost estimate for this scope."
    )
    cost_basis: Literal["history", "fallback"] = Field(
        description="Whether the estimate is derived from past runs or a flat fallback.",
    )
