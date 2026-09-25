"""Focused service tests for the shared topic-emission service."""

from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from shared.event_emission import (
    EventEmissionCaller,
    EventEmissionError,
    emit_topic_event,
)
from src.core.constants import SYSTEM_USER_UUID
from src.core.principal import UserPrincipal
from src.models.contracts.events import EmitEventRequest


def _superuser(org_id=None) -> UserPrincipal:
    return UserPrincipal(
        user_id=uuid4(),
        email="admin@example.com",
        organization_id=org_id,
        is_superuser=True,
        is_verified=True,
    )


def _regular_user(org_id) -> UserPrincipal:
    return UserPrincipal(
        user_id=uuid4(),
        email="user@example.com",
        organization_id=org_id,
        is_superuser=False,
        is_verified=True,
    )


def _service_principal(org_id) -> UserPrincipal:
    return UserPrincipal(
        user_id=SYSTEM_USER_UUID,
        email="service-abc@bifrost.internal",
        organization_id=org_id,
        is_superuser=False,
        is_verified=True,
        service_id=str(uuid4()),
        service_attempt_id=str(uuid4()),
    )


def _caller(principal, db=None, **ctx) -> EventEmissionCaller:
    return EventEmissionCaller(
        user=principal,
        db=db or AsyncMock(),
        solution_id=ctx.get("solution_id"),
        caller_solution_id=ctx.get("caller_solution_id"),
        app_id=ctx.get("app_id"),
    )


@pytest.mark.asyncio
async def test_regular_user_denied():
    org = uuid4()
    caller = _caller(_regular_user(org))
    with pytest.raises(EventEmissionError) as exc:
        await emit_topic_event(caller, EmitEventRequest(topic="a.b", data={}))
    assert exc.value.status_code == 403
    assert exc.value.detail == "Not authorized to emit events"


@pytest.mark.asyncio
async def test_invalid_topic_returns_400():
    caller = _caller(_superuser())
    with pytest.raises(EventEmissionError) as exc:
        await emit_topic_event(caller, EmitEventRequest(topic="INVALID", data={}))
    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_invalid_scope_returns_400():
    caller = _caller(_superuser())
    with pytest.raises(EventEmissionError) as exc:
        await emit_topic_event(
            caller, EmitEventRequest(topic="a.b", data={}, scope="nope")
        )
    assert exc.value.status_code == 400
    assert "Invalid scope" in exc.value.detail


@pytest.mark.asyncio
async def test_service_cross_org_and_global_denied():
    org = uuid4()
    caller = _caller(_service_principal(org))
    with pytest.raises(EventEmissionError) as exc:
        await emit_topic_event(
            caller,
            EmitEventRequest(topic="a.b", data={}, scope=str(uuid4())),
        )
    assert exc.value.status_code == 403
    with pytest.raises(EventEmissionError) as exc:
        await emit_topic_event(
            caller, EmitEventRequest(topic="a.b", data={}, scope="GLOBAL")
        )
    assert exc.value.status_code == 403
    with pytest.raises(EventEmissionError) as exc:
        await emit_topic_event(caller, EmitEventRequest(topic="a.b", data={}))
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_service_own_org_emission_success():
    from uuid import uuid4 as _uuid4

    org = uuid4()
    principal = _service_principal(org)
    caller = _caller(principal)
    event_id = _uuid4()
    with patch(
        "src.services.events.emit_event",
        new=AsyncMock(return_value=(event_id, 2)),
    ) as durable:
        resp = await emit_topic_event(
            caller, EmitEventRequest(topic="a.b", data={"k": "v"}, scope=str(org))
        )
    assert resp.event_id == str(event_id)
    assert resp.subscribers_notified == 2
    durable.assert_awaited_once()
    _, kwargs = durable.call_args
    assert kwargs["organization_id"] == org
    assert kwargs["solution_id"] is None
    assert kwargs["triggered_by"] == str(principal.user_id)


@pytest.mark.asyncio
async def test_superuser_global_success_preserves_actor():
    principal = _superuser()
    caller = _caller(principal)
    event_id = uuid4()
    with patch(
        "src.services.events.emit_event",
        new=AsyncMock(return_value=(event_id, 0)),
    ) as durable:
        resp = await emit_topic_event(
            caller, EmitEventRequest(topic="a.b", data={})
        )
    assert resp.event_id == str(event_id)
    assert resp.subscribers_notified == 0
    _, kwargs = durable.call_args
    assert kwargs["organization_id"] is None
    assert kwargs["triggered_by"] == str(principal.user_id)


@pytest.mark.e2e
class TestSolutionGating:
    async def _org(self, db):
        from src.models.orm.organizations import Organization

        org = Organization(id=uuid4(), name=f"O-{uuid4().hex[:6]}", created_by="t")
        db.add(org)
        await db.flush()
        return org

    async def _sol(self, db, org_id, slug, sealed=False):
        from src.models.orm.solutions import Solution

        sol = Solution(
            id=uuid4(),
            slug=slug,
            name=f"N-{slug}",
            organization_id=org_id,
        )
        if sealed:
            sol.allow_inbound_access = False
        db.add(sol)
        await db.flush()
        return sol

    async def test_unknown_solution_404(self, db_session):
        caller = EventEmissionCaller(
            user=_superuser(), db=db_session, solution_id=None
        )
        with pytest.raises(EventEmissionError) as exc:
            await emit_topic_event(
                caller,
                EmitEventRequest(topic="a.b", data={}, solution=str(uuid4())),
            )
        assert exc.value.status_code == 404

    async def test_sealed_solution_denies_outside_caller(self, db_session):
        org = await self._org(db_session)
        sol = await self._sol(db_session, org.id, "sealed", sealed=True)
        caller = EventEmissionCaller(user=_superuser(), db=db_session)
        with pytest.raises(EventEmissionError) as exc:
            await emit_topic_event(
                caller,
                EmitEventRequest(
                    topic="a.b", data={}, scope=str(org.id), solution=str(sol.id)
                ),
            )
        assert exc.value.status_code == 404

    async def test_open_solution_emits_with_target(self, db_session):
        org = await self._org(db_session)
        sol = await self._sol(db_session, org.id, "open")
        principal = _superuser()
        caller = EventEmissionCaller(user=principal, db=db_session)
        event_id = uuid4()
        with patch(
            "src.services.events.emit_event",
            new=AsyncMock(return_value=(event_id, 1)),
        ) as durable:
            resp = await emit_topic_event(
                caller,
                EmitEventRequest(
                    topic="a.b", data={}, scope=str(org.id), solution=str(sol.id)
                ),
            )
        assert resp.subscribers_notified == 1
        _, kwargs = durable.call_args
        assert kwargs["solution_id"] == sol.id
        assert kwargs["organization_id"] == org.id

    async def test_sealed_solution_own_call_via_signed_claim_passes(
        self, db_session
    ):
        org = await self._org(db_session)
        sol = await self._sol(db_session, org.id, "sealed-own", sealed=True)
        principal = UserPrincipal(
            user_id=SYSTEM_USER_UUID,
            email="engine@example.com",
            organization_id=org.id,
            is_superuser=True,
            is_verified=True,
            engine_execution_id="exec-1",
            engine_solution_id=str(sol.id),
        )
        caller = EventEmissionCaller(user=principal, db=db_session)
        event_id = uuid4()
        with patch(
            "src.services.events.emit_event",
            new=AsyncMock(return_value=(event_id, 1)),
        ):
            resp = await emit_topic_event(
                caller,
                EmitEventRequest(
                    topic="a.b", data={}, scope=str(org.id), solution=str(sol.id)
                ),
            )
        assert resp.event_id == str(event_id)

    async def test_constrained_caller_solution_engine_only(self, db_session):
        org = await self._org(db_session)
        sol = await self._sol(db_session, org.id, "sealed-cs", sealed=True)

        # Engine principal without signed claims may attest via body
        # caller_solution (constrained case).
        engine = UserPrincipal(
            user_id=SYSTEM_USER_UUID,
            email="engine@example.com",
            organization_id=org.id,
            is_superuser=True,
            is_verified=True,
            engine_execution_id="exec-9",
            engine_solution_id=None,
        )
        caller = EventEmissionCaller(user=engine, db=db_session)
        event_id = uuid4()
        with patch(
            "src.services.events.emit_event",
            new=AsyncMock(return_value=(event_id, 1)),
        ):
            resp = await emit_topic_event(
                caller,
                EmitEventRequest(
                    topic="a.b",
                    data={},
                    scope=str(org.id),
                    solution=str(sol.id),
                    caller_solution=str(sol.id),
                ),
            )
        assert resp.event_id == str(event_id)

        # Non-engine callers must not benefit from body caller_solution.
        outsider = EventEmissionCaller(user=_superuser(), db=db_session)
        with pytest.raises(EventEmissionError) as exc:
            await emit_topic_event(
                outsider,
                EmitEventRequest(
                    topic="a.b",
                    data={},
                    scope=str(org.id),
                    solution=str(sol.id),
                    caller_solution=str(sol.id),
                ),
            )
        assert exc.value.status_code == 404
