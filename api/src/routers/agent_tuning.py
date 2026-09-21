"""Consolidated agent tuning session endpoints.

Two live endpoints under ``/api/agents/{id}/tuning-session``:

- ``POST /``: analyze all flagged runs + their tuning conversations,
  return a consolidated proposal (summary + proposed_prompt +
  affected_run_ids).
- ``POST /dry-run``: evaluate a proposed prompt against each flagged run
  (capped at the first 10) and return per-run verdicts.

``POST /apply`` is gone (410): the legacy apply cleared flagged verdicts
so runs re-entered review under the new prompt. The replacement applies
reviewed changes through the normal authorized ``PUT /api/agents/{id}``
(which records ``AgentPromptHistory`` and never touches verdicts,
findings, or evidence) with an ``If-Unmodified-Since`` stale guard.

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
    ConsolidatedDryRunRequest,
    ConsolidatedDryRunResponse,
    ConsolidatedProposalResponse,
    DryRunPerRun,
)
from src.models.orm.agents import Agent
from src.services.execution.tuning_service import (
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
    is_admin = user.has_platform_admin_grant()

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
        proposal = await propose_consolidated_tuning(agent_id, db, user)
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
        user=user,
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
    status_code=status.HTTP_410_GONE,
)
async def apply_tuning_session(
    agent_id: UUID,
    db: DbSession,
    user: CurrentActiveUser,
) -> None:
    """Gone: apply reviewed changes via ``PUT /api/agents/{id}``.

    The legacy apply cleared flagged verdicts; the replacement preserves
    verdicts, findings, and evidence, records ``AgentPromptHistory``
    through the normal authorized update, and guards stale diffs with
    ``If-Unmodified-Since``.
    """
    await _load_agent_with_access(agent_id, db, user)
    raise HTTPException(
        status_code=status.HTTP_410_GONE,
        detail=(
            "The tuning-session apply endpoint was removed because it "
            "cleared flagged verdicts. Apply reviewed changes through "
            "PUT /api/agents/{agent_id} with change_reason and "
            "If-Unmodified-Since; verdicts, findings, and evidence are "
            "preserved."
        ),
    )
