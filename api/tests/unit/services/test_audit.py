"""
Unit tests for the audit emission helper and actor context.
"""

from uuid import uuid4
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.services.audit import emit_audit
from src.services.audit_context import (
    ActorContext,
    clear_actor,
    current_actor,
    set_actor,
)


@pytest.fixture(autouse=True)
def _reset_actor():
    """Ensure each test starts and ends with an empty actor context."""
    clear_actor()
    yield
    clear_actor()


class _SavepointCM:
    """Async context-manager stand-in for AsyncSession.begin_nested()."""

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


def _session_mock() -> MagicMock:
    """A session mock whose begin_nested() is a usable async savepoint."""
    db = MagicMock()
    db.begin_nested = MagicMock(return_value=_SavepointCM())
    return db


class TestActorContext:
    def test_current_actor_empty_by_default(self):
        assert current_actor() is None

    def test_set_and_get_actor(self):
        ctx = ActorContext(
            user_id=uuid4(),
            organization_id=uuid4(),
            email="test@example.com",
            ip_address="1.2.3.4",
        )
        set_actor(ctx)
        assert current_actor() is ctx

    def test_clear_actor(self):
        set_actor(ActorContext(user_id=None, organization_id=None))
        clear_actor()
        assert current_actor() is None

    def test_actor_source_defaults_to_http(self):
        ctx = ActorContext(user_id=None, organization_id=None)
        assert ctx.source == "http"


class TestEmitAudit:
    @pytest.mark.asyncio
    async def test_skips_when_no_actor(self):
        """With no actor context, emit_audit should silently skip."""
        db = MagicMock()
        # If the repo were called, this would blow up.
        await emit_audit(db, "user.create", resource_type="user")
        # db.add should never be called because we short-circuit early.
        db.add.assert_not_called()

    @pytest.mark.asyncio
    async def test_writes_when_actor_present(self, monkeypatch):
        """With an actor context, emit_audit writes via the repository."""
        created = {}

        async def fake_create(**kwargs):
            created.update(kwargs)
            return MagicMock(id=uuid4())

        mock_repo = MagicMock()
        mock_repo.create = AsyncMock(side_effect=fake_create)

        def fake_repo_ctor(session):
            return mock_repo

        monkeypatch.setattr(
            "src.services.audit.AuditLogRepository", fake_repo_ctor
        )

        user_id = uuid4()
        org_id = uuid4()
        set_actor(
            ActorContext(
                user_id=user_id,
                organization_id=org_id,
                email="actor@example.com",
                ip_address="10.0.0.1",
                user_agent="pytest",
                source="http",
            )
        )

        db = _session_mock()
        resource_id = uuid4()
        await emit_audit(
            db,
            "user.create",
            resource_type="user",
            resource_id=resource_id,
            details={"email": "new@example.com"},
        )

        mock_repo.create.assert_awaited_once()
        assert created["action"] == "user.create"
        assert created["user_id"] == user_id
        assert created["organization_id"] == org_id
        assert created["resource_type"] == "user"
        assert created["resource_id"] == resource_id
        assert created["outcome"] == "success"
        assert created["source"] == "http"
        assert created["ip_address"] == "10.0.0.1"
        assert created["details"] == {"email": "new@example.com"}

    @pytest.mark.asyncio
    async def test_swallows_repository_errors(self, monkeypatch):
        """Audit failures must NOT propagate to the caller."""
        mock_repo = MagicMock()
        mock_repo.create = AsyncMock(side_effect=RuntimeError("db exploded"))
        monkeypatch.setattr(
            "src.services.audit.AuditLogRepository",
            lambda session: mock_repo,
        )

        set_actor(
            ActorContext(
                user_id=uuid4(),
                organization_id=None,
            )
        )

        # Should NOT raise.
        await emit_audit(_session_mock(), "user.create")

    @pytest.mark.asyncio
    async def test_actor_override_wins(self, monkeypatch):
        """actor_override replaces the contextvar actor."""
        captured = {}

        async def fake_create(**kwargs):
            captured.update(kwargs)
            return MagicMock(id=uuid4())

        mock_repo = MagicMock()
        mock_repo.create = AsyncMock(side_effect=fake_create)
        monkeypatch.setattr(
            "src.services.audit.AuditLogRepository", lambda session: mock_repo
        )

        # Context actor says Alice
        alice_id = uuid4()
        set_actor(ActorContext(user_id=alice_id, organization_id=None))

        # Override says Bob
        bob_id = uuid4()
        await emit_audit(
            _session_mock(),
            "auth.login.success",
            actor_override=ActorContext(
                user_id=bob_id,
                organization_id=None,
                source="http",
            ),
        )

        assert captured["user_id"] == bob_id
