"""True pre-head rehearsal for the RBAC boundary migration.

This test intentionally does not use the normal cloned ``bifrost_test`` database.
It creates a disposable PostgreSQL database, migrates it only to the legacy
cut-point, seeds representative legacy rows, then upgrades to head and verifies
the durable data outcome.
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

from shared.authorization_defaults_v1 import (
    BUILDER_ROLE_ID,
    ORGANIZATION_MEMBER_ROLE_ID,
    PLATFORM_ADMIN_ROLE_ID,
    PLATFORM_OPERATOR_ROLE_ID,
)
from src.config import get_settings


pytestmark = pytest.mark.e2e

LEGACY_REVISION = "20260819_skill_file_tool"
DATABASE_NAME_RE = re.compile(r"^bifrost_rbac_rehearsal_[0-9a-f]{12}$")


def _direct_database_url(database_name: str) -> str:
    """Return a direct PostgreSQL async URL for the requested database."""

    url = make_url(os.environ["BIFROST_DATABASE_URL"])
    return (
        url.set(
            drivername="postgresql+asyncpg",
            host="postgres",
            port=5432,
            database=database_name,
        )
        .render_as_string(hide_password=False)
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


async def _seed_legacy_rows(database_url: str, ids: dict[str, str]) -> None:
    async def seed(connection: AsyncConnection) -> None:
        await connection.execute(
            sa.text(
                """
                INSERT INTO organizations (id, name, domain, is_provider, created_by)
                VALUES
                    (CAST(:provider_org AS uuid), 'Provider MSP', 'provider.example', TRUE, :actor),
                    (CAST(:customer_org AS uuid), 'Customer Org', 'customer.example', FALSE, :actor)
                """
            ),
            {
                "provider_org": ids["provider_org"],
                "customer_org": ids["customer_org"],
                "actor": "migration-rehearsal",
            },
        )
        await connection.execute(
            sa.text(
                """
                INSERT INTO users (
                    id, email, name, hashed_password, is_active, is_superuser,
                    is_verified, is_registered, is_system, organization_id
                )
                VALUES
                    (CAST(:legacy_admin AS uuid), 'legacy-admin@example.test', 'Legacy Admin', NULL, TRUE, TRUE, TRUE, TRUE, FALSE, NULL),
                    (CAST(:system_admin AS uuid), 'system-admin@example.test', 'System Admin', NULL, TRUE, TRUE, TRUE, TRUE, TRUE, NULL),
                    (CAST(:provider_user AS uuid), 'provider-user@example.test', 'Provider User', NULL, TRUE, FALSE, TRUE, TRUE, FALSE, CAST(:provider_org AS uuid)),
                    (CAST(:customer_user AS uuid), 'customer-user@example.test', 'Customer User', NULL, TRUE, FALSE, TRUE, TRUE, FALSE, CAST(:customer_org AS uuid))
                """
            ),
            ids,
        )
        await connection.execute(
            sa.text(
                """
                INSERT INTO roles (
                    id, key, name, description, permissions, scopes,
                    is_builtin, assignable_to_resources, created_by
                )
                VALUES (
                    CAST(:custom_role AS uuid), 'legacy_custom_builder',
                    'Legacy Custom Builder', 'Legacy role seeded before RBAC cutover',
                    CAST(:permissions AS jsonb), CAST(:scopes AS jsonb),
                    FALSE, TRUE, :actor
                )
                """
            ),
            {
                "custom_role": ids["custom_role"],
                "permissions": '{"can_promote_agent": true, "custom_flag": "kept"}',
                "scopes": '["agents.write", "solutions.build"]',
                "actor": "migration-rehearsal",
            },
        )
        await connection.execute(
            sa.text(
                """
                INSERT INTO user_roles (user_id, role_id, assigned_by)
                VALUES (
                    CAST(:customer_user AS uuid),
                    CAST(:custom_role AS uuid),
                    :actor
                )
                """
            ),
            {
                "customer_user": ids["customer_user"],
                "custom_role": ids["custom_role"],
                "actor": "migration-rehearsal",
            },
        )

    await _run_in_database(database_url, seed)


async def _snapshot_rbac_state(database_url: str, ids: dict[str, str]) -> dict[str, Any]:
    async def snapshot(connection: AsyncConnection) -> dict[str, Any]:
        users = {
            str(row.id): dict(row._mapping)
            for row in (
                await connection.execute(
                    sa.text(
                        """
                        SELECT id, email, is_superuser, is_system, organization_id
                        FROM users
                        WHERE id IN (
                            CAST(:legacy_admin AS uuid),
                            CAST(:system_admin AS uuid),
                            CAST(:provider_user AS uuid),
                            CAST(:customer_user AS uuid)
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
                        SELECT id, key, permissions, capabilities
                        FROM roles
                        WHERE id IN (
                            CAST(:admin_role AS uuid),
                            CAST(:operator_role AS uuid),
                            CAST(:member_role AS uuid),
                            CAST(:builder_role AS uuid),
                            CAST(:custom_role AS uuid)
                        )
                        ORDER BY key
                        """
                    ),
                    {
                        **ids,
                        "admin_role": str(PLATFORM_ADMIN_ROLE_ID),
                        "operator_role": str(PLATFORM_OPERATOR_ROLE_ID),
                        "member_role": str(ORGANIZATION_MEMBER_ROLE_ID),
                        "builder_role": str(BUILDER_ROLE_ID),
                    },
                )
            )
        }
        assignments = [
            dict(row._mapping)
            for row in (
                await connection.execute(
                    sa.text(
                        """
                        SELECT
                            assignment.user_id,
                            assignment.role_id,
                            boundary.boundary_kind,
                            boundary.organization_id
                        FROM role_assignments AS assignment
                        JOIN role_assignment_boundaries AS boundary
                          ON boundary.role_assignment_id = assignment.id
                        WHERE assignment.user_id IN (
                            CAST(:legacy_admin AS uuid),
                            CAST(:system_admin AS uuid),
                            CAST(:provider_user AS uuid),
                            CAST(:customer_user AS uuid)
                        )
                        ORDER BY
                            assignment.user_id,
                            assignment.role_id,
                            boundary.boundary_kind,
                            boundary.organization_id NULLS FIRST
                        """
                    ),
                    ids,
                )
            )
        ]
        return {"users": users, "roles": roles, "assignments": assignments}

    return await _run_in_database(database_url, snapshot)


def _assignments_for(
    snapshot: dict[str, Any],
    *,
    user_id: str,
    role_id: UUID | str | None = None,
) -> list[dict[str, Any]]:
    role_id_text = str(role_id) if role_id is not None else None
    return [
        assignment
        for assignment in snapshot["assignments"]
        if str(assignment["user_id"]) == user_id
        and (role_id_text is None or str(assignment["role_id"]) == role_id_text)
    ]


def _assert_migrated_state(snapshot: dict[str, Any], ids: dict[str, str]) -> None:
    users = snapshot["users"]
    assert users[ids["legacy_admin"]]["is_superuser"] is True
    assert users[ids["system_admin"]]["is_superuser"] is True
    assert users[ids["provider_user"]]["is_superuser"] is False
    assert users[ids["customer_user"]]["is_superuser"] is False

    custom_role = snapshot["roles"][ids["custom_role"]]
    assert custom_role["permissions"] == {
        "can_promote_agent": True,
        "custom_flag": "kept",
    }
    assert set(custom_role["capabilities"]) == {
        "agents.readwrite",
        "builder.execute",
        "solutions.build.execute",
        "solutions.deploy.execute",
        "solutions.readwrite",
    }

    builder_capabilities = set(snapshot["roles"][str(BUILDER_ROLE_ID)]["capabilities"])
    assert "builder.execute" in builder_capabilities
    assert "organizations.read" not in builder_capabilities
    assert "roles.read" not in builder_capabilities

    operator_capabilities = set(
        snapshot["roles"][str(PLATFORM_OPERATOR_ROLE_ID)]["capabilities"]
    )
    assert "organizationgroups.readwrite" in operator_capabilities
    assert "builder.execute" not in operator_capabilities

    legacy_admin_grants = _assignments_for(
        snapshot,
        user_id=ids["legacy_admin"],
        role_id=PLATFORM_ADMIN_ROLE_ID,
    )
    assert legacy_admin_grants == [
        {
            "user_id": UUID(ids["legacy_admin"]),
            "role_id": PLATFORM_ADMIN_ROLE_ID,
            "boundary_kind": "platform",
            "organization_id": None,
        }
    ]
    assert not _assignments_for(
        snapshot,
        user_id=ids["system_admin"],
        role_id=PLATFORM_ADMIN_ROLE_ID,
    )

    provider_operator_grants = _assignments_for(
        snapshot,
        user_id=ids["provider_user"],
        role_id=PLATFORM_OPERATOR_ROLE_ID,
    )
    assert provider_operator_grants == [
        {
            "user_id": UUID(ids["provider_user"]),
            "role_id": PLATFORM_OPERATOR_ROLE_ID,
            "boundary_kind": "managed_organizations",
            "organization_id": None,
        }
    ]

    customer_member_grants = _assignments_for(
        snapshot,
        user_id=ids["customer_user"],
        role_id=ORGANIZATION_MEMBER_ROLE_ID,
    )
    assert customer_member_grants == [
        {
            "user_id": UUID(ids["customer_user"]),
            "role_id": ORGANIZATION_MEMBER_ROLE_ID,
            "boundary_kind": "organization",
            "organization_id": UUID(ids["customer_org"]),
        }
    ]
    custom_grants = _assignments_for(
        snapshot,
        user_id=ids["customer_user"],
        role_id=ids["custom_role"],
    )
    assert custom_grants == [
        {
            "user_id": UUID(ids["customer_user"]),
            "role_id": UUID(ids["custom_role"]),
            "boundary_kind": "organization",
            "organization_id": UUID(ids["customer_org"]),
        }
    ]


def test_rbac_boundary_migration_rehearses_legacy_upgrade_outside_template() -> None:
    database_name = f"bifrost_rbac_rehearsal_{uuid4().hex[:12]}"
    _assert_safe_database_name(database_name)
    database_url = _direct_database_url(database_name)
    ids = {
        "provider_org": str(uuid4()),
        "customer_org": str(uuid4()),
        "legacy_admin": str(uuid4()),
        "system_admin": str(uuid4()),
        "provider_user": str(uuid4()),
        "customer_user": str(uuid4()),
        "custom_role": str(uuid4()),
    }

    try:
        asyncio.run(_create_database(database_name))
        _upgrade(database_url, LEGACY_REVISION)
        asyncio.run(_seed_legacy_rows(database_url, ids))

        _upgrade(database_url, "head")
        migrated_snapshot = asyncio.run(_snapshot_rbac_state(database_url, ids))
        _assert_migrated_state(migrated_snapshot, ids)

        # The supported idempotence check is Alembic's no-op upgrade path at
        # head. We assert it does not create duplicate grants or alter data.
        _upgrade(database_url, "head")
        second_snapshot = asyncio.run(_snapshot_rbac_state(database_url, ids))
        assert second_snapshot == migrated_snapshot
    finally:
        asyncio.run(_drop_database(database_name))
