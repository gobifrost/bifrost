"""The desired package generation: recorded in order, read by executors."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import AsyncGenerator

import pytest
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.models.orm.package_changes import PackageChange
from src.services import package_generation
from src.services.package_generation import (
    change_message,
    current_package_generation,
    package_changes_after,
    record_package_change,
)


@pytest.fixture(autouse=True)
async def sessions(async_session_factory: async_sessionmaker[AsyncSession], monkeypatch):
    @asynccontextmanager
    async def context() -> AsyncGenerator[AsyncSession, None]:
        async with async_session_factory() as session:
            yield session
            await session.commit()

    monkeypatch.setattr(package_generation, "get_db_context", context)
    yield async_session_factory
    async with async_session_factory() as session:
        await session.execute(delete(PackageChange))
        await session.commit()


async def _record(sessions, action: str, package: str | None) -> None:  # type: ignore[no-untyped-def]
    async with sessions() as db:
        await record_package_change(db, action=action, package=package, version=None, run_id="r")
        await db.commit()


async def test_executors_read_newer_changes_in_order(sessions) -> None:  # type: ignore[no-untyped-def]
    start = await current_package_generation()
    await _record(sessions, "install", "humanize")
    await _record(sessions, "uninstall", "humanize")

    changes = await package_changes_after(start)

    assert [(c.action, c.package) for c in changes] == [
        ("install", "humanize"),
        ("uninstall", "humanize"),
    ]
    assert await current_package_generation() == changes[-1].id
    assert await package_changes_after(changes[-1].id) == []
    assert change_message(changes[0]) == {
        "action": "install",
        "package": "humanize",
        "version": None,
        "run_id": "r",
    }


async def test_a_change_is_visible_only_once_its_transaction_commits(sessions) -> None:  # type: ignore[no-untyped-def]
    start = await current_package_generation()
    async with sessions() as db:
        await record_package_change(db, action="install", package=None, version=None, run_id="r")
        assert await package_changes_after(start) == []
        await db.commit()
    assert len(await package_changes_after(start)) == 1


async def test_a_change_wakes_executors_once_it_commits(sessions, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from unittest.mock import AsyncMock

    from src.services import work_dispatch

    wake = AsyncMock()
    monkeypatch.setattr(work_dispatch, "wake_pool_workers", wake)
    async with sessions() as db:
        await record_package_change(db, action="install", package=None, version=None, run_id="r")
        await asyncio.sleep(0)
        wake.assert_not_awaited()  # executors would find nothing to apply yet
        await db.commit()
        await asyncio.sleep(0)
        wake.assert_awaited_once_with()


async def test_concurrent_changes_commit_in_generation_order(sessions) -> None:  # type: ignore[no-untyped-def]
    """A later generation never commits before an earlier one, so an executor
    that has applied generation N can never later find N-1."""
    start = await current_package_generation()
    async with sessions() as first:
        await record_package_change(first, action="install", package="a", version=None, run_id="r")
        second = asyncio.create_task(_record(sessions, "install", "b"))
        await asyncio.sleep(0.3)
        assert not second.done(), "the second change waited for the first to commit"
        await first.commit()
    await asyncio.wait_for(second, timeout=10)

    changes = await package_changes_after(start)
    assert [c.package for c in changes] == ["a", "b"]
