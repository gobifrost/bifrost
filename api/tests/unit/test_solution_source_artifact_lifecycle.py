from __future__ import annotations

import uuid
from contextlib import asynccontextmanager

import pytest

from shared.builtin_roles import PLATFORM_ADMIN_ROLE_ID, DECRYPTION_ROLE_ID
from src.models.orm.users import UserRole, UserRoleBoundary
from src.core.auth import ExecutionContext
from src.core.principal import UserPrincipal
from src.models.orm.solutions import Solution
from src.routers.solutions import delete_solution
from src.services.solutions.source_artifact import SolutionSourceArtifactStorage

pytestmark = pytest.mark.e2e


async def _admin(db, seeded_user, *, secrets=False) -> tuple[ExecutionContext, UserPrincipal]:
    for role_id in [PLATFORM_ADMIN_ROLE_ID, *([DECRYPTION_ROLE_ID] if secrets else [])]:
        db.add_all([
            UserRole(user_id=seeded_user.id, role_id=role_id, assigned_by="test"),
            UserRoleBoundary(user_id=seeded_user.id, role_id=role_id, kind="platform"),
        ])
    await db.flush()
    user = UserPrincipal(
        user_id=seeded_user.id, email=seeded_user.email,
        organization_id=seeded_user.organization_id,
    )
    return ExecutionContext(user=user, org_id=None, db=db), user


async def test_delete_solution_removes_source_artifact(db_session, monkeypatch, seed_user) -> None:
    from src.core.module_cache import get_module, set_module
    from src.services.solutions.storage import SolutionStorage

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
    storage = SolutionStorage(sol.id)
    source_path = "workflows/removed.py"
    source = "VALUE = 1\n"
    content_hash = await storage.write(source_path, source.encode())
    await set_module(storage._key(source_path), source, content_hash)

    @asynccontextmanager
    async def _unlocked(_solution_id):
        yield

    monkeypatch.setattr("src.services.solutions.write_lock.solution_write_lock", _unlocked)

    ctx, user = await _admin(db_session, seed_user)
    await delete_solution(sol.id, ctx, user, confirm=sol.slug)

    assert await artifact.read() is None
    assert await get_module(storage._key(source_path)) is None
