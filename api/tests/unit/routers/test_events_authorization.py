"""Real-DB original-Caller authorization coverage for Event management."""

from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import HTTPException

from shared.builtin_roles import USER_ROLE_ID
from src.core.principal import UserPrincipal
from src.models.contracts.events import EventSourceUpdate, EventSubscriptionCreate
from src.models.enums import EventSourceType
from src.models.orm.events import EventSource
from src.models.orm.organizations import Organization
from src.models.orm.users import Role, RolePermission, User, UserRole, UserRoleBoundary
from src.models.orm.workflows import Workflow
from src.routers.events import create_subscription, list_sources, update_source
from src.services.authorization.enforce import load_caller
from src.services.webhooks.auth import build_webhook_integration_credentials


async def _scoped_person(db, *permissions: str):
    home, foreign = (
        Organization(name=name, created_by="test") for name in ("Contoso", "Fabrikam")
    )
    db.add_all([home, foreign])
    await db.flush()
    person = User(
        email=f"events-{uuid4().hex}@contoso.example",
        organization_id=home.id,
        base_role_id=USER_ROLE_ID,
    )
    role = Role(name=f"Contoso Events {uuid4().hex}", created_by="test")
    db.add_all([person, role])
    await db.flush()
    db.add_all([
        UserRole(user_id=person.id, role_id=role.id, assigned_by="test"),
        UserRoleBoundary(
            user_id=person.id,
            role_id=role.id,
            kind="organization",
            organization_id=home.id,
        ),
        *(RolePermission(role_id=role.id, permission=permission) for permission in permissions),
    ])
    await db.flush()
    principal = UserPrincipal(
        user_id=person.id,
        email=person.email,
        organization_id=home.id,
    )
    return principal, home, foreign


async def _source(db, organization_id, name: str) -> EventSource:
    row = EventSource(
        name=name,
        source_type=EventSourceType.TOPIC,
        event_type="ticket.created",
        organization_id=organization_id,
        created_by="test",
    )
    db.add(row)
    await db.flush()
    return row


async def test_event_source_list_filters_named_role_reach_before_paging_and_count(db_session):
    principal, home, foreign = await _scoped_person(db_session, "events.read")
    await _source(db_session, foreign.id, "A foreign source")
    visible = await _source(db_session, home.id, "Z Contoso source")

    result = await list_sources(
        SimpleNamespace(),
        principal,
        db_session,
        source_type=None,
        organization_id=None,
        scope=None,
        limit=1,
        offset=0,
    )

    assert [source.id for source in result.items] == [visible.id]
    assert result.total == 1


async def test_event_source_move_requires_destination_reach_before_mutation(db_session):
    principal, home, foreign = await _scoped_person(db_session, "events.readwrite")
    source = await _source(db_session, home.id, "Contoso source")

    with pytest.raises(HTTPException) as error:
        await update_source(
            source.id,
            EventSourceUpdate(organization_id=foreign.id),
            SimpleNamespace(),
            principal,
            db_session,
        )

    assert error.value.status_code == 404
    assert source.organization_id == home.id


async def test_subscription_target_uses_live_workflow_resource_admission(db_session):
    principal, home, _foreign = await _scoped_person(
        db_session, "events.readwrite", "workflows.readwrite"
    )
    source = await _source(db_session, home.id, "Contoso source")
    workflow = Workflow(
        name=f"event_target_{uuid4().hex}",
        function_name="event_target",
        path="workflows/event_target.py",
        type="workflow",
        organization_id=home.id,
        access_level="role_based",
        is_active=True,
    )
    db_session.add(workflow)
    await db_session.flush()

    result = await create_subscription(
        source.id,
        EventSubscriptionCreate(workflow_id=workflow.id),
        SimpleNamespace(user=SimpleNamespace(email="actor@contoso.example")),
        principal,
        db_session,
    )

    assert result.workflow_id == workflow.id


@pytest.mark.parametrize("permissions", [(), ("integrations.read",)])
async def test_webhook_credentials_refuse_before_database_or_provider_io_without_both_permissions(
    db_session, monkeypatch, permissions
):
    principal, home, _foreign = await _scoped_person(db_session, *permissions)
    caller = await load_caller(db_session, principal)
    execute = AsyncMock(side_effect=AssertionError("credential lookup ran before authorization"))
    monkeypatch.setattr(db_session, "execute", execute)
    decrypt = AsyncMock(side_effect=AssertionError("credential decryption ran before authorization"))
    monkeypatch.setattr("src.services.webhooks.auth.decrypt_secret", decrypt)

    with pytest.raises(HTTPException) as error:
        await build_webhook_integration_credentials(
            db_session, caller, uuid4(), home.id
        )

    assert error.value.status_code == 403
    execute.assert_not_awaited()
    decrypt.assert_not_called()
