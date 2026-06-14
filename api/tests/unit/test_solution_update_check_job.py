"""Unit tests for the solution_update_check scheduler — diff + emit-once logic.

Verifies the per-install sweep:
- A None→version edge stores ``update_available_version`` AND emits exactly once.
- A no-change pass (stored == newly-computed) re-stores nothing and does NOT re-emit.
- An up-to-date pass (version→None edge) clears the stored value and does NOT emit.

The job redirects DB access through its own ``get_db_context`` and imports
``fetch_remote_version`` / ``emit_solution_update_available`` into its own
namespace, so both the DB context and those functions are patched on the JOB
module (a from-import means patching the source module would not take effect).
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncGenerator
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from src.jobs.schedulers import solution_update_check as mod
from src.models.orm.solutions import Solution

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def patch_scheduler_db(monkeypatch, async_session_factory):
    """Redirect the job's get_db_context to the test's session factory so the
    job's separate session sees committed test data (same factory as db_session,
    NullPool, expire_on_commit=False)."""

    @asynccontextmanager
    async def _test_db_context() -> AsyncGenerator[AsyncSession, None]:
        async with async_session_factory() as session:
            yield session

    monkeypatch.setattr(mod, "get_db_context", _test_db_context)


async def _make_install(
    db: AsyncSession,
    *,
    version: str | None,
    update_available_version: str | None,
) -> Solution:
    sol = Solution(
        id=uuid4(),
        slug=f"upd-{uuid4().hex[:8]}",
        name="UPD",
        organization_id=None,
        version=version,
        git_connected=True,
        git_repo_url="https://example.com/repo.git",
        git_ref="main",
        repo_subpath="solutions/upd",
        update_available_version=update_available_version,
    )
    db.add(sol)
    await db.flush()
    return sol


def _patch_remote(monkeypatch, version: str | None) -> None:
    async def _fake_fetch(*, repo_url, repo_subpath, ref):
        return version

    monkeypatch.setattr(mod, "fetch_remote_version", _fake_fetch)


def _patch_emit(monkeypatch) -> list[dict]:
    calls: list[dict] = []

    async def _fake_emit(**kwargs):
        calls.append(kwargs)

    monkeypatch.setattr(mod, "emit_solution_update_available", _fake_emit)
    return calls


async def test_none_to_version_emits_once_and_stores(
    db_session: AsyncSession, monkeypatch
):
    """install at 1.0.0, remote 1.1.0, no prior stored value → stores 1.1.0 + one emit."""
    sol = await _make_install(db_session, version="1.0.0", update_available_version=None)
    await db_session.commit()

    _patch_remote(monkeypatch, "1.1.0")
    calls = _patch_emit(monkeypatch)

    result = await mod.check_solution_updates()

    await db_session.refresh(sol)
    assert sol.update_available_version == "1.1.0"
    mine = [c for c in calls if c["solution_id"] == sol.id]
    assert len(mine) == 1
    assert mine[0]["available_version"] == "1.1.0"
    assert mine[0]["installed_version"] == "1.0.0"
    assert result["updates_found"] >= 1


async def test_already_stored_same_value_no_reemit(
    db_session: AsyncSession, monkeypatch
):
    """install with update_available_version already 1.1.0, remote still 1.1.0 →
    no change, no re-emit, value stays 1.1.0."""
    sol = await _make_install(
        db_session, version="1.0.0", update_available_version="1.1.0"
    )
    await db_session.commit()

    _patch_remote(monkeypatch, "1.1.0")
    calls = _patch_emit(monkeypatch)

    await mod.check_solution_updates()

    await db_session.refresh(sol)
    assert sol.update_available_version == "1.1.0"
    assert calls == []


async def test_up_to_date_clears_and_no_emit(
    db_session: AsyncSession, monkeypatch
):
    """install at 1.0.0 with stored 1.1.0, remote now 1.0.0 (they updated) →
    update_available_version cleared to None, no emit."""
    sol = await _make_install(
        db_session, version="1.0.0", update_available_version="1.1.0"
    )
    await db_session.commit()

    _patch_remote(monkeypatch, "1.0.0")
    calls = _patch_emit(monkeypatch)

    await mod.check_solution_updates()

    await db_session.refresh(sol)
    assert sol.update_available_version is None
    assert calls == []
