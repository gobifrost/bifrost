"""Consolidated agent tuning session contracts.

A *consolidated tuning session* takes all currently-flagged runs for an
agent (verdict='down'), feeds them and their per-flag tuning conversations
to the tuning model in a single shot, and proposes one consolidated prompt
change. The user can dry-run the proposal across the same set of flagged
runs and apply it; applying records an :class:`AgentPromptHistory` row,
updates :class:`Agent.system_prompt`, and clears the verdict on the
affected runs so they re-enter the review queue under the new prompt.
"""
from uuid import UUID

from pydantic import BaseModel, Field

from src.models.contracts.agents import AGENT_PROMPT_MAX_LENGTH


class ConsolidatedProposalResponse(BaseModel):
    """Output of ``POST /api/agents/{id}/tuning-session``.

    ``affected_run_ids`` is the list of flagged runs that informed the
    proposal — this is what the dry-run/apply endpoints will operate on.
    """

    summary: str
    proposed_prompt: str
    affected_run_ids: list[UUID]


class DryRunPerRun(BaseModel):
    """Per-run dry-run verdict in a consolidated dry-run response."""

    run_id: UUID
    would_still_decide_same: bool
    reasoning: str
    confidence: float
    evaluation_failed: bool = False


class ConsolidatedDryRunRequest(BaseModel):
    """Body for ``POST /api/agents/{id}/tuning-session/dry-run``."""

    proposed_prompt: str = Field(min_length=1, max_length=AGENT_PROMPT_MAX_LENGTH)


class ConsolidatedDryRunResponse(BaseModel):
    """Aggregated per-run dry-run results."""

    results: list[DryRunPerRun]


class ApplyTuningRequest(BaseModel):
    """Body for ``POST /api/agents/{id}/tuning-session/apply``."""

    new_prompt: str = Field(min_length=1, max_length=AGENT_PROMPT_MAX_LENGTH)
    reason: str | None = Field(default=None, max_length=500)


class ApplyTuningResponse(BaseModel):
    """Result of applying a consolidated tuning proposal."""

    agent_id: UUID
    history_id: UUID
    affected_run_ids: list[UUID]
