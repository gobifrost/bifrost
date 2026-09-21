"""Shared authorized finding search and serialization.

Both the finding router and the review-results read path use this module so
Markdown evidence, review provenance, and linked test cases cannot drift
between surfaces. All list/count/page queries apply the accepted shared SQL
visibility predicate before counting or paging.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from shared.agent_finding_visibility import visible_agent_finding_condition
from shared.models import FindingPublic, FindingSearchPage
from src.core.principal import UserPrincipal
from src.models.orm.agent_evaluations import (
    AgentEvaluationCase,
    AgentEvaluationSuite,
)
from src.models.orm.agent_findings import AgentFinding

_MAX_QUERY_LENGTH = 200


def _escape_like(value: str) -> str:
    return (
        value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    )


async def serialize_finding(
    db: AsyncSession, finding: AgentFinding, user: UserPrincipal
) -> FindingPublic:
    """Serialize one finding with authorized linked cases and provenance."""
    case_query = (
        select(AgentEvaluationCase.id)
        .join(
            AgentEvaluationSuite,
            AgentEvaluationSuite.id == AgentEvaluationCase.suite_id,
        )
        .where(AgentEvaluationCase.finding_id == finding.id)
    )
    if not user.is_superuser:
        case_query = case_query.where(
            AgentEvaluationSuite.org_id == user.organization_id
        )
    linked = list((await db.execute(case_query)).scalars().all())
    refs = finding.source_run_refs
    if not isinstance(refs, list):
        refs = []
    return FindingPublic(
        id=finding.id,
        agent_id=finding.agent_id,
        org_id=finding.org_id,
        status=finding.status,
        description=finding.description,
        expected_behavior=finding.expected_behavior,
        source_kind=finding.source_kind,
        source_run_id=finding.source_run_id,
        source_sequence=finding.source_sequence,
        external_ref=finding.external_ref,
        finding_kind=finding.finding_kind or "problem",
        evidence_markdown=finding.evidence_markdown,
        source_review_id=finding.source_review_id,
        source_review_version_id=finding.source_review_version_id,
        source_review_run_id=finding.source_review_run_id,
        source_review_version=finding.source_review_version,
        source_run_refs=list(refs),
        source_ordinal=finding.source_ordinal,
        linked_case_ids=list(linked),
        created_by=finding.created_by,
        created_at=finding.created_at,
        updated_at=finding.updated_at,
    )


async def search_findings(
    db: AsyncSession,
    *,
    user: UserPrincipal,
    agent_id: UUID | None = None,
    status: str | None = None,
    finding_kind: str | None = None,
    source_kind: str | None = None,
    review_id: UUID | None = None,
    review_run_id: UUID | None = None,
    query: str | None = None,
    organization_id: UUID | None = None,
    limit: int = 50,
    offset: int = 0,
) -> FindingSearchPage:
    """Count and page visible findings with one shared SQL predicate."""
    conditions = [visible_agent_finding_condition(user)]
    if agent_id is not None:
        conditions.append(AgentFinding.agent_id == agent_id)
    if status is not None:
        conditions.append(AgentFinding.status == status)
    if finding_kind is not None:
        conditions.append(AgentFinding.finding_kind == finding_kind)
    if source_kind is not None:
        conditions.append(AgentFinding.source_kind == source_kind)
    if review_id is not None:
        conditions.append(AgentFinding.source_review_id == review_id)
    if review_run_id is not None:
        conditions.append(AgentFinding.source_review_run_id == review_run_id)
    if organization_id is not None:
        conditions.append(AgentFinding.org_id == organization_id)
    if query:
        q = query[:_MAX_QUERY_LENGTH]
        pattern = f"%{_escape_like(q)}%"
        conditions.append(
            or_(
                AgentFinding.description.ilike(pattern, escape="\\"),
                AgentFinding.expected_behavior.ilike(pattern, escape="\\"),
                AgentFinding.evidence_markdown.ilike(pattern, escape="\\"),
            )
        )
    total = int(
        (
            await db.execute(
                select(func.count())
                .select_from(AgentFinding)
                .where(*conditions)
            )
        ).scalar_one()
    )
    rows = (
        (
            await db.execute(
                select(AgentFinding)
                .where(*conditions)
                .order_by(AgentFinding.created_at.desc(), AgentFinding.id)
                .limit(limit)
                .offset(offset)
            )
        )
        .scalars()
        .all()
    )
    items = [await serialize_finding(db, row, user) for row in rows]
    return FindingSearchPage(
        items=items, total=total, limit=limit, offset=offset
    )
