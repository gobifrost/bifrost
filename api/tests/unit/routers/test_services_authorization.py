"""Current-caller authorization for supervised service rows."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from src.core.auth import ExecutionContext
from src.core.principal import UserPrincipal
from src.models.orm.organizations import Organization
from src.models.orm.services import ServiceDefinition
from src.models.orm.users import Role, RolePermission, User, UserRole, UserRoleBoundary
from src.models.orm.workflows import Workflow
from src.routers import services

pytestmark = pytest.mark.asyncio


async def _organization(db, name: str) -> Organization:
    organization = Organization(name=f"{name}-{uuid4().hex[:8]}", created_by="service-auth-test")
    db.add(organization)
    await db.flush()
    return organization


async def _user_with_platform_read(db, organization: Organization) -> UserPrincipal:
    user = User(
        email=f"service-{uuid4().hex}@example.test",
        name="Service reader",
        organization_id=organization.id,
    )
    role = Role(name=f"Service reader-{uuid4().hex[:8]}", created_by="service-auth-test")
    db.add_all((user, role))
    await db.flush()
    db.add_all((
        RolePermission(role_id=role.id, permission="platform.read"),
        UserRole(user_id=user.id, role_id=role.id, assigned_by="service-auth-test"),
        UserRoleBoundary(
            user_id=user.id,
            role_id=role.id,
            kind="organization",
            organization_id=organization.id,
        ),
    ))
    await db.flush()
    return UserPrincipal(user_id=user.id, email=user.email, organization_id=organization.id)


async def _service(db, organization: Organization, *, created_at: datetime) -> ServiceDefinition:
    name = f"service_{uuid4().hex[:8]}"
    workflow = Workflow(
        name=name,
        function_name=name,
        path=f"workflows/{name}.py",
        type="service",
        organization_id=organization.id,
    )
    db.add(workflow)
    await db.flush()
    definition = ServiceDefinition(
        workflow_id=workflow.id,
        organization_id=organization.id,
        created_by="service-auth-test",
        created_at=created_at,
    )
    db.add(definition)
    await db.flush()
    return definition


async def test_services_named_role_filters_current_org_before_count_and_limit(db_session, monkeypatch) -> None:
    visible_org = await _organization(db_session, "visible")
    hidden_org = await _organization(db_session, "hidden")
    user = await _user_with_platform_read(db_session, visible_org)
    now = datetime.now(timezone.utc)
    visible = await _service(db_session, visible_org, created_at=now - timedelta(minutes=1))
    await _service(db_session, hidden_org, created_at=now)
    monkeypatch.setattr(services.service_memory, "read_service_memory", AsyncMock(return_value={}))
    ctx = ExecutionContext(user=user, org_id=user.organization_id, db=db_session)

    response = await services.list_services(ctx, user, db_session, limit=1, offset=0)

    assert response.total == 1
    assert [row.id for row in response.items] == [visible.id]


async def test_services_named_role_allows_actual_org_and_refuses_other_org(db_session) -> None:
    allowed_org = await _organization(db_session, "allowed")
    denied_org = await _organization(db_session, "denied")
    user = await _user_with_platform_read(db_session, allowed_org)
    now = datetime.now(timezone.utc)
    allowed = await _service(db_session, allowed_org, created_at=now)
    denied = await _service(db_session, denied_org, created_at=now)
    ctx = ExecutionContext(user=user, org_id=user.organization_id, db=db_session)

    assert (await services.get_service(allowed.id, ctx, user, db_session)).id == allowed.id
    with pytest.raises(HTTPException) as exc_info:
        await services.get_service(denied.id, ctx, user, db_session)

    assert exc_info.value.status_code == 403
