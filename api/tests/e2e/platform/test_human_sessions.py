"""Durable human refresh-session serialization against PostgreSQL."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import UUID

import pytest
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from shared import human_sessions
from src.core.security import decode_token
from src.models.orm.human_sessions import HumanAuthSession
from src.services.authorization.tokens import HumanSubject
from tests.unit.authorization.committed_runs import committed_world

pytestmark = pytest.mark.e2e


@dataclass
class _SessionServiceHarness:
    """Observes session transaction scope without changing service behavior."""

    transaction_depth: ContextVar[int] = field(
        default_factory=lambda: ContextVar("human_session_test_transaction_depth", default=0),
    )
    mint_transaction_counts: list[int] = field(default_factory=list)
    commit_started: asyncio.Event | None = None
    allow_commit: asyncio.Event | None = None

    @property
    def active_transactions(self) -> int:
        """Only this task's connections can make its own mint nested."""
        return self.transaction_depth.get()


@pytest.fixture
def human_session_service(
    async_engine: AsyncEngine,
    monkeypatch: pytest.MonkeyPatch,
) -> _SessionServiceHarness:
    """Point session operations at the test engine and stub only access minting."""
    harness = _SessionServiceHarness()

    @asynccontextmanager
    async def db_context() -> AsyncIterator[AsyncSession]:
        async with AsyncSession(async_engine, expire_on_commit=False) as db:
            depth_token = harness.transaction_depth.set(harness.active_transactions + 1)
            try:
                yield db
                if harness.commit_started is not None:
                    harness.commit_started.set()
                    assert harness.allow_commit is not None
                    await harness.allow_commit.wait()
                await db.commit()
            except BaseException:
                await db.rollback()
                raise
            finally:
                harness.transaction_depth.reset(depth_token)

    async def issue_access_token(subject: HumanSubject) -> SimpleNamespace:
        harness.mint_transaction_counts.append(harness.active_transactions)
        assert harness.active_transactions == 0
        return SimpleNamespace(
            token=f"access-{subject.user_id}-{subject.session_id}",
            expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        )

    monkeypatch.setattr(human_sessions, "get_db_context", db_context)
    monkeypatch.setattr(human_sessions, "issue_access_token", issue_access_token)
    return harness


def _refresh_claims(refresh_token: str) -> tuple[UUID, str]:
    payload = decode_token(refresh_token, expected_type="refresh")
    assert payload is not None
    return UUID(payload["sid"]), payload["jti"]


async def _active_session_count(engine: AsyncEngine, user_id: UUID) -> int:
    async with AsyncSession(engine) as db:
        return await db.scalar(
            select(func.count())
            .select_from(HumanAuthSession)
            .where(HumanAuthSession.user_id == user_id, HumanAuthSession.revoked_at.is_(None))
        ) or 0


async def _session_count(engine: AsyncEngine, user_id: UUID) -> int:
    async with AsyncSession(engine) as db:
        return await db.scalar(
            select(func.count()).select_from(HumanAuthSession).where(HumanAuthSession.user_id == user_id)
        ) or 0


async def test_issue_prunes_expired_sessions_for_the_authenticated_user(
    async_engine: AsyncEngine,
    human_session_service: _SessionServiceHarness,
) -> None:
    async with committed_world(async_engine) as world:
        organization = await world.organization()
        user = await world.person(organization)
        await world.session.commit()
        await human_sessions.issue_human_session(user.id)
        async with AsyncSession(async_engine) as db:
            await db.execute(
                update(HumanAuthSession)
                .where(HumanAuthSession.user_id == user.id)
                .values(expires_at=datetime.now(timezone.utc) - timedelta(seconds=1))
            )
            await db.commit()

        await human_sessions.issue_human_session(user.id)

        assert await _session_count(async_engine, user.id) == 1


async def test_concurrent_refresh_and_revoke_all_leave_no_live_successor(
    async_engine: AsyncEngine,
    human_session_service: _SessionServiceHarness,
) -> None:
    async with committed_world(async_engine) as world:
        organization = await world.organization()
        user = await world.person(organization)
        await world.session.commit()
        issued = await human_sessions.issue_human_session(user.id)
        _, issued_jti = _refresh_claims(issued.refresh_token)
        assert await human_sessions.has_live_refresh_token_jti(world.session, user.id, issued_jti) is True

        rotation, revocation = await asyncio.gather(
            human_sessions.rotate_human_session(issued.refresh_token),
            human_sessions.revoke_all_human_sessions(user.id),
            return_exceptions=True,
        )

        assert revocation == 1
        assert await _active_session_count(async_engine, user.id) == 0
        if isinstance(rotation, human_sessions.SessionTokens):
            _, successor_jti = _refresh_claims(rotation.refresh_token)
            assert (
                await human_sessions.has_live_refresh_token_jti(world.session, user.id, successor_jti)
            ) is False
        else:
            assert isinstance(rotation, human_sessions.HumanSessionUnavailable)


async def test_concurrent_refresh_rotations_allow_exactly_one_successor(
    async_engine: AsyncEngine,
    human_session_service: _SessionServiceHarness,
) -> None:
    async with committed_world(async_engine) as world:
        organization = await world.organization()
        user = await world.person(organization)
        await world.session.commit()
        issued = await human_sessions.issue_human_session(user.id)

        first, second = await asyncio.gather(
            human_sessions.rotate_human_session(issued.refresh_token),
            human_sessions.rotate_human_session(issued.refresh_token),
            return_exceptions=True,
        )

        results = [first, second]
        successors = [result for result in results if isinstance(result, human_sessions.SessionTokens)]
        refusals = [result for result in results if isinstance(result, human_sessions.HumanSessionUnavailable)]
        assert len(successors) == 1
        assert len(refusals) == 1
        _, successor_jti = _refresh_claims(successors[0].refresh_token)
        assert await human_sessions.has_live_refresh_token_jti(world.session, user.id, successor_jti) is True


async def test_revocation_after_refresh_candidate_mint_refuses_rotation(
    async_engine: AsyncEngine,
    human_session_service: _SessionServiceHarness,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async with committed_world(async_engine) as world:
        organization = await world.organization()
        user = await world.person(organization)
        await world.session.commit()
        issued = await human_sessions.issue_human_session(user.id)
        mint_started = asyncio.Event()
        allow_mint = asyncio.Event()

        async def blocked_mint(subject: HumanSubject) -> SimpleNamespace:
            assert human_session_service.active_transactions == 0
            mint_started.set()
            await allow_mint.wait()
            return SimpleNamespace(
                token=f"access-{subject.user_id}-{subject.session_id}",
                expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
            )

        monkeypatch.setattr(human_sessions, "issue_access_token", blocked_mint)
        rotation = asyncio.create_task(human_sessions.rotate_human_session(issued.refresh_token))
        await mint_started.wait()
        assert await human_sessions.revoke_all_human_sessions(user.id) == 1
        allow_mint.set()

        with pytest.raises(human_sessions.HumanSessionUnavailable):
            _ = await rotation


async def test_access_candidate_mints_after_preflight_and_before_mutation_transaction(
    async_engine: AsyncEngine,
    human_session_service: _SessionServiceHarness,
) -> None:
    async with committed_world(async_engine) as world:
        organization = await world.organization()
        user = await world.person(organization)
        await world.session.commit()
        issued = await human_sessions.issue_human_session(user.id)
        await human_sessions.rotate_human_session(issued.refresh_token)

        assert human_session_service.mint_transaction_counts == [0, 0]


async def test_logout_from_an_old_access_session_revokes_its_rotated_successor(
    async_engine: AsyncEngine,
    human_session_service: _SessionServiceHarness,
) -> None:
    async with committed_world(async_engine) as world:
        organization = await world.organization()
        user = await world.person(organization)
        await world.session.commit()
        issued = await human_sessions.issue_human_session(user.id)
        session_id, _ = _refresh_claims(issued.refresh_token)
        rotated = await human_sessions.rotate_human_session(issued.refresh_token)
        _, successor_jti = _refresh_claims(rotated.refresh_token)

        assert await human_sessions.revoke_human_session(user.id, session_id) == 1
        assert await human_sessions.has_live_refresh_token_jti(world.session, user.id, successor_jti) is False


async def test_cancelled_session_issue_rolls_back_its_flushed_registry_row(
    async_engine: AsyncEngine,
    human_session_service: _SessionServiceHarness,
) -> None:
    async with committed_world(async_engine) as world:
        organization = await world.organization()
        user = await world.person(organization)
        await world.session.commit()
        human_session_service.commit_started = asyncio.Event()
        human_session_service.allow_commit = asyncio.Event()
        issue = asyncio.create_task(human_sessions.issue_human_session(user.id))
        await human_session_service.commit_started.wait()
        issue.cancel()

        with pytest.raises(asyncio.CancelledError):
            _ = await issue

        assert await _active_session_count(async_engine, user.id) == 0
