"""Engine-local transport for the fixed ``events.emit`` call.

Covers the acceptance surface that does not need a forked child:

- ``events.emit`` rides the async SDK channel (membership asserted on
  the allowlist; the import channel rejects it);
- the parent dispatcher calls the same ``shared.event_emission``
  service the HTTP handler calls, with DTO validation (422),
  authorization (403), topic validation (400), scope parsing (400),
  service-org confinement (403), and Solution resolution plus the
  inbound gate (404) in the same precedence;
- token-equivalent authority: the workflow engine superuser (with
  verified execution/Solution claims) emits globally or org-scoped,
  while a supervised service emits only into its own org;
- child frame actor, app id, organization, and ``caller_solution``
  claims are never read — the parent-verified own Solution id (or
  None) replaces ``caller_solution`` before the service call;
- the durable emit runs once per request (no retry);
- the SDK facade rides ``BifrostClient.engine_request`` with the exact HTTP
  body/path and never falls back to the network API after a failed local call;
- external callers (no injected socket) keep the HTTP path unchanged.
"""

from __future__ import annotations

import contextlib
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import httpx
import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from bifrost._local_transport import OP_EVENTS_EMIT


@pytest_asyncio.fixture
async def db_session(async_engine):
    """Exercise real flushes while rolling back seeded rows after each test."""
    async with async_engine.connect() as connection:
        transaction = await connection.begin()
        async with AsyncSession(
            bind=connection,
            expire_on_commit=False,
            join_transaction_mode="create_savepoint",
        ) as session:
            try:
                yield session
            finally:
                await session.rollback()
                await transaction.rollback()


@contextlib.asynccontextmanager
async def _db_factory(db_session):
    # Unit cases share the fixture transaction (savepoint-joined, rolled
    # back at teardown), so real local commits never leak across tests.
    yield db_session


def _context_data(**kwargs):
    data = {
        "organization": kwargs.get("organization"),
        "is_platform_admin": kwargs.get("is_platform_admin", True),
        "execution_id": kwargs.get("execution_id", "exec-events-1"),
    }
    if kwargs.get("solution_id") is not None:
        data["solution_id"] = kwargs["solution_id"]
    if kwargs.get("service") is not None:
        data["service"] = kwargs["service"]
    return data


def _workflow_principal(**kwargs):
    from src.services.execution.sdk_local_dispatch import principal_from_context

    return principal_from_context(_context_data(**kwargs))


def _service_principal(org_id=None, **kwargs):
    from src.services.execution.sdk_local_dispatch import principal_from_context

    service_id = str(kwargs.get("service_id", uuid4()))
    attempt_id = str(kwargs.get("attempt_id", uuid4()))
    return principal_from_context(
        _context_data(
            organization={"id": str(org_id)} if org_id is not None else None,
            is_platform_admin=False,
            service={"service_id": service_id, "attempt_id": attempt_id},
            execution_id=attempt_id,
        )
    )


def _frame(frame_id=None, **fields):
    return {"v": 1, "id": frame_id or f"events-{uuid4().hex}", "op": OP_EVENTS_EMIT, **fields}


async def _dispatch(db_session, principal, frame):
    from src.services.execution.sdk_local_dispatch import dispatch_frame

    return await dispatch_frame(lambda: _db_factory(db_session), principal, frame)


async def _org(db_session):
    from src.models.orm.organizations import Organization

    org = Organization(id=uuid4(), name=f"O-{uuid4().hex[:6]}", created_by="t")
    db_session.add(org)
    await db_session.flush()
    return org


async def _solution(db_session, org_id, slug, sealed=False):
    from src.models.orm.solutions import Solution

    sol = Solution(
        id=uuid4(),
        slug=slug,
        name=f"N-{slug}",
        organization_id=org_id,
    )
    if sealed:
        sol.allow_inbound_access = False
    db_session.add(sol)
    await db_session.flush()
    return sol


# =============================================================================
# Allowlist
# =============================================================================


def test_events_emit_on_sdk_allowlist_only():
    from src.services.execution.sdk_local_dispatch import (
        IMPORT_CHANNEL_ALLOWED_OPS,
        SDK_CHANNEL_ALLOWED_OPS,
    )

    assert OP_EVENTS_EMIT in SDK_CHANNEL_ALLOWED_OPS
    assert OP_EVENTS_EMIT not in IMPORT_CHANNEL_ALLOWED_OPS


@pytest.mark.asyncio
async def test_unknown_op_and_bad_version_rejected(db_session):
    denied = await _dispatch(
        db_session, _workflow_principal(), _frame(op="events.broadcast")
    )
    assert denied["ok"] is False
    assert denied["status"] == 404

    bad_version = _frame(topic="a.b", data={})
    bad_version["v"] = 999
    denied = await _dispatch(db_session, _workflow_principal(), bad_version)
    assert denied["ok"] is False
    assert denied["status"] == 400


# =============================================================================
# Success parity (workflow engine principal)
# =============================================================================


@pytest.mark.asyncio
async def test_workflow_global_emit_success_calls_durable_once(db_session):
    from src.core.constants import SYSTEM_USER_UUID

    event_id = uuid4()
    principal = _workflow_principal()
    with patch(
        "src.services.events.emit_event",
        new=AsyncMock(return_value=(event_id, 3)),
    ) as durable:
        resp = await _dispatch(
            db_session,
            principal,
            _frame(topic="acme.deal_won", data={"amount": 1}),
        )
    assert resp["ok"] is True, resp
    assert resp["result"] == {
        "event_id": str(event_id),
        "subscribers_notified": 3,
    }
    durable.assert_awaited_once()
    args, kwargs = durable.call_args
    assert args == ("acme.deal_won", {"amount": 1})
    assert kwargs["organization_id"] is None
    assert kwargs["solution_id"] is None
    assert kwargs["triggered_by"] == str(SYSTEM_USER_UUID)


@pytest.mark.asyncio
async def test_workflow_org_scoped_emit_stamps_org(db_session):
    org = await _org(db_session)
    event_id = uuid4()
    principal = _workflow_principal()
    with patch(
        "src.services.events.emit_event",
        new=AsyncMock(return_value=(event_id, 0)),
    ) as durable:
        resp = await _dispatch(
            db_session,
            principal,
            _frame(topic="a.b", data={}, scope=str(org.id)),
        )
    assert resp["ok"] is True, resp
    _, kwargs = durable.call_args
    assert kwargs["organization_id"] == org.id


@pytest.mark.asyncio
async def test_non_admin_initiator_keeps_workflow_engine_authority(db_session):
    """HTTP workflow SDK calls use the engine superuser token."""
    event_id = uuid4()
    principal = _workflow_principal(is_platform_admin=False)
    with patch(
        "src.services.events.emit_event",
        new=AsyncMock(return_value=(event_id, 0)),
    ):
        resp = await _dispatch(
            db_session, principal, _frame(topic="a.b", data={})
        )
    assert resp["ok"] is True, resp
    assert resp["result"]["event_id"] == str(event_id)


# =============================================================================
# Denial parity
# =============================================================================


@pytest.mark.asyncio
async def test_invalid_topic_returns_400(db_session):
    for topic in ("INVALID", "nodot", ""):
        resp = await _dispatch(
            db_session,
            _workflow_principal(),
            _frame(topic=topic, data={}),
        )
        assert resp["ok"] is False, topic
        assert resp["status"] == 400, topic


@pytest.mark.asyncio
async def test_invalid_scope_returns_400(db_session):
    resp = await _dispatch(
        db_session,
        _workflow_principal(),
        _frame(topic="a.b", data={}, scope="nope"),
    )
    assert resp["ok"] is False
    assert resp["status"] == 400
    assert "Invalid scope" in resp["detail"]


@pytest.mark.asyncio
async def test_missing_topic_or_bad_data_422(db_session):
    missing = await _dispatch(
        db_session, _workflow_principal(), _frame(data={})
    )
    assert missing["ok"] is False
    assert missing["status"] == 422

    bad_data = await _dispatch(
        db_session,
        _workflow_principal(),
        _frame(topic="a.b", data="not-a-dict"),
    )
    assert bad_data["ok"] is False
    assert bad_data["status"] == 422


@pytest.mark.asyncio
async def test_service_cross_org_and_global_denied(db_session):
    org = await _org(db_session)
    principal = _service_principal(org.id)
    for scope in (str(uuid4()), "GLOBAL", None):
        resp = await _dispatch(
            db_session,
            principal,
            _frame(topic="a.b", data={}, scope=scope),
        )
        assert resp["ok"] is False, scope
        assert resp["status"] == 403, scope


@pytest.mark.asyncio
async def test_service_own_org_emit_success(db_session):
    from src.core.constants import SYSTEM_USER_UUID

    org = await _org(db_session)
    event_id = uuid4()
    principal = _service_principal(org.id)
    with patch(
        "src.services.events.emit_event",
        new=AsyncMock(return_value=(event_id, 1)),
    ) as durable:
        resp = await _dispatch(
            db_session,
            principal,
            _frame(topic="a.b", data={"k": "v"}, scope=str(org.id)),
        )
    assert resp["ok"] is True, resp
    assert resp["result"]["subscribers_notified"] == 1
    durable.assert_awaited_once()
    _, kwargs = durable.call_args
    assert kwargs["organization_id"] == org.id
    assert kwargs["triggered_by"] == str(SYSTEM_USER_UUID)


# =============================================================================
# Solution gating (parent-verified identity only)
# =============================================================================


@pytest.mark.asyncio
async def test_unknown_solution_404(db_session):
    resp = await _dispatch(
        db_session,
        _workflow_principal(),
        _frame(topic="a.b", data={}, solution=str(uuid4())),
    )
    assert resp["ok"] is False
    assert resp["status"] == 404


@pytest.mark.asyncio
async def test_sealed_solution_denies_outside_caller(db_session):
    org = await _org(db_session)
    sol = await _solution(db_session, org.id, "sealed", sealed=True)
    resp = await _dispatch(
        db_session,
        _workflow_principal(),
        _frame(
            topic="a.b",
            data={},
            scope=str(org.id),
            solution=str(sol.id),
        ),
    )
    assert resp["ok"] is False
    assert resp["status"] == 404


@pytest.mark.asyncio
async def test_forged_caller_solution_claim_ignored(db_session):
    """A child ``caller_solution`` frame field never attests an own call."""
    org = await _org(db_session)
    sol = await _solution(db_session, org.id, "sealed-forge", sealed=True)
    with patch(
        "src.services.events.emit_event",
        new=AsyncMock(side_effect=AssertionError("must not emit")),
    ):
        resp = await _dispatch(
            db_session,
            _workflow_principal(),
            _frame(
                topic="a.b",
                data={},
                scope=str(org.id),
                solution=str(sol.id),
                caller_solution=str(sol.id),
                actor_email="admin@example.com",
                app_id=str(uuid4()),
            ),
        )
    assert resp["ok"] is False
    assert resp["status"] == 404


@pytest.mark.asyncio
async def test_sealed_solution_own_call_inherits_parent_solution(db_session):
    """An omitted target inherits the verified install and passes its inbound gate."""
    org = await _org(db_session)
    sol = await _solution(db_session, org.id, "sealed-own", sealed=True)
    event_id = uuid4()
    principal = _workflow_principal(solution_id=str(sol.id))
    with patch(
        "src.services.events.emit_event",
        new=AsyncMock(return_value=(event_id, 1)),
    ) as durable:
        resp = await _dispatch(
            db_session,
            principal,
            _frame(
                topic="a.b",
                data={},
                scope=str(org.id),
            ),
        )
    assert resp["ok"] is True, resp
    assert resp["result"]["event_id"] == str(event_id)
    _, kwargs = durable.call_args
    assert kwargs["solution_id"] == sol.id
    assert kwargs["organization_id"] == org.id


@pytest.mark.asyncio
async def test_open_solution_emits_with_target(db_session):
    org = await _org(db_session)
    sol = await _solution(db_session, org.id, "open")
    event_id = uuid4()
    with patch(
        "src.services.events.emit_event",
        new=AsyncMock(return_value=(event_id, 1)),
    ) as durable:
        resp = await _dispatch(
            db_session,
            _workflow_principal(),
            _frame(
                topic="a.b",
                data={},
                scope=str(org.id),
                solution=str(sol.id),
            ),
        )
    assert resp["ok"] is True, resp
    assert resp["result"]["subscribers_notified"] == 1
    _, kwargs = durable.call_args
    assert kwargs["solution_id"] == sol.id


# =============================================================================
# SDK facade mapping (no DB)
# =============================================================================


@pytest.mark.asyncio
class TestEngineRequestFacade:
    """Gate C5c: the migrated events facade rides ``engine_request``.

    ``emit`` posts the exact HTTP body, keeps the shared client's default
    timeout with no override, and errors surface as the same public
    exceptions as the HTTP path — with no dedicated-channel frames and no
    silent HTTP fallback.
    """

    def _client(self, *responses):
        client = AsyncMock()
        client.engine_request = AsyncMock(side_effect=responses)
        return client

    async def test_emit_posts_exact_body_and_returns_dict(self):
        from bifrost.events import events as events_facade

        body = {"event_id": str(uuid4()), "subscribers_notified": 2}
        client = self._client(
            httpx.Response(200, json=body, request=httpx.Request("POST", "http://x"))
        )
        with (
            patch("bifrost.events.get_client", return_value=client),
            patch("bifrost.events.resolve_scope", return_value=None),
            patch("bifrost.events.get_effective_solution", return_value=None),
            patch("bifrost.events.get_caller_solution", return_value=None),
        ):
            result = await events_facade.emit("a.b", {"k": "v"})

        assert result == body
        call = client.engine_request.await_args
        assert call.args == ("POST", "/api/events/emit")
        assert call.kwargs["json"] == {
            "topic": "a.b",
            "data": {"k": "v"},
            "scope": None,
        }
        assert "timeout" not in call.kwargs

    async def test_emit_includes_scope_solution_and_caller(self):
        from bifrost.events import events as events_facade

        body = {"event_id": str(uuid4()), "subscribers_notified": 0}
        org_id = str(uuid4())
        solution_id = str(uuid4())
        caller_id = str(uuid4())
        client = self._client(
            httpx.Response(200, json=body, request=httpx.Request("POST", "http://x"))
        )
        with (
            patch("bifrost.events.get_client", return_value=client),
            patch("bifrost.events.resolve_scope", return_value=org_id),
            patch("bifrost.events.get_effective_solution", return_value=solution_id),
            patch("bifrost.events.get_caller_solution", return_value=caller_id),
        ):
            result = await events_facade.emit(
                "a.b", {}, scope=org_id, solution=solution_id
            )

        assert result == body
        assert client.engine_request.await_args.kwargs["json"] == {
            "topic": "a.b",
            "data": {},
            "scope": org_id,
            "solution": solution_id,
            "caller_solution": caller_id,
        }

    async def test_emit_error_statuses_surface_without_channel(self):
        from bifrost.client import BifrostAuthorizationError, BifrostAPIError
        from bifrost.events import events as events_facade

        request = httpx.Request("POST", "http://engine/api/events/emit")
        for status, exc_type in (
            (400, BifrostAPIError),
            (403, BifrostAuthorizationError),
            (500, BifrostAPIError),
        ):
            client = self._client(
                httpx.Response(status, json={"detail": "denied"}, request=request)
            )
            with patch("bifrost.events.get_client", return_value=client):
                with pytest.raises(exc_type) as exc_info:
                    await events_facade.emit("a.b", {})
            assert exc_info.value.response.status_code == status
