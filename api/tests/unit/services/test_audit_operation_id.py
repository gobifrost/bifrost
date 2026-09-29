"""Unit tests for resolving ``operation_id`` at emit_audit() time from the
request's ASGI scope (R2a-2 Part 1, post-review rework).

``emit_audit`` no longer reads a pre-stamped ``operation_id`` off the actor;
it reads ``request.scope["route"]`` live, via the scope dict stashed by
``src.core.app_wiring``'s request-context middleware
(``set_request_scope`` / ``current_request_scope``). This works uniformly
for the main app and the worker-local SDK socket app because it relies only
on the public ASGI routing contract (Starlette mutates the same scope dict
in place once it matches a route) — see
tests/unit/services/test_worker_sdk_http.py::TestOperationIdCapture and
tests/unit/core/test_main_app_operation_id_capture.py for the two apps'
real-routing coverage.
"""

from dataclasses import dataclass
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from src.services.audit import emit_audit
from src.services.audit_context import (
    ActorContext,
    clear_actor,
    clear_request_scope,
    set_actor,
    set_request_scope,
)


@dataclass
class _FakeRoute:
    operation_id: str | None


class _SavepointCM:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


def _session_mock() -> MagicMock:
    db = MagicMock()
    db.begin_nested = MagicMock(return_value=_SavepointCM())
    return db


@pytest.fixture(autouse=True)
def _reset_context():
    clear_actor()
    clear_request_scope()
    yield
    clear_actor()
    clear_request_scope()


class TestEmitAuditResolvesOperationIdFromScope:
    @pytest.mark.asyncio
    async def test_catalogued_route_in_scope_is_recorded(self, monkeypatch):
        created = {}

        async def fake_create(**kwargs):
            created.update(kwargs)
            return MagicMock(id=uuid4())

        mock_repo = MagicMock()
        mock_repo.create = AsyncMock(side_effect=fake_create)
        monkeypatch.setattr(
            "src.services.audit.AuditLogRepository", lambda session: mock_repo
        )

        set_actor(ActorContext(user_id=uuid4(), organization_id=uuid4()))
        set_request_scope({"route": _FakeRoute(operation_id="agents.list")})

        await emit_audit(_session_mock(), "agents.list")

        assert created["operation_id"] == "agents.list"

    @pytest.mark.asyncio
    async def test_uncatalogued_route_in_scope_is_none(self, monkeypatch):
        created = {}

        async def fake_create(**kwargs):
            created.update(kwargs)
            return MagicMock(id=uuid4())

        mock_repo = MagicMock()
        mock_repo.create = AsyncMock(side_effect=fake_create)
        monkeypatch.setattr(
            "src.services.audit.AuditLogRepository", lambda session: mock_repo
        )

        set_actor(ActorContext(user_id=uuid4(), organization_id=uuid4()))
        set_request_scope({"route": _FakeRoute(operation_id=None)})

        await emit_audit(_session_mock(), "some.uncatalogued.action")

        assert created["operation_id"] is None

    @pytest.mark.asyncio
    async def test_no_request_scope_is_none(self, monkeypatch):
        """Non-HTTP callers (actor_override, no request in flight) get None."""
        created = {}

        async def fake_create(**kwargs):
            created.update(kwargs)
            return MagicMock(id=uuid4())

        mock_repo = MagicMock()
        mock_repo.create = AsyncMock(side_effect=fake_create)
        monkeypatch.setattr(
            "src.services.audit.AuditLogRepository", lambda session: mock_repo
        )

        await emit_audit(
            _session_mock(),
            "policy_rule.create",
            actor_override=ActorContext(user_id=None, organization_id=None),
        )

        assert created["operation_id"] is None

    @pytest.mark.asyncio
    async def test_scope_with_no_matched_route_is_none(self, monkeypatch):
        """A scope was stashed, but routing hadn't matched anything yet."""
        created = {}

        async def fake_create(**kwargs):
            created.update(kwargs)
            return MagicMock(id=uuid4())

        mock_repo = MagicMock()
        mock_repo.create = AsyncMock(side_effect=fake_create)
        monkeypatch.setattr(
            "src.services.audit.AuditLogRepository", lambda session: mock_repo
        )

        set_actor(ActorContext(user_id=uuid4(), organization_id=uuid4()))
        set_request_scope({})

        await emit_audit(_session_mock(), "agents.list")

        assert created["operation_id"] is None
