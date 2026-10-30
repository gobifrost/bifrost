"""The authority epoch row, its protection and its once-per-transaction bump."""

from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine

_EPOCH = text(
    "SELECT version FROM authz_versions "
    "WHERE subject_kind = 'global' AND subject_id = '00000000-0000-0000-0000-000000000000'"
)
_BUMP = text("SELECT authz_epoch_bump_once()")


async def _committed_epoch(engine: AsyncEngine) -> int:
    async with engine.connect() as connection:
        return (await connection.execute(_EPOCH)).scalar_one()


async def test_exactly_one_global_epoch_row_is_seeded(async_engine: AsyncEngine) -> None:
    async with async_engine.connect() as connection:
        rows = (
            await connection.execute(text("SELECT subject_kind, subject_id::text FROM authz_versions"))
        ).all()
    assert rows == [("global", "00000000-0000-0000-0000-000000000000")]


async def test_bump_changes_the_epoch_once_per_transaction(async_engine: AsyncEngine) -> None:
    before = await _committed_epoch(async_engine)

    async with async_engine.begin() as connection:
        await connection.execute(_BUMP)
        first = (await connection.execute(_EPOCH)).scalar_one()
        await connection.execute(_BUMP)
        second = (await connection.execute(_EPOCH)).scalar_one()

    assert first != before
    assert second == first
    assert await _committed_epoch(async_engine) == first

    # The flag is transaction-local: the next transaction bumps again.
    async with async_engine.begin() as connection:
        await connection.execute(_BUMP)
    assert await _committed_epoch(async_engine) != first


async def test_rolled_back_bump_leaves_the_epoch_unchanged(async_engine: AsyncEngine) -> None:
    before = await _committed_epoch(async_engine)

    async with async_engine.connect() as connection:
        transaction = await connection.begin()
        await connection.execute(_BUMP)
        await transaction.rollback()

    assert await _committed_epoch(async_engine) == before


async def test_rolled_back_savepoint_undoes_its_bump_and_its_flag(async_engine: AsyncEngine) -> None:
    before = await _committed_epoch(async_engine)

    async with async_engine.begin() as connection:
        savepoint = await connection.begin_nested()
        await connection.execute(_BUMP)
        await savepoint.rollback()
        assert (await connection.execute(_EPOCH)).scalar_one() == before
        # The flag went with the savepoint, so this bump is not skipped.
        await connection.execute(_BUMP)

    assert await _committed_epoch(async_engine) != before


@pytest.mark.parametrize(
    "statement",
    [
        "DELETE FROM authz_versions",
        "DELETE FROM authz_versions WHERE subject_kind = 'global'",
        "TRUNCATE authz_versions",
    ],
)
async def test_the_epoch_row_cannot_be_deleted_or_truncated(
    async_engine: AsyncEngine, statement: str
) -> None:
    before = await _committed_epoch(async_engine)

    with pytest.raises(DBAPIError, match="cannot be deleted or truncated"):
        async with async_engine.begin() as connection:
            await connection.execute(text(statement))

    assert await _committed_epoch(async_engine) == before


async def test_a_fresh_install_records_protocol_one(async_engine: AsyncEngine) -> None:
    # The test database is migrated from an empty schema, which the
    # migration recognises as a fresh install.
    async with async_engine.connect() as connection:
        protocols = (
            await connection.execute(text("SELECT protocol FROM authz_protocol_state"))
        ).scalars().all()
    assert 1 in protocols
