"""E2E: solution-tier file-policy CRUD surface (Task A3, closes A4).

The admin CRUD endpoints accept a ``solution`` param that was previously
ignored. Decision (see report): solution-tier file policies are the deploy-owned
managed entity (guard.py semantics — like the Table row, not its instance-owned
Documents), so admin CRUD may READ them but writes/deletes are refused (409).

These tests exercise the service read variants + the router write guard directly
(no full async install flow needed to prove the param is honored end-to-end).
"""
from __future__ import annotations

import uuid

import pytest
import pytest_asyncio
from fastapi import HTTPException
from sqlalchemy import insert

from src.models.orm.file_metadata import FilePolicy
from src.models.orm.organizations import Organization
from src.models.orm.solutions import Solution
from src.services.file_policy_service import FilePolicyService

pytestmark = pytest.mark.e2e


@pytest_asyncio.fixture
async def install(db_session):
    org = Organization(
        id=uuid.uuid4(), name=f"fp-crud-{uuid.uuid4().hex[:6]}", created_by="t"
    )
    db_session.add(org)
    await db_session.flush()
    sol = Solution(
        id=uuid.uuid4(),
        slug=f"fp-crud-{uuid.uuid4().hex[:6]}",
        name="FP CRUD Test",
        organization_id=org.id,
    )
    db_session.add(sol)
    await db_session.flush()
    # Seed a solution-tier policy via Core insert (prod discipline).
    await db_session.execute(
        insert(FilePolicy).values(
            id=uuid.uuid4(),
            organization_id=org.id,
            solution_id=sol.id,
            location="shared",
            path="finance",
            policies={
                "policies": [
                    {"name": "r", "actions": ["read"], "when": {"user": "is_platform_admin"}}
                ]
            },
        )
    )
    await db_session.flush()
    return sol


@pytest.mark.asyncio
async def test_list_returns_solution_tier_rows(db_session, install):
    """list_policies(solution_id=…) returns the install's deploy-owned rows."""
    svc = FilePolicyService(db_session)
    rows = await svc.list_policies(organization_id=None, solution_id=install.id)
    assert [r.path for r in rows] == ["finance"]
    assert rows[0].solution_id == install.id
    # Without the solution scope, the workspace tier (solution_id IS NULL) is
    # returned instead — the solution row must NOT leak there.
    ws_rows = await svc.list_policies(organization_id=install.organization_id)
    assert all(r.solution_id is None for r in ws_rows)


@pytest.mark.asyncio
async def test_get_solution_policy_exact(db_session, install):
    """get_solution_policy_exact resolves by (solution_id, location, path)."""
    svc = FilePolicyService(db_session)
    row = await svc.get_solution_policy_exact(
        solution_id=install.id, location="shared", path="finance"
    )
    assert row is not None
    assert row.solution_id == install.id
    missing = await svc.get_solution_policy_exact(
        solution_id=install.id, location="shared", path="nope"
    )
    assert missing is None


@pytest.mark.asyncio
async def test_set_and_delete_refused_for_solution_tier(db_session, install):
    """PUT/DELETE with a solution param are refused (409) — deploy-owned."""
    from src.routers.files import delete_file_policy, set_file_policy

    with pytest.raises(HTTPException) as exc_set:
        await set_file_policy(
            policy_path="finance",
            request=type("R", (), {"policies": []})(),
            ctx=None,  # type: ignore[arg-type]
            user=None,  # type: ignore[arg-type]
            location="shared",
            scope=None,
            solution=str(install.id),
            db=db_session,
        )
    assert exc_set.value.status_code == 409

    with pytest.raises(HTTPException) as exc_del:
        await delete_file_policy(
            policy_path="finance",
            ctx=None,  # type: ignore[arg-type]
            user=None,  # type: ignore[arg-type]
            location="shared",
            scope=None,
            solution=str(install.id),
            db=db_session,
        )
    assert exc_del.value.status_code == 409
