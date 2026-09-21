"""Agent-wide default test collection (Phase 4 foundation).

One server-managed published suite per (org, agent) holds agent-wide tests
created without a named suite. Ordinary suite flows never set ``is_default``
and stay unchanged. Race-safe: concurrent creators collapse onto one row via
the partial-unique fence.
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.orm.agent_evaluations import AgentEvaluationSuite

DEFAULT_COLLECTION_NAME = "Default Tests"


async def _find_default_suite(
    db: AsyncSession, *, agent_id: UUID, org_id: UUID
) -> AgentEvaluationSuite | None:
    return (
        await db.execute(
            select(AgentEvaluationSuite).where(
                AgentEvaluationSuite.is_default.is_(True),
                AgentEvaluationSuite.org_id == org_id,
                AgentEvaluationSuite.agent_id == agent_id,
            )
        )
    ).scalar_one_or_none()


async def get_or_create_default_suite(
    db: AsyncSession,
    *,
    agent_id: UUID,
    org_id: UUID,
    created_by: str | None = None,
) -> AgentEvaluationSuite:
    """Return the published default suite for (org, agent), creating it once."""
    existing = await _find_default_suite(db, agent_id=agent_id, org_id=org_id)
    if existing is not None:
        return existing
    now = datetime.now(timezone.utc)
    try:
        async with db.begin_nested():
            suite = AgentEvaluationSuite(
                id=uuid4(),
                org_id=org_id,
                agent_id=agent_id,
                name=DEFAULT_COLLECTION_NAME,
                description="Server-managed agent-wide test collection.",
                status="published",
                version=1,
                is_default=True,
                created_by=created_by,
                created_at=now,
                updated_at=now,
            )
            db.add(suite)
            await db.flush()
    except IntegrityError:
        existing = await _find_default_suite(db, agent_id=agent_id, org_id=org_id)
        if existing is None:
            raise
        return existing
    return suite


async def is_default_suite(
    db: AsyncSession, *, suite_id: UUID
) -> bool:
    suite = await db.get(AgentEvaluationSuite, suite_id)
    return suite is not None and bool(suite.is_default)
