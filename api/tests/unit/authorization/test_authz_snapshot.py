"""A token's facts and its epoch come from one snapshot; later commits refuse it.

These are the event-ordered races of plan §3.4: a writer committing between
the minter's fact reads and its epoch read, and a slow commit landing after
the mint. "Refused" is the validator's rule: the committed epoch no longer
equals the token's ``av``.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from src.services.authorization import tokens
from src.services.authorization.tokens import RuntimeSubject, issue_access_token
from tests.unit.authorization.committed_runs import World, committed_world, token_accepted

_SET_EPOCH = text(
    "UPDATE authz_versions SET version = :version "
    "WHERE subject_kind = 'global' AND subject_id = '00000000-0000-0000-0000-000000000000'"
)


async def _run(world: World) -> tuple[RuntimeSubject, Any, Any, Any]:
    organization = await world.organization()
    user = await world.person(organization)
    role = await world.role("secrets.read")
    execution = await world.execution(run_user=user, organization=organization)
    await world.session.commit()
    return RuntimeSubject("workflow_execution", execution.id), organization, user, role


def _grant(user: Any, role: Any, organization: Any) -> list[tuple[str, dict[str, Any]]]:
    return [
        (
            "INSERT INTO user_roles (user_id, role_id, assigned_by, assigned_at) VALUES (:user, :role, 'race', now())",
            {"user": user.id, "role": role.id},
        ),
        (
            "INSERT INTO user_role_boundaries (id, user_id, role_id, kind, organization_id) "
            "VALUES (gen_random_uuid(), :user, :role, 'organization', :organization)",
            {"user": user.id, "role": role.id, "organization": organization.id},
        ),
    ]


async def test_a_writer_committing_between_fact_reads_and_the_epoch_read_refuses_the_token(
    async_engine: AsyncEngine, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with committed_world(async_engine) as world:
        subject, organization, user, role = await _run(world)
        real_read_epoch = tokens.read_epoch

        async def commit_then_read_epoch(db: AsyncSession) -> int:
            # The minter has read every fact; a writer commits now.
            async with async_engine.begin() as writer:
                for statement, params in _grant(user, role, organization):
                    await writer.execute(text(statement), params)
            return await real_read_epoch(db)

        monkeypatch.setattr(tokens, "read_epoch", commit_then_read_epoch)
        claims = (await issue_access_token(subject)).claims

        # Facts and epoch both predate the writer.
        assert claims.usr.roles == []
        assert not await token_accepted(async_engine, claims)
        monkeypatch.undo()
        fresh = (await issue_access_token(subject)).claims
        assert [r.id for r in fresh.usr.roles] == [role.id]
        assert await token_accepted(async_engine, fresh)


async def test_a_slow_commit_landing_after_the_mint_refuses_the_token(async_engine: AsyncEngine) -> None:
    async with committed_world(async_engine) as world:
        subject, organization, user, role = await _run(world)

        async with async_engine.connect() as writer:
            transaction = await writer.begin()
            for statement, params in _grant(user, role, organization):
                await writer.execute(text(statement), params)
            # The change is written, its deferred bump queued, nothing committed.
            claims = (await issue_access_token(subject)).claims
            assert await token_accepted(async_engine, claims)
            await transaction.commit()

        assert claims.usr.roles == []
        assert not await token_accepted(async_engine, claims)


async def test_the_snapshot_is_repeatable_read_and_read_only_from_the_first_statement(
    async_engine: AsyncEngine, monkeypatch: pytest.MonkeyPatch
) -> None:
    observed: dict[str, Any] = {}
    real_load = tokens.load_runtime_authority
    real_read_epoch = tokens.read_epoch

    async def load_first(db: AsyncSession, *args: Any, **kwargs: Any) -> Any:
        observed["isolation"] = (await db.execute(text("SHOW transaction_isolation"))).scalar_one()
        observed["read_only"] = (await db.execute(text("SHOW transaction_read_only"))).scalar_one()
        observed["start"] = (await db.execute(text("SELECT pg_backend_pid(), now()"))).one()
        return await real_load(db, *args, **kwargs)

    async def epoch_last(db: AsyncSession) -> int:
        observed["end"] = (await db.execute(text("SELECT pg_backend_pid(), now()"))).one()
        return await real_read_epoch(db)

    monkeypatch.setattr(tokens, "load_runtime_authority", load_first)
    monkeypatch.setattr(tokens, "read_epoch", epoch_last)
    async with committed_world(async_engine) as world:
        subject, *_ = await _run(world)
        await issue_access_token(subject)

    assert observed["isolation"] == "repeatable read"
    assert observed["read_only"] == "on"
    # One backend, one transaction (now() is the transaction start).
    assert observed["start"] == observed["end"]


@pytest.mark.parametrize("skew", [-3600, 3600])
async def test_the_minting_hosts_clock_changes_no_outcome(
    async_engine: AsyncEngine, monkeypatch: pytest.MonkeyPatch, skew: int
) -> None:
    real_now: Callable[[], int] = tokens._now
    monkeypatch.setattr(tokens, "_now", lambda: real_now() + skew)
    async with committed_world(async_engine) as world:
        subject, *_ = await _run(world)
        claims = (await issue_access_token(subject)).claims

        assert abs(claims.iat - real_now() - skew) <= 5
        assert await token_accepted(async_engine, claims)


async def test_equality_alone_decides_after_a_failover_restores_an_epoch(async_engine: AsyncEngine) -> None:
    async with committed_world(async_engine) as world:
        subject, *_ = await _run(world)
        claims = (await issue_access_token(subject)).claims
        minted = int(claims.av)

        async with async_engine.begin() as connection:
            await connection.execute(text("SELECT authz_epoch_bump_once()"))
        assert not await token_accepted(async_engine, claims)

        # A lossy failover or restore lands back on the token's epoch, and on
        # the facts that went with it.
        async with async_engine.begin() as connection:
            await connection.execute(_SET_EPOCH, {"version": minted})
        assert await token_accepted(async_engine, claims)

        async with async_engine.begin() as connection:
            await connection.execute(_SET_EPOCH, {"version": minted + 1 if minted < 2**63 - 1 else minted - 1})
        assert not await token_accepted(async_engine, claims)
