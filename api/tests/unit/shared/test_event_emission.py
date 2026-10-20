"""Topic emission uses current permissions, scope, and verified provenance."""

from dataclasses import replace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from shared.builtin_roles import USER_ROLE_ID
from shared.event_emission import EventEmissionCaller, EventEmissionError, emit_topic_event
from src.core.principal import UserPrincipal
from src.models.contracts.events import EmitEventRequest
from src.models.orm.organizations import Organization
from src.models.orm.solutions import Solution
from src.models.orm.users import Role, RolePermission, User, UserRole, UserRoleBoundary
from src.services.authorization.enforce import load_caller
from src.services.authorization.explain import RunUser
from src.services.authorization.runtime import RuntimeAuthority


async def _caller(db, *, permission=True, actor_flag=False, runtime_kind=None, solution_id=None):
    org = Organization(name="Contoso", created_by="test")
    db.add(org)
    await db.flush()
    person = User(email=f"event-{uuid4().hex}@contoso.example", organization_id=org.id, base_role_id=USER_ROLE_ID)
    role = Role(name=f"Contoso Events {uuid4().hex}", created_by="test")
    db.add_all([person, role])
    await db.flush()
    db.add_all([
        UserRole(user_id=person.id, role_id=role.id, assigned_by="test"),
        UserRoleBoundary(user_id=person.id, role_id=role.id, kind="organization", organization_id=org.id),
        UserRoleBoundary(user_id=person.id, role_id=role.id, kind="platform"),
    ])
    if permission:
        db.add(RolePermission(role_id=role.id, permission="events.readwrite"))
    await db.flush()
    principal = UserPrincipal(user_id=person.id, email=person.email, organization_id=org.id, is_superuser=actor_flag)
    if runtime_kind:
        authority = await load_caller(db, principal)
        run_id = uuid4()
        principal = replace(principal, runtime_authority=RuntimeAuthority(
            kind=runtime_kind, credential_id=run_id, run_user=RunUser(person.id, authority.ctx, None, True),
            powers=None, acting_user_id=person.id, organization_id=org.id,
            workflow_id=None, started_by_user_id=person.id, root_execution_id=run_id,
            service_id=uuid4() if runtime_kind == "service_attempt" else None,
            solution_id=solution_id,
        ))
    return EventEmissionCaller(principal, db), org


async def test_named_writer_emits_at_omitted_original_scope_and_preserves_actor(db_session, monkeypatch):
    caller, org = await _caller(db_session)
    event_id = uuid4()
    durable = AsyncMock(return_value=(event_id, 2))
    monkeypatch.setattr("src.services.events.emit_event", durable)
    result = await emit_topic_event(caller, EmitEventRequest(topic="contoso.changed", data={"value": 1}))
    assert result.event_id == str(event_id)
    assert result.subscribers_notified == 2
    durable.assert_awaited_once_with("contoso.changed", {"value": 1}, organization_id=org.id, solution_id=None, triggered_by=str(caller.user.user_id))


async def test_actor_admin_flag_cannot_emit_without_named_permission(db_session, monkeypatch):
    caller, _org = await _caller(db_session, permission=False, actor_flag=True)
    durable = AsyncMock(side_effect=AssertionError("emit before permission admission"))
    monkeypatch.setattr("src.services.events.emit_event", durable)
    with pytest.raises(EventEmissionError) as error:
        await emit_topic_event(caller, EmitEventRequest(topic="contoso.changed", data={}))
    assert error.value.status_code == 403
    durable.assert_not_awaited()


async def test_foreign_scope_is_refused_before_solution_or_delivery_io(db_session, monkeypatch):
    caller, _org = await _caller(db_session, actor_flag=True)
    durable = AsyncMock(side_effect=AssertionError("emit outside original reach"))
    monkeypatch.setattr("src.services.events.emit_event", durable)
    with pytest.raises(EventEmissionError) as error:
        await emit_topic_event(caller, EmitEventRequest(topic="contoso.changed", data={}, scope=str(uuid4())))
    assert error.value.status_code == 403
    durable.assert_not_awaited()


@pytest.mark.parametrize("payload", [
    EmitEventRequest(topic="INVALID", data={}),
    EmitEventRequest(topic="contoso.changed", data={}, scope="invalid-scope"),
])
async def test_invalid_input_is_refused_before_delivery(db_session, monkeypatch, payload):
    caller, _org = await _caller(db_session)
    durable = AsyncMock(side_effect=AssertionError("emit before input validation"))
    monkeypatch.setattr("src.services.events.emit_event", durable)
    with pytest.raises(EventEmissionError) as error:
        await emit_topic_event(caller, payload)
    assert error.value.status_code == 400
    durable.assert_not_awaited()


async def test_typed_service_keeps_own_org_confinement(db_session, monkeypatch):
    caller, org = await _caller(db_session, runtime_kind="service_attempt")
    durable = AsyncMock(return_value=(uuid4(), 0))
    monkeypatch.setattr("src.services.events.emit_event", durable)
    with pytest.raises(EventEmissionError) as error:
        await emit_topic_event(caller, EmitEventRequest(topic="contoso.changed", data={}, scope="GLOBAL"))
    assert error.value.status_code == 403
    durable.assert_not_awaited()
    await emit_topic_event(caller, EmitEventRequest(topic="contoso.changed", data={}))
    assert durable.await_args.kwargs["organization_id"] == org.id


async def test_sealed_solution_uses_durable_runtime_provenance_instead_of_claimed_request(db_session, monkeypatch):
    caller, org = await _caller(db_session, runtime_kind="workflow_execution")
    solution = Solution(slug=f"contoso-{uuid4().hex}", name="Contoso", organization_id=org.id, allow_inbound_access=False)
    db_session.add(solution)
    await db_session.flush()
    request = EmitEventRequest(topic="contoso.changed", data={}, solution=str(solution.id), caller_solution=str(solution.id))
    durable = AsyncMock(return_value=(uuid4(), 1))
    monkeypatch.setattr("src.services.events.emit_event", durable)
    with pytest.raises(EventEmissionError) as error:
        await emit_topic_event(caller, request)
    assert error.value.status_code == 404
    durable.assert_not_awaited()

    caller.user = replace(caller.user, runtime_authority=replace(caller.user.runtime_authority, solution_id=solution.id))
    result = await emit_topic_event(caller, request)
    assert result.subscribers_notified == 1
    assert durable.await_args.kwargs["solution_id"] == solution.id


async def test_unknown_solution_is_404(db_session):
    caller, _org = await _caller(db_session)
    with pytest.raises(EventEmissionError) as error:
        await emit_topic_event(caller, EmitEventRequest(topic="contoso.changed", data={}, solution=str(uuid4())))
    assert error.value.status_code == 404
