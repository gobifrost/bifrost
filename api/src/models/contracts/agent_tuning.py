"""Consolidated agent tuning session contracts.

A *consolidated tuning session* takes all currently-flagged runs for an
agent (verdict='down'), feeds them and their per-flag tuning conversations
to the tuning model in a single shot, and proposes one consolidated prompt
change. The proposal seeds an evaluation-only candidate; applying a
reviewed candidate goes through the normal authorized
``PUT /api/agents/{id}`` (history + stale guard, verdicts preserved).
The legacy verdict-clearing ``POST .../tuning-session/apply`` endpoint was
removed (410 Gone).
"""
from uuid import UUID

from pydantic import BaseModel, Field


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


class ConsolidatedDryRunRequest(BaseModel):
    """Body for ``POST /api/agents/{id}/tuning-session/dry-run``."""

    proposed_prompt: str = Field(min_length=1, max_length=20000)


class ConsolidatedDryRunResponse(BaseModel):
    """Aggregated per-run dry-run results."""

    results: list[DryRunPerRun]
