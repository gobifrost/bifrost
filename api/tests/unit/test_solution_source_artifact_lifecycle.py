from __future__ import annotations

import uuid
from contextlib import asynccontextmanager

import pytest

from src.core.auth import ExecutionContext
from src.core.principal import UserPrincipal
from src.models.orm.solutions import Solution
from src.routers.solutions import delete_solution
from src.services.solutions.source_artifact import SolutionSourceArtifactStorage

pytestmark = pytest.mark.e2e


def _admin(db) -> tuple[ExecutionContext, UserPrincipal]:
    user = UserPrincipal(
        user_id=uuid.uuid4(),
        email="admin@example.com",
        organization_id=None,
        is_superuser=True,
    )
    return ExecutionContext(user=user, org_id=None, db=db), user


async def test_delete_solution_removes_source_artifact(db_session, monkeypatch) -> None:
    sol = Solution(
        id=uuid.uuid4(),
        slug=f"src-artifact-{uuid.uuid4().hex[:8]}",
        name="Source Artifact",
        organization_id=None,
    )
    db_session.add(sol)
    await db_session.flush()
    artifact = SolutionSourceArtifactStorage(sol.id)
    await artifact.write(b"PK\x05\x06" + b"\x00" * 18)
    assert await artifact.read() is not None

    @asynccontextmanager
    async def _unlocked(_solution_id):
        yield

    monkeypatch.setattr("src.services.solutions.write_lock.solution_write_lock", _unlocked)

    ctx, user = _admin(db_session)
    await delete_solution(sol.id, ctx, user)

    assert await artifact.read() is None
