"""First-class agent finding endpoints.

Tenant-authorized CRUD for reviewed findings (observed problem, expected
behavior, source reference). Findings may be dismissed without creating a
test; tests may exist without a finding. A passing test never resolves a
finding automatically.

Authorization has two independent layers (never the legacy tuning
org-only check):

- the referenced agent must be visible through ``_authorized_agent``;
- the finding itself is scoped by *caller tenant*: non-superusers only
  ever see findings whose ``org_id`` matches their own organization,
  regardless of whether the agent is shared/global. Platform admins see
  all findings; an admin-created finding is scoped to the agent's
  organization explicitly.

Source runs are authorized through the canonical run-visibility
conditions (same-agent alone is insufficient), and any supplied
``source_run_id`` is validated no matter the source kind. Linking an
external source grants no fetch or access.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated
from uuid import UUID, uuid4

from fastapi import APIRouter, HTTPException, Query, Response, status
from sqlalchemy import select, text

from shared.agent_finding_queries import search_findings, serialize_finding
from shared.models import FindingCreate, FindingPublic, FindingSearchPage, FindingUpdate
from src.core.auth import CurrentActiveUser
from src.core.db_deps import DbSession, ReadSnapshotDbSession
from src.core.principal import UserPrincipal
from src.models.orm.agent_findings import AgentFinding
from src.models.orm.agent_runs import AgentRun
from shared.agent_finding_visibility import visible_agent_finding_condition
from src.routers.agent_evaluations import _authorized_agent, _org_id_for
from src.services.execution.agent_run_access import (
    agent_run_visibility_conditions,
)

router = APIRouter(prefix="/api/agent-findings", tags=["agent-findings"])


def _principal(user: CurrentActiveUser) -> UserPrincipal:
    return UserPrincipal(
        user_id=user.user_id,
        email=user.email,
        organization_id=user.organization_id,
        name=user.name or "",
        is_active=user.is_active,
        is_superuser=user.is_superuser,
        is_verified=user.is_verified,
        is_external=user.is_external,
    )


async def _to_public(
    db: DbSession, finding: AgentFinding, user: CurrentActiveUser
) -> FindingPublic:
    return await serialize_finding(db, finding, _principal(user))


async def _require_agent(db: DbSession, user: CurrentActiveUser, agent_id: UUID):
    agent = await _authorized_agent(
        db, user, agent_id, org_id=_org_id_for(user, None)
    )
    if agent is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Not found."
        )
    return agent


def _require_finding_tenant(finding: AgentFinding, user: CurrentActiveUser) -> None:
    """Enforce caller-tenant scope independently of agent visibility."""
    if not user.is_superuser and finding.org_id != user.organization_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Not found."
        )


async def _resolve_source_run(
    db: DbSession,
    user: CurrentActiveUser,
    agent_id: UUID,
    source_run_id: UUID | None,
    *,
    required: bool,
) -> None:
    """Validate an optional run reference through canonical run access."""
    if source_run_id is None:
        if required:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="source_run_id is required for run-sourced findings.",
            )
        return
    run = (
        await db.execute(
            select(AgentRun).where(
                AgentRun.id == source_run_id,
                *agent_run_visibility_conditions(user),
            )
        )
    ).scalar_one_or_none()
    if run is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Source run not found.",
        )
    if run.agent_id != agent_id:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="source_run_id must be a run of the finding's agent.",
        )


async def _existing_run_source_finding(
    db: DbSession, user: CurrentActiveUser, source_run_id: UUID | None
) -> AgentFinding | None:
    if source_run_id is None:
        return None
    return (
        await db.execute(
            select(AgentFinding).where(
                AgentFinding.source_kind == "run",
                AgentFinding.source_run_id == source_run_id,
                visible_agent_finding_condition(user),
            )
            .order_by(AgentFinding.created_at.asc(), AgentFinding.id.asc())
            .limit(1)
        )
    ).scalar_one_or_none()


async def _lock_run_source_finding(db: DbSession, source_run_id: UUID | None) -> None:
    if source_run_id is None:
        return
    await db.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
        {"key": str(source_run_id)},
    )


@router.post("", response_model=FindingPublic, status_code=status.HTTP_201_CREATED)
async def create_finding(
    body: FindingCreate,
    db: DbSession,
    user: CurrentActiveUser,
    response: Response,
) -> FindingPublic:
    """Record a reviewed finding against a visible agent."""
    agent = await _require_agent(db, user, body.agent_id)

    await _resolve_source_run(
        db,
        user,
        agent.id,
        body.source_run_id,
        required=body.source_kind == "run",
    )
    if body.source_sequence is not None and body.source_run_id is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="source_sequence requires a run source.",
        )
    if body.source_kind == "run":
        await _lock_run_source_finding(db, body.source_run_id)
        existing = await _existing_run_source_finding(db, user, body.source_run_id)
        if existing is not None:
            response.status_code = status.HTTP_200_OK
            return await _to_public(db, existing, user)

    now = datetime.now(timezone.utc)
    finding = AgentFinding(
        id=uuid4(),
        agent_id=agent.id,
        # Caller-tenant scope: a shared/global agent never leaks one
        # tenant's findings into another. Admin creates are scoped to the
        # agent's organization explicitly.
        org_id=(
            user.organization_id
            if not user.is_superuser
            else agent.organization_id
        ),
        status="open",
        description=body.description,
        expected_behavior=body.expected_behavior,
        source_kind=body.source_kind,
        source_run_id=body.source_run_id,
        source_sequence=body.source_sequence,
        external_ref=body.external_ref,
        finding_kind=body.finding_kind,
        evidence_markdown=body.evidence_markdown,
        created_by=user.user_id,
        created_at=now,
        updated_at=now,
    )
    db.add(finding)
    await db.commit()
    await db.refresh(finding)
    return await _to_public(db, finding, user)


@router.get("/search", response_model=FindingSearchPage)
async def search_finding_page(
    db: ReadSnapshotDbSession,
    user: CurrentActiveUser,
    agent_id: UUID | None = None,
    status_filter: Annotated[str | None, Query(alias="status")] = None,
    finding_kind: str | None = None,
    source_kind: str | None = None,
    review_id: UUID | None = None,
    review_run_id: UUID | None = None,
    q: Annotated[str | None, Query(max_length=200)] = None,
    organization_id: UUID | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> FindingSearchPage:
    """Search visible findings across authorized agents with paging."""
    if status_filter is not None and status_filter not in ("open", "dismissed"):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="status must be open or dismissed.",
        )
    if finding_kind is not None and finding_kind not in ("problem", "opportunity"):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="finding_kind must be problem or opportunity.",
        )
    if source_kind is not None and source_kind not in ("run", "manual", "external"):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="source_kind must be run, manual, or external.",
        )
    if organization_id is not None and not user.is_superuser:
        if organization_id != user.organization_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Cannot expand finding search beyond your organization.",
            )
    if agent_id is not None:
        await _require_agent(db, user, agent_id)
    return await search_findings(
        db,
        user=_principal(user),
        agent_id=agent_id,
        status=status_filter,
        finding_kind=finding_kind,
        source_kind=source_kind,
        review_id=review_id,
        review_run_id=review_run_id,
        query=q,
        organization_id=organization_id if user.is_superuser else None,
        limit=limit,
        offset=offset,
    )


@router.get("", response_model=list[FindingPublic])
async def list_findings(
    agent_id: UUID,
    db: DbSession,
    user: CurrentActiveUser,
    status_filter: str | None = Query(default=None, alias="status"),
) -> list[FindingPublic]:
    """List the caller's tenant findings for one visible agent, oldest first."""
    agent = await _require_agent(db, user, agent_id)
    query = (
        select(AgentFinding)
        .where(AgentFinding.agent_id == agent.id)
        .order_by(AgentFinding.created_at)
    )
    query = query.where(visible_agent_finding_condition(user))
    if status_filter is not None:
        if status_filter not in ("open", "dismissed"):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="status must be open or dismissed.",
            )
        query = query.where(AgentFinding.status == status_filter)
    rows = (await db.execute(query)).scalars().all()
    return [await _to_public(db, row, user) for row in rows]


@router.get("/{finding_id}", response_model=FindingPublic)
async def get_finding(
    finding_id: UUID, db: DbSession, user: CurrentActiveUser
) -> FindingPublic:
    """Read one finding; cross-tenant ids read as not found."""
    finding = (
        await db.execute(
            select(AgentFinding).where(
                AgentFinding.id == finding_id,
                visible_agent_finding_condition(user),
            )
        )
    ).scalar_one_or_none()
    if finding is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Not found."
        )
    await _require_agent(db, user, finding.agent_id)
    _require_finding_tenant(finding, user)
    return await _to_public(db, finding, user)


@router.patch("/{finding_id}", response_model=FindingPublic)
async def update_finding(
    finding_id: UUID,
    body: FindingUpdate,
    db: DbSession,
    user: CurrentActiveUser,
) -> FindingPublic:
    """Edit text or dismiss a finding. Dismissal needs no test."""
    finding = (
        await db.execute(
            select(AgentFinding).where(
                AgentFinding.id == finding_id,
                visible_agent_finding_condition(user),
            )
        )
    ).scalar_one_or_none()
    if finding is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Not found."
        )
    await _require_agent(db, user, finding.agent_id)
    _require_finding_tenant(finding, user)
    fields = body.model_fields_set
    if body.description is not None:
        finding.description = body.description
    if body.expected_behavior is not None:
        finding.expected_behavior = body.expected_behavior
    if body.status is not None:
        finding.status = body.status
    if body.finding_kind is not None:
        finding.finding_kind = body.finding_kind
    if "evidence_markdown" in fields:
        finding.evidence_markdown = body.evidence_markdown
    finding.updated_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(finding)
    return await _to_public(db, finding, user)
