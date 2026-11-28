"""Direct prior-head migration rehearsal for workload federation storage."""

from __future__ import annotations

import asyncio
from uuid import uuid4

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncConnection

from shared.builtin_roles import USER_ROLE_ID
from tests.e2e.platform.test_r2b_roles_migration_rehearsal import (
    _assert_safe_database_name,
    _create_database,
    _direct_database_url,
    _drop_database,
    _run_in_database,
    _upgrade,
)

pytestmark = pytest.mark.e2e

PREVIOUS_HEAD = "20261127_integration_instances"
# Verified from the published v2.0.0 tag's Alembic graph. No intermediate
# application boot is allowed between seeding this schema and upgrading.
RELEASED_HEAD = "20261107_running_run_lineage"


async def _seed_existing_identity(database_url: str, organization_id: str, identity_id: str) -> None:
    async def seed(connection: AsyncConnection) -> None:
        await connection.execute(
            sa.text(
                "INSERT INTO organizations (id, name, is_active, is_provider, settings, created_by) "
                "VALUES (CAST(:id AS uuid), 'Federation Rehearsal', TRUE, FALSE, '{}'::jsonb, 'migration-rehearsal')"
            ),
            {"id": organization_id},
        )
        await connection.execute(
            sa.text(
                "INSERT INTO users (id, email, name, is_active, is_superuser, is_verified, is_registered, "
                "is_system, is_external, organization_id, base_role_id, identity_kind) "
                "VALUES (CAST(:id AS uuid), 'federation-rehearsal@identities.bifrost.internal', "
                "'Federation Rehearsal', TRUE, FALSE, TRUE, TRUE, FALSE, FALSE, CAST(:organization AS uuid), "
                "CAST(:user_role AS uuid), 'custom')"
            ),
            {"id": identity_id, "organization": organization_id, "user_role": str(USER_ROLE_ID)},
        )

    await _run_in_database(database_url, seed)


async def _verify_upgrade(database_url: str, identity_id: str) -> None:
    async def verify(connection: AsyncConnection) -> None:
        identity = (
            await connection.execute(
                sa.text("SELECT email, identity_kind FROM users WHERE id = CAST(:id AS uuid)"), {"id": identity_id}
            )
        ).one()
        assert identity == ("federation-rehearsal@identities.bifrost.internal", "custom")
        assert await connection.scalar(sa.text("SELECT COUNT(*) FROM federated_credentials")) == 0
        triggers = set(
            (
                await connection.execute(
                    sa.text(
                        "SELECT tgname FROM pg_trigger WHERE tgrelid = 'federated_credentials'::regclass "
                        "AND NOT tgisinternal"
                    )
                )
            ).scalars()
        )
        assert triggers == {"authz_epoch_ins", "authz_epoch_upd", "authz_epoch_del", "authz_epoch_truncate"}

    await _run_in_database(database_url, verify)


@pytest.mark.parametrize("starting_revision", [RELEASED_HEAD, PREVIOUS_HEAD])
def test_direct_upgrade_preserves_existing_identities_and_needs_no_backfill(starting_revision: str) -> None:
    # The shared disposable-database helper accepts only this bounded prefix.
    database_name = f"bifrost_r2b_rehearsal_{uuid4().hex[:12]}"
    _assert_safe_database_name(database_name)
    database_url = _direct_database_url(database_name)
    organization_id, identity_id = str(uuid4()), str(uuid4())
    try:
        asyncio.run(_create_database(database_name))
        _upgrade(database_url, starting_revision)
        asyncio.run(_seed_existing_identity(database_url, organization_id, identity_id))
        _upgrade(database_url, "head")
        asyncio.run(_verify_upgrade(database_url, identity_id))
    finally:
        asyncio.run(_drop_database(database_name))
