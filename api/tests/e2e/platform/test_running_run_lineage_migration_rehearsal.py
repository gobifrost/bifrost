"""Disposable rehearsal for the live v1.4.1 execution-lineage repair."""

from __future__ import annotations

import asyncio
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncConnection

from tests.e2e.platform.test_r2b_roles_migration_rehearsal import (
    _assert_safe_database_name,
    _create_database,
    _direct_database_url,
    _drop_database,
    _run_in_database,
    _upgrade,
)

pytestmark = pytest.mark.e2e

PREVIOUS_REVISION = "20261106_chat_execute"
REVISION = "20261107_running_run_lineage"
SYSTEM_USER_ID = "00000000-0000-0000-0000-000000000001"
USER_ROLE_ID = "00000000-0000-0000-0000-000000000006"


async def _seed(database_url: str, ids: dict[str, str]) -> None:
    async def seed(connection: AsyncConnection) -> None:
        await connection.execute(
            sa.text(
                "INSERT INTO organizations (id, name, is_active, is_provider, settings, created_by) "
                "VALUES (CAST(:id AS uuid), 'Lineage Rehearsal', TRUE, FALSE, '{}'::jsonb, 'migration-rehearsal')"
            ),
            {"id": ids["org"]},
        )
        for key, email, identity_kind in (
            ("person", "person@rehearsal.example", None),
            ("identity", "identity@rehearsal.example", "org_default"),
        ):
            await connection.execute(
                sa.text(
                    "INSERT INTO users (id, email, name, organization_id, is_active, is_superuser, "
                    "is_verified, is_registered, base_role_id, identity_kind) "
                    "VALUES (CAST(:id AS uuid), :email, :name, CAST(:org AS uuid), TRUE, FALSE, "
                    "TRUE, TRUE, CAST(:role AS uuid), :identity_kind)"
                ),
                {
                    "id": ids[key],
                    "email": email,
                    "name": key.title(),
                    "org": ids["org"],
                    "role": USER_ROLE_ID,
                    "identity_kind": identity_kind,
                },
            )
        await connection.execute(
            sa.text(
                "INSERT INTO workflows (id, name, function_name, path, organization_id, run_identity_id, endpoint_enabled) "
                "VALUES (CAST(:id AS uuid), 'rehearsal', 'rehearsal', 'workflows/rehearsal.py', "
                "CAST(:org AS uuid), CAST(:identity AS uuid), FALSE)"
            ),
            {"id": ids["workflow"], "org": ids["org"], "identity": ids["identity"]},
        )
        rows = (
            ("human_running", "Running", ids["person"], None, None, None),
            ("unattended_running", "Running", SYSTEM_USER_ID, None, None, None),
            ("unattended_cancelling", "Cancelling", SYSTEM_USER_ID, None, None, None),
            ("existing", "Running", ids["person"], ids["person"], ids["person"], ids["existing"]),
            ("partial", "Running", ids["person"], ids["person"], None, None),
            ("terminal", "Success", ids["person"], None, None, None),
        )
        for key, status, executed_by, run_user_id, started_by_user_id, root_execution_id in rows:
            await connection.execute(
                sa.text(
                    "INSERT INTO executions (id, workflow_name, status, executed_by, executed_by_name, organization_id, "
                    "workflow_id, run_user_id, started_by_user_id, root_execution_id) "
                    "VALUES (CAST(:id AS uuid), 'rehearsal', :status, CAST(:executed_by AS uuid), 'Recorded', "
                    "CAST(:org AS uuid), CAST(:workflow AS uuid), CAST(:run_user_id AS uuid), "
                    "CAST(:started_by_user_id AS uuid), CAST(:root_execution_id AS uuid))"
                ),
                {
                    "id": ids[key],
                    "status": status,
                    "executed_by": executed_by,
                    "org": ids["org"],
                    "workflow": ids["workflow"],
                    "run_user_id": run_user_id,
                    "started_by_user_id": started_by_user_id,
                    "root_execution_id": root_execution_id,
                },
            )

    await _run_in_database(database_url, seed)


async def _lineage(
    database_url: str,
) -> dict[UUID, tuple[UUID | None, UUID | None, UUID | None, UUID | None]]:
    async def read(
        connection: AsyncConnection,
    ) -> dict[UUID, tuple[UUID | None, UUID | None, UUID | None, UUID | None]]:
        rows = await connection.execute(
            sa.text(
                "SELECT id, executed_by, run_user_id, started_by_user_id, root_execution_id "
                "FROM executions WHERE workflow_name = 'rehearsal'"
            )
        )
        return {row[0]: tuple(row[1:]) for row in rows.all()}

    return await _run_in_database(database_url, read)


def test_running_and_cancelling_legacy_rows_receive_only_durable_lineage_repairs() -> None:
    database_name = f"bifrost_r2b_rehearsal_{uuid4().hex[:12]}"
    _assert_safe_database_name(database_name)
    database_url = _direct_database_url(database_name)
    ids = {
        key: str(uuid4())
        for key in (
            "org",
            "person",
            "identity",
            "workflow",
            "human_running",
            "unattended_running",
            "unattended_cancelling",
            "existing",
            "partial",
            "terminal",
        )
    }
    try:
        asyncio.run(_create_database(database_name))
        _upgrade(database_url, PREVIOUS_REVISION)
        asyncio.run(_seed(database_url, ids))
        before = asyncio.run(_lineage(database_url))
        assert before[UUID(ids["human_running"])] == (UUID(ids["person"]), None, None, None)
        assert before[UUID(ids["unattended_running"])] == (UUID(SYSTEM_USER_ID), None, None, None)

        _upgrade(database_url, REVISION)

        assert asyncio.run(_lineage(database_url)) == {
            UUID(ids["human_running"]): (
                UUID(ids["person"]), UUID(ids["person"]), UUID(ids["person"]), UUID(ids["human_running"])
            ),
            UUID(ids["unattended_running"]): (
                UUID(SYSTEM_USER_ID), UUID(ids["identity"]), UUID(ids["identity"]), UUID(ids["unattended_running"])
            ),
            UUID(ids["unattended_cancelling"]): (
                UUID(SYSTEM_USER_ID), UUID(ids["identity"]), UUID(ids["identity"]), UUID(ids["unattended_cancelling"])
            ),
            UUID(ids["existing"]): (
                UUID(ids["person"]), UUID(ids["person"]), UUID(ids["person"]), UUID(ids["existing"])
            ),
            UUID(ids["partial"]): (UUID(ids["person"]), UUID(ids["person"]), None, None),
            UUID(ids["terminal"]): (UUID(ids["person"]), None, None, None),
        }
    finally:
        asyncio.run(_drop_database(database_name))
