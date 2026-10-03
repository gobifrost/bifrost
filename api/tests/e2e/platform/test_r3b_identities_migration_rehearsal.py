"""True pre-head rehearsal for ``alembic/versions/20261003_r3b_identities.py``.

Builds a disposable database at the migration's ``down_revision``, seeds two
customer organizations and four workflows (a global one with an event
subscription, a global one with an enabled endpoint, a global one with
neither, and a customer one with an event subscription), then upgrades and
checks the identities it creates, the provider organization identity's
Platform Admin assignment, which workflows run unattended as that identity,
that head leaves it all in place, and that the downgrade removes exactly what
the migration made.

Ids and expectations are frozen here, like the migration's own SQL.
"""

from __future__ import annotations

import asyncio
import importlib.util
from pathlib import Path
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

PREVIOUS_REVISION = "20261003_r3_operator_secrets"
REVISION = "20261003_r3b_identities"

PLATFORM_ADMIN_ROLE_ID = UUID("00000000-0000-0000-0000-000000000005")
USER_ROLE_ID = UUID("00000000-0000-0000-0000-000000000006")
PROVIDER_ORG_ID = UUID("00000000-0000-0000-0000-000000000002")
ACTOR = "migration-rehearsal"
ASSIGNED_BY = "migration:20261003_r3b_identities"
DOMAIN = "identities.bifrost.internal"

MIGRATION = Path("/app/alembic/versions/20261003_r3b_identities.py")

_WORKFLOWS = ("g_sched", "g_endpoint", "g_plain", "a_sched")


def _downgrade(database_url: str, revision: str) -> None:
    with _temporary_migration_database_url(database_url):
        command.downgrade(_alembic_config(), revision)


async def _seed(database_url: str, ids: dict[str, str]) -> None:
    async def seed(connection: AsyncConnection) -> None:
        for key, name in (("org_a", "Rehearsal A"), ("org_b", "Rehearsal B")):
            await connection.execute(
                sa.text(
                    "INSERT INTO organizations (id, name, is_active, is_provider, settings, created_by) "
                    "VALUES (CAST(:id AS uuid), :name, TRUE, FALSE, '{}'::jsonb, :actor)"
                ),
                {"id": ids[key], "name": name, "actor": ACTOR},
            )
        for key, organization, endpoint in (
            ("g_sched", None, False),
            ("g_endpoint", None, True),
            ("g_plain", None, False),
            ("a_sched", ids["org_a"], False),
        ):
            await connection.execute(
                sa.text(
                    "INSERT INTO workflows (id, name, function_name, path, organization_id, endpoint_enabled) "
                    "VALUES (CAST(:id AS uuid), :name, :name, :path, CAST(:org AS uuid), :endpoint)"
                ),
                {"id": ids[key], "name": key, "path": f"workflows/{key}.py", "org": organization, "endpoint": endpoint},
            )
        await connection.execute(
            sa.text(
                "INSERT INTO event_sources (id, name, source_type, is_active, created_by) "
                "VALUES (CAST(:id AS uuid), 'rehearsal schedule', 'schedule', TRUE, :actor)"
            ),
            {"id": ids["source"], "actor": ACTOR},
        )
        for key in ("g_sched", "a_sched"):
            await connection.execute(
                sa.text(
                    "INSERT INTO event_subscriptions (id, event_source_id, workflow_id, created_by) "
                    "VALUES (gen_random_uuid(), CAST(:source AS uuid), CAST(:workflow AS uuid), :actor)"
                ),
                {"source": ids["source"], "workflow": ids[key], "actor": ACTOR},
            )

    await _run_in_database(database_url, seed)


async def _rerun_data_step(database_url: str) -> None:
    spec = importlib.util.spec_from_file_location("r3b_identities_migration", MIGRATION)
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)

    async def rerun(connection: AsyncConnection) -> None:
        await connection.run_sync(migration.create_identities)

    await _run_in_database(database_url, rerun)


async def _state(database_url: str) -> dict:
    async def read(connection: AsyncConnection) -> dict:
        constraint = (
            await connection.execute(
                sa.text(
                    "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
                    "WHERE conname = 'ck_users_org_requires_superuser'"
                )
            )
        ).scalar_one()
        columns = {
            (table, column)
            for table, column in (
                await connection.execute(
                    sa.text(
                        "SELECT table_name, column_name FROM information_schema.columns "
                        "WHERE (table_name, column_name) IN "
                        "(('users', 'identity_kind'), ('workflows', 'run_identity_id'))"
                    )
                )
            ).all()
        }
        if not columns:
            identities: set = set()
            run_identities: dict = {}
        else:
            identities = {
                tuple(str(value) if isinstance(value, UUID) else value for value in row)
                for row in (
                    await connection.execute(
                        sa.text(
                            "SELECT id, email, name, organization_id, identity_kind, is_active, "
                            "is_superuser, is_verified, is_registered, is_system, is_external, "
                            "hashed_password, base_role_id FROM users WHERE identity_kind IS NOT NULL"
                        )
                    )
                ).all()
            }
            run_identities = {
                str(workflow_id): str(identity_id) if identity_id else None
                for workflow_id, identity_id in (
                    await connection.execute(sa.text("SELECT id, run_identity_id FROM workflows"))
                ).all()
            }
        admin_assignments = {
            (str(user_id), assigned_by)
            for user_id, assigned_by in (
                await connection.execute(
                    sa.text("SELECT user_id, assigned_by FROM user_roles WHERE role_id = CAST(:role AS uuid)"),
                    {"role": str(PLATFORM_ADMIN_ROLE_ID)},
                )
            ).all()
        }
        admin_boundaries = {
            (str(user_id), kind, str(organization_id) if organization_id else None)
            for user_id, kind, organization_id in (
                await connection.execute(
                    sa.text(
                        "SELECT user_id, kind, organization_id FROM user_role_boundaries "
                        "WHERE role_id = CAST(:role AS uuid)"
                    ),
                    {"role": str(PLATFORM_ADMIN_ROLE_ID)},
                )
            ).all()
        }
        return {
            "constraint": constraint,
            "columns": columns,
            "identities": identities,
            "run_identities": run_identities,
            "admin_assignments": admin_assignments,
            "admin_boundaries": admin_boundaries,
        }

    return await _run_in_database(database_url, read)


def test_identities_are_created_and_unattended_global_workflows_point_at_the_provider_identity() -> None:
    database_name = f"bifrost_r2b_rehearsal_{uuid4().hex[:12]}"
    _assert_safe_database_name(database_name)
    database_url = _direct_database_url(database_name)
    ids = {key: str(uuid4()) for key in (*_WORKFLOWS, "org_a", "org_b", "source")}
    provider = str(PROVIDER_ORG_ID)

    try:
        asyncio.run(_create_database(database_name))
        _upgrade(database_url, PREVIOUS_REVISION)
        asyncio.run(_seed(database_url, ids))
        before = asyncio.run(_state(database_url))
        assert before["constraint"] == "CHECK (((organization_id IS NOT NULL) OR (is_superuser = true)))"
        assert before["columns"] == set()

        _upgrade(database_url, REVISION)
        after = asyncio.run(_state(database_url))
        assert after["constraint"] == (
            "CHECK (((organization_id IS NOT NULL) OR (is_superuser = true) "
            "OR ((identity_kind)::text = 'global_default'::text)))"
        )

        by_org = {row[3]: row for row in after["identities"]}
        assert len(after["identities"]) == 4
        assert set(by_org) == {provider, ids["org_a"], ids["org_b"], None}
        names = {provider: None, ids["org_a"]: "Rehearsal A identity", ids["org_b"]: "Rehearsal B identity"}
        for organization_id in (provider, ids["org_a"], ids["org_b"]):
            row = by_org[organization_id]
            assert row[1] == f"identity-{organization_id}@{DOMAIN}"
            assert row[4] == "org_default"
            if names[organization_id]:
                assert row[2] == names[organization_id]
        provider_identity = by_org[provider][0]
        global_row = by_org[None]
        assert global_row[1:5] == (f"identity-global@{DOMAIN}", "Global identity", None, "global_default")
        for row in after["identities"]:
            # active, verified, registered, not system/external, no password, base role User
            assert row[5:] == (
                True,
                row[0] == provider_identity,
                True,
                True,
                False,
                False,
                None,
                str(USER_ROLE_ID),
            )

        assert after["admin_assignments"] - before["admin_assignments"] == {(provider_identity, ASSIGNED_BY)}
        assert after["admin_boundaries"] - before["admin_boundaries"] == {(provider_identity, "platform", None)}
        assert after["run_identities"] == {
            ids["g_sched"]: provider_identity,
            ids["g_endpoint"]: provider_identity,
            ids["g_plain"]: None,
            ids["a_sched"]: None,
        }

        _upgrade(database_url, "head")
        assert asyncio.run(_state(database_url)) == after
        asyncio.run(_rerun_data_step(database_url))
        assert asyncio.run(_state(database_url)) == after

        _downgrade(database_url, PREVIOUS_REVISION)
        assert asyncio.run(_state(database_url)) == before
    finally:
        asyncio.run(_drop_database(database_name))
