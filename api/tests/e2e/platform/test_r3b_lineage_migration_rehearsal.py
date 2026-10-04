"""True pre-head rehearsal for ``alembic/versions/20261004_r3b_lineage.py``.

Builds a disposable database at the migration's ``down_revision``, seeds a
person, two executions and an agent run, then upgrades and checks the new
lineage columns: present, NULL on existing rows, foreign keys created
without validating existing rows, and removed again by the downgrade.
"""

from __future__ import annotations

import asyncio
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
from alembic import command
from sqlalchemy.ext.asyncio import AsyncConnection

from tests.e2e.platform.test_r2b_roles_migration_rehearsal import (
    _alembic_config,
    _assert_safe_database_name,
    _create_database,
    _direct_database_url,
    _drop_database,
    _run_in_database,
    _temporary_migration_database_url,
    _upgrade,
)

pytestmark = pytest.mark.e2e

PREVIOUS_REVISION = "20261003_r3b_identities"
REVISION = "20261004_r3b_lineage"
USER_ROLE_ID = UUID("00000000-0000-0000-0000-000000000006")

_EXECUTION_COLUMNS = ("run_user_id", "started_by_user_id", "root_execution_id")
# Partial indexes so ON DELETE SET NULL on a user doesn't scan executions.
_INDEXES = {
    "ix_executions_run_user_id": "executions (run_user_id) WHERE (run_user_id IS NOT NULL)",
    "ix_executions_started_by_user_id": "executions (started_by_user_id) WHERE (started_by_user_id IS NOT NULL)",
    "ix_agent_runs_run_user_id": "agent_runs (run_user_id) WHERE (run_user_id IS NOT NULL)",
}
_FOREIGN_KEYS = {
    "fk_executions_run_user_id",
    "fk_executions_started_by_user_id",
    "fk_agent_runs_run_user_id",
}


def _downgrade(database_url: str, revision: str) -> None:
    with _temporary_migration_database_url(database_url):
        command.downgrade(_alembic_config(), revision)


async def _seed(database_url: str, ids: dict[str, str]) -> None:
    async def seed(connection: AsyncConnection) -> None:
        await connection.execute(
            sa.text(
                "INSERT INTO organizations (id, name, is_active, is_provider, settings, created_by) "
                "VALUES (CAST(:id AS uuid), 'Rehearsal A', TRUE, FALSE, '{}'::jsonb, 'migration-rehearsal')"
            ),
            {"id": ids["org"]},
        )
        await connection.execute(
            sa.text(
                "INSERT INTO users (id, email, name, organization_id, is_active, is_superuser, "
                "is_verified, is_registered, base_role_id) "
                "VALUES (CAST(:id AS uuid), 'person@rehearsal.example', 'Person', CAST(:org AS uuid), "
                "TRUE, FALSE, TRUE, TRUE, CAST(:user_role AS uuid))"
            ),
            {"id": ids["person"], "org": ids["org"], "user_role": str(USER_ROLE_ID)},
        )
        for key, status in (("scheduled", "Scheduled"), ("done", "Success")):
            await connection.execute(
                sa.text(
                    "INSERT INTO executions (id, workflow_name, status, executed_by, executed_by_name, "
                    "organization_id) VALUES (CAST(:id AS uuid), 'rehearsal', :status, "
                    "CAST(:person AS uuid), 'Person', CAST(:org AS uuid))"
                ),
                {"id": ids[key], "status": status, "person": ids["person"], "org": ids["org"]},
            )
        await connection.execute(
            sa.text(
                "INSERT INTO agent_runs (id, trigger_type, status, caller_user_id) "
                "VALUES (CAST(:id AS uuid), 'api', 'completed', :person)"
            ),
            {"id": ids["agent_run"], "person": ids["person"]},
        )

    await _run_in_database(database_url, seed)


async def _state(database_url: str) -> dict:
    async def read(connection: AsyncConnection) -> dict:
        columns = {
            (table, column)
            for table, column in (
                await connection.execute(
                    sa.text(
                        "SELECT table_name, column_name FROM information_schema.columns "
                        "WHERE (table_name = 'executions' AND column_name IN "
                        "('run_user_id', 'started_by_user_id', 'root_execution_id')) "
                        "OR (table_name = 'agent_runs' AND column_name = 'run_user_id')"
                    )
                )
            ).all()
        }
        foreign_keys = {
            name: (validated, on_delete)
            for name, validated, on_delete in (
                await connection.execute(
                    sa.text(
                        "SELECT conname, convalidated, confdeltype::text FROM pg_constraint "
                        "WHERE conname = ANY(:names)"
                    ),
                    {"names": sorted(_FOREIGN_KEYS)},
                )
            ).all()
        }
        values: list = []
        if columns:
            values = [
                tuple(row)
                for row in (
                    await connection.execute(
                        sa.text(
                            "SELECT run_user_id, started_by_user_id, root_execution_id FROM executions "
                            "UNION ALL SELECT run_user_id, NULL, NULL FROM agent_runs"
                        )
                    )
                ).all()
            ]
        indexes = {
            name: definition
            for name, definition in (
                await connection.execute(
                    sa.text(
                        "SELECT indexname, indexdef FROM pg_indexes "
                        "WHERE indexname = ANY(:names)"
                    ),
                    {"names": sorted(_INDEXES)},
                )
            ).all()
        }
        return {"columns": columns, "foreign_keys": foreign_keys, "values": values, "indexes": indexes}

    return await _run_in_database(database_url, read)


def test_lineage_columns_are_added_empty_with_unvalidated_foreign_keys() -> None:
    database_name = f"bifrost_r2b_rehearsal_{uuid4().hex[:12]}"
    _assert_safe_database_name(database_name)
    database_url = _direct_database_url(database_name)
    ids = {key: str(uuid4()) for key in ("org", "person", "scheduled", "done", "agent_run")}

    try:
        asyncio.run(_create_database(database_name))
        _upgrade(database_url, PREVIOUS_REVISION)
        asyncio.run(_seed(database_url, ids))
        before = asyncio.run(_state(database_url))
        assert before == {"columns": set(), "foreign_keys": {}, "values": [], "indexes": {}}

        _upgrade(database_url, REVISION)
        after = asyncio.run(_state(database_url))
        assert after["columns"] == {
            *(("executions", column) for column in _EXECUTION_COLUMNS),
            ("agent_runs", "run_user_id"),
        }
        # Not validated (no scan of existing rows); ON DELETE SET NULL.
        assert after["foreign_keys"] == {name: (False, "n") for name in _FOREIGN_KEYS}
        assert after["values"] == [(None, None, None)] * 3
        assert {name: definition.split(" ON public.")[1].replace(" USING btree", "") for name, definition in after["indexes"].items()} == _INDEXES

        _upgrade(database_url, "head")
        assert asyncio.run(_state(database_url)) == after

        _downgrade(database_url, PREVIOUS_REVISION)
        assert asyncio.run(_state(database_url)) == before
    finally:
        asyncio.run(_drop_database(database_name))
