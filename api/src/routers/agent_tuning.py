"""Consolidated agent tuning session endpoints.

Three endpoints under ``/api/agents/{id}/tuning-session``:

- ``POST /``: analyze all flagged runs + their tuning conversations,
  return a consolidated proposal (summary + proposed_prompt +
  affected_run_ids).
- ``POST /dry-run``: evaluate a proposed prompt against each flagged run
  (capped at the first 10) and return per-run verdicts.
- ``POST /apply``: persist the new prompt on the agent, write an
  ``AgentPromptHistory`` row, and clear verdicts on the affected flagged
  runs so they re-enter the unreviewed queue.

These endpoints are mounted on the same prefix as the agent CRUD router
(``/api/agents``) but live in a separate router file because the surface
is logically distinct (tuning lifecycle, not agent metadata).
"""
import logging
from uuid import UUID

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select

from src.core.auth import CurrentActiveUser
from src.core.database import get_session_factory
from src.core.db_deps import DbSession
from src.models.contracts.agent_tuning import (
    ApplyTuningRequest,
    ApplyTuningResponse,
    ConsolidatedDryRunRequest,
    ConsolidatedDryRunResponse,
    ConsolidatedProposalResponse,
    DryRunPerRun,
)
from src.models.orm.agents import Agent
from src.services.execution.tuning_service import (
    apply_consolidated_tuning,
    dry_run_consolidated,
    propose_consolidated_tuning,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/agents", tags=["Agent Tuning"])


async def _load_agent_with_access(
    agent_id: UUID, db: DbSession, user: CurrentActiveUser
) -> Agent:
    """Fetch an agent and enforce org scoping for non-superusers.

    Org users can only tune agents in their own org (or global agents,
    where ``organization_id is None``). Platform admins can tune any.
    """
    is_admin = user.is_superuser or any(
        role in ["Platform Admin", "Platform Owner"] for role in user.roles
    )

    agent = (
        await db.execute(select(Agent).where(Agent.id == agent_id))
    ).scalar_one_or_none()
    if agent is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Agent {agent_id} not found",
        )

    if not is_admin:
        if (
            agent.organization_id is not None
            and agent.organization_id != user.organization_id
        ):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Agent {agent_id} not found",
            )

    return agent


@router.post(
    "/{agent_id}/tuning-session",
    response_model=ConsolidatedProposalResponse,
)
async def create_tuning_session(
    agent_id: UUID,
    db: DbSession,
    user: CurrentActiveUser,
) -> ConsolidatedProposalResponse:
    """Generate a consolidated prompt proposal from this agent's flagged runs."""
    await _load_agent_with_access(agent_id, db, user)

    try:
        proposal = await propose_consolidated_tuning(agent_id, db)
    except LookupError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)
        )

    return ConsolidatedProposalResponse(
        summary=proposal.summary,
        proposed_prompt=proposal.proposed_prompt,
        affected_run_ids=proposal.affected_run_ids,
    )


@router.post(
    "/{agent_id}/tuning-session/dry-run",
    response_model=ConsolidatedDryRunResponse,
)
async def dry_run_tuning_session(
    agent_id: UUID,
    request: ConsolidatedDryRunRequest,
    db: DbSession,
    user: CurrentActiveUser,
) -> ConsolidatedDryRunResponse:
    """Per-run dry-run of a proposed prompt across this agent's flagged runs.

    Capped at 10 runs by the service layer to bound cost.
    """
    await _load_agent_with_access(agent_id, db, user)

    session_factory = get_session_factory()
    raw = await dry_run_consolidated(
        agent_id=agent_id,
        proposed_prompt=request.proposed_prompt,
        db=db,
        session_factory=session_factory,
    )
    return ConsolidatedDryRunResponse(
        results=[
            DryRunPerRun(
                run_id=run_id,
                would_still_decide_same=same,
                reasoning=reasoning,
                confidence=confidence,
            )
            for run_id, same, reasoning, confidence in raw
        ]
    )


@router.post(
    "/{agent_id}/tuning-session/apply",
    response_model=ApplyTuningResponse,
)
async def apply_tuning_session(
    agent_id: UUID,
    request: ApplyTuningRequest,
    db: DbSession,
    user: CurrentActiveUser,
) -> ApplyTuningResponse:
    """Apply a consolidated tuning proposal: update prompt, write history, clear verdicts."""
    await _load_agent_with_access(agent_id, db, user)

    try:
        applied = await apply_consolidated_tuning(
            agent_id=agent_id,
            new_prompt=request.new_prompt,
            reason=request.reason,
            user_id=user.user_id,
            db=db,
        )
    except LookupError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)
        )

    await db.commit()

    return ApplyTuningResponse(
        agent_id=applied.agent_id,
        history_id=applied.history_id,
        affected_run_ids=applied.affected_run_ids,
    )
