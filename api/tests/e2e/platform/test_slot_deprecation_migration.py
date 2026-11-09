"""Rehearse the non-destructive slot deprecation over the prior schema.

No worker jobs are needed: the boundary is PostgreSQL migration/old and new
writers. Existing worker tests exercise admission and ownership fencing.
"""

from __future__ import annotations

import asyncio
import logging
from uuid import uuid4

import pytest
import sqlalchemy as sa
from alembic import command
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession

from src.models.orm.execution_workers import ExecutionWorker
from src.models.orm.scheduler_diagnostics import SchedulerReplica
from tests.e2e.platform.test_r2b_roles_migration_rehearsal import (
    _alembic_config,
    _create_database,
    _direct_database_url,
    _drop_database,
    _run_in_database,
    _temporary_migration_database_url,
    _upgrade,
)

pytestmark = pytest.mark.e2e


async def _seed_legacy(connection: AsyncConnection) -> None:
    await connection.execute(sa.text("""
        INSERT INTO scheduler_replicas (id, hostname, pid, job_slots)
        VALUES ('legacy-api', 'api', 1, 0), ('legacy-worker', 'worker', 2, 3)
    """))


async def _assert_compatible_writers(connection: AsyncConnection) -> None:
    rows = (await connection.execute(sa.text("""
        SELECT id, job_slots, runs_platform_jobs FROM scheduler_replicas ORDER BY id
    """))).all()
    assert rows == [('legacy-api', 0, False), ('legacy-worker', 3, True)]

    # Old code omits the new flag entirely and retains its original count.
    await connection.execute(sa.text("""
        INSERT INTO scheduler_replicas (id, hostname, pid, job_slots)
        VALUES ('old-after-upgrade', 'old', 3, 4)
    """))
    assert (await connection.execute(sa.text("""
        SELECT job_slots, runs_platform_jobs FROM scheduler_replicas
        WHERE id = 'old-after-upgrade'
    """))).one() == (4, True)
    await connection.execute(sa.text("""
        UPDATE scheduler_replicas SET job_slots = 0 WHERE id = 'old-after-upgrade'
    """))
    assert await connection.scalar(sa.text("""
        SELECT runs_platform_jobs FROM scheduler_replicas WHERE id = 'old-after-upgrade'
    """)) is False

    # New code omits both legacy fields. Older readers still receive usable
    # values, including for an API control process that runs no platform jobs.
    async with AsyncSession(bind=connection, expire_on_commit=False) as session:
        worker = ExecutionWorker(form="static", state="starting", resource_class="standard")
        replica = SchedulerReplica(id="new-api", hostname="new", pid=4, runs_platform_jobs=False)
        session.add_all([worker, replica])
        await session.flush()
        assert await connection.scalar(sa.text(
            "SELECT slots FROM execution_workers WHERE id = :id"
        ), {"id": worker.id}) == 0
        assert await connection.scalar(sa.text(
            "SELECT job_slots FROM scheduler_replicas WHERE id = 'new-api'"
        )) == 0
        replica.runs_platform_jobs = True
        await session.flush()
        assert await connection.scalar(sa.text(
            "SELECT job_slots FROM scheduler_replicas WHERE id = 'new-api'"
        )) == 1

        # Legacy heartbeats can still update the same row during a handoff.
        await connection.execute(sa.text("""
            UPDATE scheduler_replicas SET job_slots = 0 WHERE id = 'new-api'
        """))
        await session.refresh(replica)
        assert replica.runs_platform_jobs is False
        await session.commit()


async def _assert_legacy_rollback(connection: AsyncConnection) -> None:
    assert (await connection.execute(sa.text("""
        SELECT id, job_slots FROM scheduler_replicas ORDER BY id
    """))).all() == [
        ('legacy-api', 0), ('legacy-worker', 3), ('new-api', 0), ('old-after-upgrade', 0),
    ]
    assert await connection.scalar(sa.text("""
        SELECT count(*) FROM information_schema.columns
        WHERE table_name = 'scheduler_replicas' AND column_name = 'runs_platform_jobs'
    """)) == 0


def test_deprecation_preserves_old_and_new_writers() -> None:
    # Reuse the existing disposable-database helper and its strict name guard.
    database_name = f"bifrost_r2b_rehearsal_{uuid4().hex[:12]}"
    database_url = _direct_database_url(database_name)
    logger = logging.getLogger("slot-deprecation-rehearsal")
    try:
        asyncio.run(_create_database(database_name))
        _upgrade(database_url, "20261108_workspace_field_utc")
        asyncio.run(_run_in_database(database_url, _seed_legacy))
        _upgrade(database_url, "head")
        assert not logger.disabled
        asyncio.run(_run_in_database(database_url, _assert_compatible_writers))
        with _temporary_migration_database_url(database_url):
            command.downgrade(_alembic_config(), "20261108_workspace_field_utc")
        asyncio.run(_run_in_database(database_url, _assert_legacy_rollback))
    finally:
        asyncio.run(_drop_database(database_name))
