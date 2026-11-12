"""Rehearse upgrades and direct-write enforcement of the provider identity."""

import asyncio
from uuid import uuid4

import pytest
import sqlalchemy as sa
from alembic import command
from sqlalchemy.ext.asyncio import AsyncConnection

from tests.e2e.platform.test_r2b_roles_migration_rehearsal import (
    _alembic_config,
    _create_database,
    _current_revision,
    _direct_database_url,
    _drop_database,
    _run_in_database,
    _temporary_migration_database_url,
    _upgrade,
)

pytestmark = pytest.mark.e2e

PREVIOUS = "20261111_integration_notes"
REVISION = "20261111_provider_identity"
PROVIDER_ID = "00000000-0000-0000-0000-000000000002"
CUSTOMER_ID = "00000000-0000-0000-0000-000000001333"


def _execute(database_url: str, sql: str) -> None:
    async def execute(connection: AsyncConnection) -> None:
        await connection.execute(sa.text(sql))

    asyncio.run(_run_in_database(database_url, execute))


def test_provider_identity_upgrade_and_constraints() -> None:
    """Valid installs upgrade; malformed rows stop without being rewritten."""
    database_name = f"bifrost_r2b_rehearsal_{uuid4().hex[:12]}"
    database_url = _direct_database_url(database_name)
    asyncio.run(_create_database(database_name))
    try:
        _upgrade(database_url, PREVIOUS)
        _execute(database_url, f"""
            INSERT INTO organizations (id, name, is_active, is_provider, settings, created_by)
            VALUES ('{CUSTOMER_ID}', 'Customer', TRUE, FALSE, '{{}}', 'rehearsal')
        """)
        _upgrade(database_url, REVISION)
        assert _current_revision(database_url) == REVISION

        # Normal edits preserve the existing provider and customer identities.
        _execute(database_url, "UPDATE organizations SET name = name || ' Updated'")
        for sql in (
            f"UPDATE organizations SET is_provider = TRUE WHERE id = '{CUSTOMER_ID}'",
            f"UPDATE organizations SET is_provider = FALSE WHERE id = '{PROVIDER_ID}'",
            "INSERT INTO organizations (id, name, is_provider, created_by) "
            "VALUES (gen_random_uuid(), 'Second Provider', TRUE, 'rehearsal')",
        ):
            with pytest.raises(sa.exc.IntegrityError, match="ck_organizations_provider_identity"):
                _execute(database_url, sql)

        # Downgrade removes only enforcement. Rehearse inconsistent legacy rows
        # and verify failed upgrades leave the rows and migration revision intact.
        with _temporary_migration_database_url(database_url):
            command.downgrade(_alembic_config(), PREVIOUS)
        for org_id, bad_flag, restored_flag in (
            (CUSTOMER_ID, "TRUE", "FALSE"),
            (PROVIDER_ID, "FALSE", "TRUE"),
        ):
            _execute(database_url, f"UPDATE organizations SET is_provider = {bad_flag} WHERE id = '{org_id}'")
            with pytest.raises(RuntimeError, match=f"inconsistent organization IDs: {org_id}"):
                _upgrade(database_url, REVISION)
            assert _current_revision(database_url) == PREVIOUS

            async def read_flag(connection: AsyncConnection) -> bool:
                return bool(await connection.scalar(sa.text(
                    f"SELECT is_provider FROM organizations WHERE id = '{org_id}'"
                )))

            assert asyncio.run(_run_in_database(database_url, read_flag)) == (bad_flag == "TRUE")
            _execute(database_url, f"UPDATE organizations SET is_provider = {restored_flag} WHERE id = '{org_id}'")
        _upgrade(database_url, "head")

        with _temporary_migration_database_url(database_url):
            command.downgrade(_alembic_config(), PREVIOUS)
        # Simulate a legacy install whose seeded provider was removed directly.
        _execute(database_url, f"DELETE FROM users WHERE organization_id = '{PROVIDER_ID}'")
        _execute(database_url, f"DELETE FROM organizations WHERE id = '{PROVIDER_ID}'")

        async def organization_rows(connection: AsyncConnection) -> list:
            return list((await connection.execute(sa.text(
                "SELECT id, name, is_provider FROM organizations ORDER BY id"
            ))).all())

        before = asyncio.run(_run_in_database(database_url, organization_rows))
        with pytest.raises(RuntimeError, match="canonical provider exists: False"):
            _upgrade(database_url, REVISION)
        assert _current_revision(database_url) == PREVIOUS
        assert asyncio.run(_run_in_database(database_url, organization_rows)) == before
    finally:
        asyncio.run(_drop_database(database_name))
