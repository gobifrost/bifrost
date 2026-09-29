"""True pre-head rehearsal for the R2b roles migration
(``alembic/versions/20260929_r2b_roles.py``).

This test intentionally does not use the normal cloned ``bifrost_test``
database. It creates a disposable PostgreSQL database, migrates it only to
the legacy cut-point (the migration's own ``down_revision``), seeds
representative legacy rows, then upgrades to head and verifies the durable
data outcome — the actual backfill behavior, not just the already-migrated
test-stack's end state.

Ported from the #620 reference branch's
``api/tests/e2e/platform/test_rbac_migration_rehearsal.py`` scaffolding
(disposable DB, direct-to-postgres admin connection, temporary settings
override); the assertions are R2b's own (base roles + role_permissions +
user_role_boundaries, not #620's role_assignments/capabilities model).
"""

from __future__ import annotations

import asyncio
import os
import re
from collections.abc import Awaitable, Callable
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncConnection, create_async_engine

from shared.builtin_roles import (
    PLATFORM_ADMIN_ROLE_ID,
    PLATFORM_OPERATOR_PERMISSIONS,
    PLATFORM_OPERATOR_ROLE_ID,
    USER_BASE_PERMISSIONS,
    USER_ROLE_ID,
)
from src.config import get_settings

pytestmark = pytest.mark.e2e

# The R2b migration's own down_revision — the legacy cut-point to seed at.
LEGACY_REVISION = "20260928_audit_op_surface"
DATABASE_NAME_RE = re.compile(r"^bifrost_r2b_rehearsal_[0-9a-f]{12}$")


def _direct_database_url(database_name: str) -> str:
    """Return a direct PostgreSQL async URL for the requested database,
    bypassing pgbouncer (CREATE/DROP DATABASE need a direct connection)."""
    url = make_url(os.environ["BIFROST_DATABASE_URL"])
    return (
        url.set(
            drivername="postgresql+asyncpg",
            host="postgres",
            port=5432,
            database=database_name,
        ).render_as_string(hide_password=False)
    )


def _assert_safe_database_name(database_name: str) -> None:
    if not DATABASE_NAME_RE.fullmatch(database_name):
        raise AssertionError(f"unsafe disposable database name: {database_name!r}")


@contextmanager
def _temporary_migration_database_url(database_url: str):
    """Point Alembic settings at the disposable DB, then restore globals."""
    previous_async = os.environ.get("BIFROST_DATABASE_URL")
    previous_sync = os.environ.get("BIFROST_DATABASE_URL_SYNC")
    sync_url = make_url(database_url).set(drivername="postgresql").render_as_string(
        hide_password=False
    )
    os.environ["BIFROST_DATABASE_URL"] = database_url
    os.environ["BIFROST_DATABASE_URL_SYNC"] = sync_url
    get_settings.cache_clear()
    try:
        yield
    finally:
        if previous_async is None:
            os.environ.pop("BIFROST_DATABASE_URL", None)
        else:
            os.environ["BIFROST_DATABASE_URL"] = previous_async
        if previous_sync is None:
            os.environ.pop("BIFROST_DATABASE_URL_SYNC", None)
        else:
            os.environ["BIFROST_DATABASE_URL_SYNC"] = previous_sync
        get_settings.cache_clear()


def _alembic_config() -> Config:
    config = Config(str(Path("/app/alembic.ini")))
    config.set_main_option("script_location", "/app/alembic")
    return config


def _upgrade(database_url: str, revision: str) -> None:
    with _temporary_migration_database_url(database_url):
        command.upgrade(_alembic_config(), revision)


def _current_revision(database_url: str) -> str | None:
    async def get_revision() -> str | None:
        engine = create_async_engine(database_url)
        try:
            async with engine.connect() as connection:
                def _read(sync_connection: Any) -> str | None:
                    from alembic.runtime.migration import MigrationContext

                    context = MigrationContext.configure(sync_connection)
                    return context.get_current_revision()

                return await connection.run_sync(_read)
        finally:
            await engine.dispose()

    return asyncio.run(get_revision())


async def _with_admin_connection(
    operation: Callable[[AsyncConnection], Awaitable[Any]],
) -> Any:
    engine = create_async_engine(
        _direct_database_url("postgres"),
        isolation_level="AUTOCOMMIT",
    )
    try:
        async with engine.connect() as connection:
            return await operation(connection)
    finally:
        await engine.dispose()


async def _create_database(database_name: str) -> None:
    _assert_safe_database_name(database_name)

    async def create(connection: AsyncConnection) -> None:
        await connection.execute(sa.text(f"CREATE DATABASE {database_name}"))

    await _with_admin_connection(create)


async def _drop_database(database_name: str) -> None:
    _assert_safe_database_name(database_name)

    async def drop(connection: AsyncConnection) -> None:
        await connection.execute(
            sa.text(
                """
                SELECT pg_terminate_backend(pid)
                FROM pg_stat_activity
                WHERE datname = :database_name
                  AND pid <> pg_backend_pid()
                """
            ),
            {"database_name": database_name},
        )
        await connection.execute(sa.text(f"DROP DATABASE IF EXISTS {database_name}"))

    await _with_admin_connection(drop)


async def _run_in_database(
    database_url: str,
    operation: Callable[[AsyncConnection], Awaitable[Any]],
) -> Any:
    engine = create_async_engine(database_url)
    try:
        async with engine.begin() as connection:
            return await operation(connection)
    finally:
        await engine.dispose()


ACTOR = "migration-rehearsal"


async def _seed_legacy_rows(database_url: str, ids: dict[str, str]) -> None:
    """Seed a representative legacy shape at LEGACY_REVISION:

    - a superuser (in the provider org)
    - a provider-org non-admin
    - a regular (customer-org) user
    - an external (customer-org) user
    - the system user (is_system, global, superuser — matches prod shape)
    - a user with no home org (superuser, since the org-requires-superuser
      check constraint forbids a non-superuser with organization_id NULL)
    - 3 custom roles with permissions={"can_promote_agent": false}, one
      with permissions={}
    - user_roles rows for several users, including the no-org user
    - knowledge_namespace_roles left empty
    """

    async def seed(connection: AsyncConnection) -> None:
        await connection.execute(
            sa.text(
                """
                INSERT INTO organizations (id, name, domain, is_active, is_provider, settings, created_by)
                VALUES
                    (CAST(:provider_org AS uuid), 'Provider MSP', 'provider.example', TRUE, TRUE, '{}'::jsonb, :actor),
                    (CAST(:customer_org AS uuid), 'Customer Org', 'customer.example', TRUE, FALSE, '{}'::jsonb, :actor)
                """
            ),
            {
                "provider_org": ids["provider_org"],
                "customer_org": ids["customer_org"],
                "actor": ACTOR,
            },
        )
        await connection.execute(
            sa.text(
                """
                INSERT INTO users (
                    id, email, name, hashed_password, is_active, is_superuser,
                    is_verified, is_registered, is_system, is_external, organization_id
                )
                VALUES
                    (CAST(:superuser AS uuid), 'superuser@example.test', 'Superuser', NULL, TRUE, TRUE, TRUE, TRUE, FALSE, FALSE, CAST(:provider_org AS uuid)),
                    (CAST(:provider_non_admin AS uuid), 'provider-non-admin@example.test', 'Provider Non-Admin', NULL, TRUE, FALSE, TRUE, TRUE, FALSE, FALSE, CAST(:provider_org AS uuid)),
                    (CAST(:regular_user AS uuid), 'regular-user@example.test', 'Regular User', NULL, TRUE, FALSE, TRUE, TRUE, FALSE, FALSE, CAST(:customer_org AS uuid)),
                    (CAST(:external_user AS uuid), 'external-user@example.test', 'External User', NULL, TRUE, FALSE, TRUE, TRUE, FALSE, TRUE, CAST(:customer_org AS uuid)),
                    (CAST(:system_user AS uuid), 'system-user@example.test', 'System User', NULL, TRUE, TRUE, TRUE, TRUE, TRUE, FALSE, NULL),
                    (CAST(:no_org_user AS uuid), 'no-org-user@example.test', 'No-Org User', NULL, TRUE, TRUE, TRUE, TRUE, FALSE, FALSE, NULL)
                """
            ),
            ids,
        )
        await connection.execute(
            sa.text(
                """
                INSERT INTO roles (id, name, description, permissions, created_by)
                VALUES
                    (CAST(:custom_role_1 AS uuid), 'Legacy Custom 1', 'seeded before R2b cutover', CAST(:perm_false AS jsonb), :actor),
                    (CAST(:custom_role_2 AS uuid), 'Legacy Custom 2', 'seeded before R2b cutover', CAST(:perm_false AS jsonb), :actor),
                    (CAST(:custom_role_3 AS uuid), 'Legacy Custom 3', 'seeded before R2b cutover', CAST(:perm_false AS jsonb), :actor),
                    (CAST(:custom_role_4 AS uuid), 'Legacy Custom 4 (empty perms)', 'seeded before R2b cutover', CAST(:perm_empty AS jsonb), :actor)
                """
            ),
            {
                **ids,
                "perm_false": '{"can_promote_agent": false}',
                "perm_empty": "{}",
                "actor": ACTOR,
            },
        )
        await connection.execute(
            sa.text(
                """
                INSERT INTO user_roles (user_id, role_id, assigned_by)
                VALUES
                    (CAST(:provider_non_admin AS uuid), CAST(:custom_role_1 AS uuid), :actor),
                    (CAST(:regular_user AS uuid), CAST(:custom_role_1 AS uuid), :actor),
                    (CAST(:external_user AS uuid), CAST(:custom_role_2 AS uuid), :actor),
                    (CAST(:no_org_user AS uuid), CAST(:custom_role_1 AS uuid), :actor)
                """
            ),
            {**ids, "actor": ACTOR},
        )
        # knowledge_namespace_roles is left empty — no insert.

    await _run_in_database(database_url, seed)


async def _snapshot_state(database_url: str, ids: dict[str, str]) -> dict[str, Any]:
    async def snapshot(connection: AsyncConnection) -> dict[str, Any]:
        users = {
            str(row.id): dict(row._mapping)
            for row in (
                await connection.execute(
                    sa.text(
                        """
                        SELECT id, email, is_superuser, base_role_id, organization_id
                        FROM users
                        WHERE id IN (
                            CAST(:superuser AS uuid), CAST(:provider_non_admin AS uuid),
                            CAST(:regular_user AS uuid), CAST(:external_user AS uuid),
                            CAST(:system_user AS uuid), CAST(:no_org_user AS uuid)
                        )
                        ORDER BY email
                        """
                    ),
                    ids,
                )
            )
        }
        roles = {
            str(row.id): dict(row._mapping)
            for row in (
                await connection.execute(
                    sa.text(
                        """
                        SELECT id, name, description, is_base, is_builtin
                        FROM roles
                        WHERE id IN (
                            CAST(:admin_role AS uuid), CAST(:user_role AS uuid), CAST(:operator_role AS uuid),
                            CAST(:custom_role_1 AS uuid), CAST(:custom_role_2 AS uuid),
                            CAST(:custom_role_3 AS uuid), CAST(:custom_role_4 AS uuid)
                        )
                        ORDER BY name
                        """
                    ),
                    {
                        **ids,
                        "admin_role": str(PLATFORM_ADMIN_ROLE_ID),
                        "user_role": str(USER_ROLE_ID),
                        "operator_role": str(PLATFORM_OPERATOR_ROLE_ID),
                    },
                )
            )
        }
        role_permissions = {
            role_id: sorted(
                row.permission
                for row in (
                    await connection.execute(
                        sa.text(
                            "SELECT permission FROM role_permissions WHERE role_id = CAST(:rid AS uuid)"
                        ),
                        {"rid": role_id},
                    )
                )
            )
            for role_id in (str(PLATFORM_ADMIN_ROLE_ID), str(USER_ROLE_ID), str(PLATFORM_OPERATOR_ROLE_ID))
        }
        boundaries = [
            dict(row._mapping)
            for row in (
                await connection.execute(
                    sa.text(
                        """
                        SELECT user_id, role_id, kind, organization_id
                        FROM user_role_boundaries
                        WHERE user_id IN (
                            CAST(:superuser AS uuid), CAST(:provider_non_admin AS uuid),
                            CAST(:regular_user AS uuid), CAST(:external_user AS uuid),
                            CAST(:system_user AS uuid), CAST(:no_org_user AS uuid)
                        )
                        ORDER BY user_id, role_id, kind, organization_id NULLS FIRST
                        """
                    ),
                    ids,
                )
            )
        ]
        roles_permissions_column_exists = await connection.scalar(
            sa.text(
                "SELECT EXISTS (SELECT 1 FROM information_schema.columns "
                "WHERE table_name = 'roles' AND column_name = 'permissions')"
            )
        )
        knowledge_namespace_roles_table_exists = await connection.scalar(
            sa.text(
                "SELECT EXISTS (SELECT 1 FROM information_schema.tables "
                "WHERE table_name = 'knowledge_namespace_roles')"
            )
        )
        return {
            "users": users,
            "roles": roles,
            "role_permissions": role_permissions,
            "boundaries": boundaries,
            "roles_permissions_column_exists": roles_permissions_column_exists,
            "knowledge_namespace_roles_table_exists": knowledge_namespace_roles_table_exists,
        }

    return await _run_in_database(database_url, snapshot)


def _boundaries_for(snapshot: dict[str, Any], *, user_id: str) -> list[dict[str, Any]]:
    return [b for b in snapshot["boundaries"] if str(b["user_id"]) == user_id]


def _assert_migrated_state(snapshot: dict[str, Any], ids: dict[str, str]) -> None:
    users = snapshot["users"]

    # base_role_id follows is_superuser, for every shape (superuser, system,
    # no-home-org, provider-org non-admin, regular, external).
    assert users[ids["superuser"]]["base_role_id"] == PLATFORM_ADMIN_ROLE_ID
    assert users[ids["system_user"]]["base_role_id"] == PLATFORM_ADMIN_ROLE_ID
    assert users[ids["no_org_user"]]["base_role_id"] == PLATFORM_ADMIN_ROLE_ID
    assert users[ids["provider_non_admin"]]["base_role_id"] == USER_ROLE_ID
    assert users[ids["regular_user"]]["base_role_id"] == USER_ROLE_ID
    assert users[ids["external_user"]]["base_role_id"] == USER_ROLE_ID

    # Builtins present with fixed ids + exact seeded permissions.
    roles = snapshot["roles"]
    admin_role = roles[str(PLATFORM_ADMIN_ROLE_ID)]
    assert admin_role["name"] == "Platform Admin"
    assert admin_role["is_base"] is True
    assert admin_role["is_builtin"] is True

    user_role = roles[str(USER_ROLE_ID)]
    assert user_role["name"] == "User"
    assert user_role["is_base"] is True
    assert user_role["is_builtin"] is True

    operator_role = roles[str(PLATFORM_OPERATOR_ROLE_ID)]
    assert operator_role["name"] == "Platform Operator"
    assert operator_role["is_base"] is False
    assert operator_role["is_builtin"] is True

    assert snapshot["role_permissions"][str(PLATFORM_ADMIN_ROLE_ID)] == []
    assert set(snapshot["role_permissions"][str(USER_ROLE_ID)]) == USER_BASE_PERMISSIONS
    assert set(snapshot["role_permissions"][str(PLATFORM_OPERATOR_ROLE_ID)]) == PLATFORM_OPERATOR_PERMISSIONS

    # Custom roles unchanged (identity + flags), permissions column gone.
    for key in ("custom_role_1", "custom_role_2", "custom_role_3", "custom_role_4"):
        custom = roles[ids[key]]
        assert custom["is_base"] is False
        assert custom["is_builtin"] is False
    assert snapshot["roles_permissions_column_exists"] is False
    assert snapshot["knowledge_namespace_roles_table_exists"] is False

    # Boundaries: one 'organization' boundary per user_roles row, at the
    # user's home org — except the no-org user, whose row gets none.
    assert _boundaries_for(snapshot, user_id=ids["provider_non_admin"]) == [
        {
            "user_id": UUID(ids["provider_non_admin"]),
            "role_id": UUID(ids["custom_role_1"]),
            "kind": "organization",
            "organization_id": UUID(ids["provider_org"]),
        }
    ]
    assert _boundaries_for(snapshot, user_id=ids["regular_user"]) == [
        {
            "user_id": UUID(ids["regular_user"]),
            "role_id": UUID(ids["custom_role_1"]),
            "kind": "organization",
            "organization_id": UUID(ids["customer_org"]),
        }
    ]
    assert _boundaries_for(snapshot, user_id=ids["external_user"]) == [
        {
            "user_id": UUID(ids["external_user"]),
            "role_id": UUID(ids["custom_role_2"]),
            "kind": "organization",
            "organization_id": UUID(ids["customer_org"]),
        }
    ]
    assert _boundaries_for(snapshot, user_id=ids["no_org_user"]) == []
    assert _boundaries_for(snapshot, user_id=ids["superuser"]) == []
    assert _boundaries_for(snapshot, user_id=ids["system_user"]) == []


@pytest.fixture
def rehearsal_ids() -> dict[str, str]:
    return {
        "provider_org": str(uuid4()),
        "customer_org": str(uuid4()),
        "superuser": str(uuid4()),
        "provider_non_admin": str(uuid4()),
        "regular_user": str(uuid4()),
        "external_user": str(uuid4()),
        "system_user": str(uuid4()),
        "no_org_user": str(uuid4()),
        "custom_role_1": str(uuid4()),
        "custom_role_2": str(uuid4()),
        "custom_role_3": str(uuid4()),
        "custom_role_4": str(uuid4()),
    }


def test_r2b_roles_migration_rehearses_legacy_upgrade_outside_template(
    rehearsal_ids: dict[str, str],
) -> None:
    database_name = f"bifrost_r2b_rehearsal_{uuid4().hex[:12]}"
    _assert_safe_database_name(database_name)
    database_url = _direct_database_url(database_name)
    ids = rehearsal_ids

    try:
        asyncio.run(_create_database(database_name))
        _upgrade(database_url, LEGACY_REVISION)
        asyncio.run(_seed_legacy_rows(database_url, ids))

        _upgrade(database_url, "head")
        migrated_snapshot = asyncio.run(_snapshot_state(database_url, ids))
        _assert_migrated_state(migrated_snapshot, ids)

        # The supported idempotence check is Alembic's no-op upgrade path at
        # head. Assert it does not create duplicate grants or alter data.
        _upgrade(database_url, "head")
        second_snapshot = asyncio.run(_snapshot_state(database_url, ids))
        assert second_snapshot == migrated_snapshot
    finally:
        asyncio.run(_drop_database(database_name))


def test_upgrade_refuses_a_role_with_can_promote_agent_true() -> None:
    """A role with `permissions->>'can_promote_agent' = true` would silently
    lose that grant when the column is dropped — the migration must refuse
    to run, leaving the DB at the previous revision."""
    database_name = f"bifrost_r2b_rehearsal_{uuid4().hex[:12]}"
    _assert_safe_database_name(database_name)
    database_url = _direct_database_url(database_name)
    role_id = str(uuid4())

    try:
        asyncio.run(_create_database(database_name))
        _upgrade(database_url, LEGACY_REVISION)

        async def seed(connection: AsyncConnection) -> None:
            await connection.execute(
                sa.text(
                    """
                    INSERT INTO roles (id, name, description, permissions, created_by)
                    VALUES (CAST(:role_id AS uuid), 'Promoter', 'grants can_promote_agent', CAST(:perms AS jsonb), :actor)
                    """
                ),
                {"role_id": role_id, "perms": '{"can_promote_agent": true}', "actor": ACTOR},
            )

        asyncio.run(_run_in_database(database_url, seed))

        with pytest.raises(Exception, match="can_promote_agent"):
            _upgrade(database_url, "head")

        assert _current_revision(database_url) == LEGACY_REVISION
    finally:
        asyncio.run(_drop_database(database_name))


def test_upgrade_refuses_non_empty_knowledge_namespace_roles() -> None:
    """A non-empty `knowledge_namespace_roles` table means the "decided
    unused, 0 rows in prod" assumption is wrong — the migration must refuse
    to drop it, leaving the DB at the previous revision."""
    database_name = f"bifrost_r2b_rehearsal_{uuid4().hex[:12]}"
    _assert_safe_database_name(database_name)
    database_url = _direct_database_url(database_name)
    role_id = str(uuid4())

    try:
        asyncio.run(_create_database(database_name))
        _upgrade(database_url, LEGACY_REVISION)

        async def seed(connection: AsyncConnection) -> None:
            await connection.execute(
                sa.text(
                    """
                    INSERT INTO roles (id, name, description, permissions, created_by)
                    VALUES (CAST(:role_id AS uuid), 'Has KB Grant', 'referenced by a namespace role', '{}'::jsonb, :actor)
                    """
                ),
                {"role_id": role_id, "actor": ACTOR},
            )
            await connection.execute(
                sa.text(
                    """
                    INSERT INTO knowledge_namespace_roles (id, namespace, organization_id, role_id, assigned_by)
                    VALUES (gen_random_uuid(), 'some-namespace', NULL, CAST(:role_id AS uuid), :actor)
                    """
                ),
                {"role_id": role_id, "actor": ACTOR},
            )

        asyncio.run(_run_in_database(database_url, seed))

        with pytest.raises(Exception, match="knowledge_namespace_roles"):
            _upgrade(database_url, "head")

        assert _current_revision(database_url) == LEGACY_REVISION
    finally:
        asyncio.run(_drop_database(database_name))
