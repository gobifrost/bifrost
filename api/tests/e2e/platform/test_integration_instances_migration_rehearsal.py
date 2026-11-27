"""Rehearse the integration-instance upgrade and guarded downgrade."""

from __future__ import annotations

import asyncio
from uuid import uuid4

import pytest
import sqlalchemy as sa
from alembic import command
from sqlalchemy.ext.asyncio import AsyncConnection

from tests.e2e.platform.test_r2b_roles_migration_rehearsal import (
    _alembic_config, _assert_safe_database_name, _create_database,
    _direct_database_url, _drop_database, _run_in_database,
    _temporary_migration_database_url, _upgrade,
)


pytestmark = pytest.mark.e2e
PREVIOUS = "20261127_integration_links"


def test_upgrade_preserves_roots_and_refuses_lossy_downgrade_with_children() -> None:
    database_name = f"bifrost_r2b_rehearsal_{uuid4().hex[:12]}"
    _assert_safe_database_name(database_name)
    database_url = _direct_database_url(database_name)
    root_id, child_id = str(uuid4()), str(uuid4())
    try:
        asyncio.run(_create_database(database_name))
        _upgrade(database_url, PREVIOUS)

        async def insert_root(connection: AsyncConnection) -> None:
            await connection.execute(sa.text(
                "INSERT INTO integrations (id, name, description, is_deleted) "
                "VALUES (CAST(:id AS uuid), 'Historical', 'preserve me', false)"
            ), {"id": root_id})

        asyncio.run(_run_in_database(database_url, insert_root))
        _upgrade(database_url, "head")

        async def verify_and_insert_child(connection: AsyncConnection) -> None:
            root = (await connection.execute(sa.text(
                "SELECT name, description, parent_integration_id, instance_key, instance_name, authentication_method "
                "FROM integrations WHERE id = CAST(:id AS uuid)"
            ), {"id": root_id})).one()
            assert root == ("Historical", "preserve me", None, "default", "Historical", "config")
            await connection.execute(sa.text(
                "INSERT INTO integrations (id, name, parent_integration_id, instance_key, instance_name, authentication_method, is_deleted) "
                "VALUES (CAST(:child AS uuid), 'internal-child', CAST(:root AS uuid), 'eu', 'Europe', 'config', false)"
            ), {"child": child_id, "root": root_id})

        asyncio.run(_run_in_database(database_url, verify_and_insert_child))
        with _temporary_migration_database_url(database_url):
            with pytest.raises(RuntimeError, match="named connection instances"):
                command.downgrade(_alembic_config(), PREVIOUS)
    finally:
        asyncio.run(_drop_database(database_name))
