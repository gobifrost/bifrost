"""Direct service coverage for resolving an existing active Solution install."""
from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy import select

from src.models.orm.solutions import Solution
from src.services.solutions.zip_install import _resolve_or_create_solution


@pytest.mark.e2e
async def test_resolve_active_install_reuses_existing_row(db_session) -> None:
    """An active deploy resolves to its existing install without creating a duplicate."""
    existing = Solution(
        id=uuid4(),
        slug=f"active-reinstall-{uuid4().hex[:8]}",
        name="Original install",
        organization_id=None,
        status="active",
    )
    db_session.add(existing)
    await db_session.flush()

    resolved = await _resolve_or_create_solution(
        db_session,
        slug=existing.slug,
        name="Replacement name is resolved during deploy",
        organization_id=None,
    )

    assert resolved.id == existing.id
    assert resolved.status == "active"
    matching_ids = (
        await db_session.execute(
            select(Solution.id).where(
                Solution.slug == existing.slug,
                Solution.organization_id.is_(None),
            )
        )
    ).scalars().all()
    assert matching_ids == [existing.id]
