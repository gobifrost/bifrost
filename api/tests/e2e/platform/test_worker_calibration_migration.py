"""Legacy and feature upgrade paths converge without losing either schema."""

import asyncio
from uuid import uuid4

import pytest
import sqlalchemy as sa
from alembic.script import ScriptDirectory

from tests.e2e.platform.test_r2b_roles_migration_rehearsal import (
    _alembic_config, _create_database, _direct_database_url, _drop_database, _run_in_database, _upgrade,
)

pytestmark = pytest.mark.e2e


@pytest.mark.parametrize("prior", [
    "20261108_worker_calibration", "20261109_deprecate_slot_fields",
    "20261109_reserved_fast_path", "20261109_solution_git_source",
])
async def test_both_upgrade_paths_converge(prior):
    head = ScriptDirectory.from_config(_alembic_config()).get_current_head()
    name = f"bifrost_r2b_rehearsal_{uuid4().hex[:12]}"
    url = _direct_database_url(name)
    await _create_database(name)
    try:
        await asyncio.to_thread(_upgrade, url, prior)
        await asyncio.to_thread(_upgrade, url, "head")

        async def verify(connection):
            assert (await connection.execute(sa.text("SELECT version_num FROM alembic_version"))).scalars().all() == [head]
            columns = (await connection.execute(sa.text(
                "SELECT table_name,column_name FROM information_schema.columns "
                "WHERE table_schema='public' AND column_name IN ('memory_calibration','runs_platform_jobs','execution_lane')"
            ))).all()
            assert set(columns) == {
                ("execution_workers", "memory_calibration"), ("scheduler_replicas", "runs_platform_jobs"),
                ("workflows", "execution_lane"), ("executions", "execution_lane"),
                ("work_dispatches", "execution_lane"), ("execution_workers", "execution_lane"),
            }
            assert await connection.scalar(sa.text("SELECT to_regclass('ix_execution_workers_calibration')"))
            assert await connection.scalar(sa.text(
                "SELECT is_nullable FROM information_schema.columns "
                "WHERE table_schema='public' AND table_name='solutions' AND column_name='git_source_id'"
            )) == "YES"
            assert await connection.scalar(sa.text(
                "SELECT confdeltype::text FROM pg_constraint WHERE conname='fk_solutions_git_source'"
            )) == "a"  # Source deletion must not erase an install's binding.

        await _run_in_database(url, verify)
    finally:
        await _drop_database(name)
