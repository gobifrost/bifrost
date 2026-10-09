"""Rehearse name reuse from the integration migration's pre-head schema."""

from __future__ import annotations

import asyncio
from uuid import uuid4

import pytest
import sqlalchemy as sa
from alembic import command
from sqlalchemy.exc import IntegrityError
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

LEGACY_REVISION = "20261008_webhook_raw_body"


async def _insert_integration(database_url: str, integration_id: str, name: str, deleted: bool) -> None:
    async def insert(connection: AsyncConnection) -> None:
        await connection.execute(
            sa.text(
                "INSERT INTO integrations (id, name, is_deleted) "
                "VALUES (CAST(:id AS uuid), :name, :deleted)"
            ),
            {"id": integration_id, "name": name, "deleted": deleted},
        )

    await _run_in_database(database_url, insert)


def test_upgrade_allows_clean_reuse_and_rejects_unsafe_downgrade() -> None:
    database_name = f"bifrost_r2b_rehearsal_{uuid4().hex[:12]}"
    _assert_safe_database_name(database_name)
    database_url = _direct_database_url(database_name)
    name = f"retired-{uuid4().hex[:12]}"

    try:
        asyncio.run(_create_database(database_name))
        _upgrade(database_url, LEGACY_REVISION)
        asyncio.run(_insert_integration(database_url, str(uuid4()), name, True))

        _upgrade(database_url, "head")
        asyncio.run(_insert_integration(database_url, str(uuid4()), name, False))

        with pytest.raises(IntegrityError):
            asyncio.run(_insert_integration(database_url, str(uuid4()), name, False))

        with _temporary_migration_database_url(database_url):
            with pytest.raises(RuntimeError, match="Cannot restore global integration name uniqueness"):
                command.downgrade(_alembic_config(), LEGACY_REVISION)
    finally:
        asyncio.run(_drop_database(database_name))
