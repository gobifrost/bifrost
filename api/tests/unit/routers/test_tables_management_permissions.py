"""Named-permission gates for table metadata management routes."""

from __future__ import annotations

from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import HTTPException

from src.core.auth import ExecutionContext
from src.core.principal import UserPrincipal
from src.models.orm.organizations import Organization
from src.models.orm.tables import Table
from src.routers import tables
from src.services.authorization.context import (
    AuthorizationContext,
    Boundary,
    BoundaryKind,
    RoleGrant,
)
from src.services.authorization.enforce import Caller


def _caller(org_id, permissions: set[str]) -> Caller:
    principal = UserPrincipal(
        user_id=uuid4(),
        email="table-manager@example.com",
        organization_id=org_id,
    )
    context = AuthorizationContext(
        user_id=principal.user_id,
        home_organization_id=org_id,
        base_role_id=uuid4(),
        is_external=False,
        base_permissions=frozenset(),
        role_grants=(
            RoleGrant(
                uuid4(),
                frozenset(permissions),
                (Boundary(BoundaryKind.ORGANIZATION, org_id),),
            ),
        ),
    )
    return Caller(principal, context)


@pytest.mark.asyncio
async def test_list_tables_filters_to_role_grant_permission_reach(
    db_session, monkeypatch
) -> None:
    org_a = Organization(name=f"Tables A {uuid4().hex[:8]}", created_by="test")
    org_b = Organization(name=f"Tables B {uuid4().hex[:8]}", created_by="test")
    db_session.add_all([org_a, org_b])
    await db_session.flush()
    visible = Table(name=f"visible_{uuid4().hex[:8]}", organization_id=org_a.id)
    hidden = Table(name=f"hidden_{uuid4().hex[:8]}", organization_id=org_b.id)
    db_session.add_all([visible, hidden])
    await db_session.flush()

    caller = _caller(org_a.id, {"tables.read"})
    monkeypatch.setattr(tables, "load_caller", AsyncMock(return_value=caller))
    ctx = ExecutionContext(user=caller.principal, org_id=org_a.id, db=db_session)

    result = await tables.list_tables(ctx, caller.principal, scope=None)

    assert [row.id for row in result.tables] == [visible.id]
    assert result.total == 1


@pytest.mark.asyncio
async def test_list_tables_rejects_explicit_scope_outside_original_user_reach(
    db_session, monkeypatch
) -> None:
    org_a = Organization(name=f"Tables A {uuid4().hex[:8]}", created_by="test")
    org_b = Organization(name=f"Tables B {uuid4().hex[:8]}", created_by="test")
    db_session.add_all([org_a, org_b])
    await db_session.flush()

    caller = _caller(org_a.id, {"tables.read"})
    async_loader = AsyncMock(return_value=caller)
    monkeypatch.setattr(tables, "load_caller", async_loader)
    monkeypatch.setattr(
        "src.services.authorization.enforce.load_caller", async_loader
    )
    ctx = ExecutionContext(user=caller.principal, org_id=org_a.id, db=db_session)

    with pytest.raises(HTTPException) as exc:
        await tables.list_tables(ctx, caller.principal, scope=str(org_b.id))

    assert exc.value.status_code == 403
