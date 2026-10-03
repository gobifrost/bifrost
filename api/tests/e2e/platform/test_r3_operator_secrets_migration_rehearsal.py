"""True pre-head rehearsal for ``alembic/versions/20261003_r3_operator_secrets.py``.

Builds a disposable database at the migration's ``down_revision``, seeds
Platform Admins (provider-organization, Global and a system account),
provider-organization users who are active, inactive, a system account,
external, and one who already holds Platform Operator at a single customer
organization, and a customer-organization user. It upgrades and checks
Platform Operator's new permission and description, who holds Platform
Operator and Secrets Reader and at which boundaries, that head leaves it all
in place, and that the downgrade removes only what the migration made.

Ids and expectations are frozen here, like the migration's own SQL.
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

PREVIOUS_REVISION = "20261002_r3a_admin_additional"
REVISION = "20261003_r3_operator_secrets"

PLATFORM_ADMIN_ROLE_ID = UUID("00000000-0000-0000-0000-000000000005")
USER_ROLE_ID = UUID("00000000-0000-0000-0000-000000000006")
OPERATOR_ROLE_ID = UUID("00000000-0000-0000-0000-000000000007")
DECRYPTION_ROLE_ID = UUID("00000000-0000-0000-0000-000000000008")
PROVIDER_ORG_ID = UUID("00000000-0000-0000-0000-000000000002")
ACTOR = "migration-rehearsal"
ASSIGNED_BY = "migration:20261003_r3_operator_secrets"
EARLIER_ASSIGNER = "earlier-admin@rehearsal.test"
LATER_ASSIGNER = "later-admin@rehearsal.test"

OPERATOR_DESCRIPTION_BEFORE = (
    "Support for customer organizations: view organizations and users, invite users, "
    "reset MFA, deactivate ordinary users, and assign roles that carry no permissions. "
    "Additional role only."
)
OPERATOR_DESCRIPTION_AFTER = (
    "Support for customer organizations: view organizations and users, invite users, "
    "reset MFA, deactivate ordinary users, assign roles that carry no permissions, "
    "and run workflows in customer organizations. Additional role only."
)
DECRYPTION_DESCRIPTION_AFTER = (
    "Decrypts secret values through the SDK secret paths, for local "
    "development. Not included in the Platform Admin wildcard."
)

_USERS = (
    "admin",
    "global_admin",
    "system_admin",
    "staff",
    "inactive_staff",
    "earlier_operator",
    "system",
    "external",
    "customer",
)
_ADMINS = ("admin", "global_admin", "system_admin")


def _downgrade(database_url: str, revision: str) -> None:
    with _temporary_migration_database_url(database_url):
        command.downgrade(_alembic_config(), revision)


async def _assign(
    connection: AsyncConnection,
    user_id: str,
    role_id: UUID,
    assigned_by: str,
    kind: str,
    organization_id: str | None,
) -> None:
    await connection.execute(
        sa.text(
            "INSERT INTO user_roles (user_id, role_id, assigned_by) "
            "VALUES (CAST(:user_id AS uuid), CAST(:role_id AS uuid), :assigned_by)"
        ),
        {"user_id": user_id, "role_id": str(role_id), "assigned_by": assigned_by},
    )
    await connection.execute(
        sa.text(
            "INSERT INTO user_role_boundaries (id, user_id, role_id, kind, organization_id) "
            "VALUES (gen_random_uuid(), CAST(:user_id AS uuid), CAST(:role_id AS uuid), "
            "CAST(:kind AS varchar), CAST(:organization_id AS uuid))"
        ),
        {"user_id": user_id, "role_id": str(role_id), "kind": kind, "organization_id": organization_id},
    )


async def _seed(database_url: str, ids: dict[str, str]) -> None:
    async def seed(connection: AsyncConnection) -> None:
        await connection.execute(
            sa.text(
                "INSERT INTO organizations (id, name, is_active, is_provider, settings, created_by) "
                "VALUES (CAST(:customer_org AS uuid), 'Rehearsal Customer', TRUE, FALSE, '{}'::jsonb, :actor)"
            ),
            {**ids, "actor": ACTOR},
        )
        await connection.execute(
            sa.text(
                """
                INSERT INTO users (
                    id, email, name, is_active, is_superuser, is_verified, is_registered,
                    is_system, is_external, organization_id, base_role_id
                )
                VALUES
                    (CAST(:admin AS uuid), 'admin@rehearsal.test', 'Admin', TRUE, TRUE,
                     TRUE, TRUE, FALSE, FALSE, CAST(:provider AS uuid), CAST(:user_role AS uuid)),
                    (CAST(:global_admin AS uuid), 'global@rehearsal.test', 'Global', TRUE, TRUE,
                     TRUE, TRUE, FALSE, FALSE, NULL, CAST(:user_role AS uuid)),
                    (CAST(:system_admin AS uuid), 'system-admin@rehearsal.test', 'System Admin', TRUE, TRUE,
                     TRUE, TRUE, TRUE, FALSE, CAST(:provider AS uuid), CAST(:user_role AS uuid)),
                    (CAST(:staff AS uuid), 'staff@rehearsal.test', 'Staff', TRUE, FALSE,
                     TRUE, TRUE, FALSE, FALSE, CAST(:provider AS uuid), CAST(:user_role AS uuid)),
                    (CAST(:inactive_staff AS uuid), 'inactive@rehearsal.test', 'Inactive', FALSE, FALSE,
                     TRUE, TRUE, FALSE, FALSE, CAST(:provider AS uuid), CAST(:user_role AS uuid)),
                    (CAST(:earlier_operator AS uuid), 'earlier@rehearsal.test', 'Earlier', TRUE, FALSE,
                     TRUE, TRUE, FALSE, FALSE, CAST(:provider AS uuid), CAST(:user_role AS uuid)),
                    (CAST(:system AS uuid), 'system@rehearsal.test', 'System', TRUE, FALSE,
                     TRUE, TRUE, TRUE, FALSE, CAST(:provider AS uuid), CAST(:user_role AS uuid)),
                    (CAST(:external AS uuid), 'external@rehearsal.test', 'External', TRUE, FALSE,
                     TRUE, TRUE, FALSE, TRUE, CAST(:provider AS uuid), CAST(:user_role AS uuid)),
                    (CAST(:customer AS uuid), 'customer@rehearsal.test', 'Customer', TRUE, FALSE,
                     TRUE, TRUE, FALSE, FALSE, CAST(:customer_org AS uuid), CAST(:user_role AS uuid))
                """
            ),
            {**ids, "provider": str(PROVIDER_ORG_ID), "user_role": str(USER_ROLE_ID)},
        )
        for admin in _ADMINS:
            await _assign(connection, ids[admin], PLATFORM_ADMIN_ROLE_ID, ACTOR, "platform", None)
        await _assign(
            connection,
            ids["earlier_operator"],
            OPERATOR_ROLE_ID,
            EARLIER_ASSIGNER,
            "organization",
            ids["customer_org"],
        )

    await _run_in_database(database_url, seed)


async def _state(database_url: str, role_id: UUID) -> dict:
    async def read(connection: AsyncConnection) -> dict:
        role = (
            await connection.execute(
                sa.text(
                    "SELECT name, description, is_base, is_builtin FROM roles "
                    "WHERE id = CAST(:role_id AS uuid)"
                ),
                {"role_id": str(role_id)},
            )
        ).first()
        permissions = {
            permission
            for (permission,) in (
                await connection.execute(
                    sa.text("SELECT permission FROM role_permissions WHERE role_id = CAST(:role_id AS uuid)"),
                    {"role_id": str(role_id)},
                )
            ).all()
        }
        assignments = {
            (str(user_id), assigned_by)
            for user_id, assigned_by in (
                await connection.execute(
                    sa.text("SELECT user_id, assigned_by FROM user_roles WHERE role_id = CAST(:role_id AS uuid)"),
                    {"role_id": str(role_id)},
                )
            ).all()
        }
        boundaries = {
            (str(user_id), kind, str(organization_id) if organization_id else None)
            for user_id, kind, organization_id in (
                await connection.execute(
                    sa.text(
                        "SELECT user_id, kind, organization_id FROM user_role_boundaries "
                        "WHERE role_id = CAST(:role_id AS uuid)"
                    ),
                    {"role_id": str(role_id)},
                )
            ).all()
        }
        return {
            "role": tuple(role) if role else None,
            "permissions": permissions,
            "assignments": assignments,
            "boundaries": boundaries,
        }

    return await _run_in_database(database_url, read)


async def _assign_secrets_reader_later(database_url: str, user_id: str) -> None:
    """A Secrets Reader assignment made after the migration, by a person."""

    async def assign(connection: AsyncConnection) -> None:
        await _assign(connection, user_id, DECRYPTION_ROLE_ID, LATER_ASSIGNER, "platform", None)

    await _run_in_database(database_url, assign)


def test_operator_and_secrets_reader_go_to_exactly_the_right_people() -> None:
    database_name = f"bifrost_r2b_rehearsal_{uuid4().hex[:12]}"
    _assert_safe_database_name(database_name)
    database_url = _direct_database_url(database_name)
    ids = {key: str(uuid4()) for key in (*_USERS, "customer_org")}
    provider = str(PROVIDER_ORG_ID)

    try:
        asyncio.run(_create_database(database_name))
        _upgrade(database_url, PREVIOUS_REVISION)
        asyncio.run(_seed(database_url, ids))
        operator_before = asyncio.run(_state(database_url, OPERATOR_ROLE_ID))
        decryption_before = asyncio.run(_state(database_url, DECRYPTION_ROLE_ID))
        assert operator_before["role"][1] == OPERATOR_DESCRIPTION_BEFORE
        assert "workflows.execute" not in operator_before["permissions"]
        assert operator_before["assignments"] == {(ids["earlier_operator"], EARLIER_ASSIGNER)}
        assert decryption_before["assignments"] == set()

        _upgrade(database_url, REVISION)
        operator = asyncio.run(_state(database_url, OPERATOR_ROLE_ID))
        assert operator["role"] == ("Platform Operator", OPERATOR_DESCRIPTION_AFTER, False, True)
        assert operator["permissions"] == operator_before["permissions"] | {"workflows.execute"}
        assert not {"secrets.read", "*"} & operator["permissions"]
        # Platform Admins, system accounts, external users and customer users
        # are left out; inactive staff are included; an existing assignment
        # keeps its own boundaries.
        assert operator["assignments"] == {
            (ids["staff"], ASSIGNED_BY),
            (ids["inactive_staff"], ASSIGNED_BY),
            (ids["earlier_operator"], EARLIER_ASSIGNER),
        }
        assert operator["boundaries"] == {
            (ids["staff"], "managed_organizations", None),
            (ids["inactive_staff"], "managed_organizations", None),
            (ids["earlier_operator"], "organization", ids["customer_org"]),
        }

        decryption = asyncio.run(_state(database_url, DECRYPTION_ROLE_ID))
        assert decryption["role"] == ("Secrets Reader", DECRYPTION_DESCRIPTION_AFTER, False, True)
        assert decryption["permissions"] == {"secrets.read"}
        # Every Platform Admin apart from the system account.
        assert decryption["assignments"] == {
            (ids["admin"], ASSIGNED_BY),
            (ids["global_admin"], ASSIGNED_BY),
        }
        assert decryption["boundaries"] == {
            (user_id, kind, organization_id)
            for user_id in (ids["admin"], ids["global_admin"])
            for kind, organization_id in (
                ("platform", None),
                ("managed_organizations", None),
                ("organization", provider),
            )
        }

        _upgrade(database_url, "head")
        assert asyncio.run(_state(database_url, OPERATOR_ROLE_ID)) == operator
        assert asyncio.run(_state(database_url, DECRYPTION_ROLE_ID)) == decryption

        asyncio.run(_assign_secrets_reader_later(database_url, ids["staff"]))
        _downgrade(database_url, PREVIOUS_REVISION)
        assert asyncio.run(_state(database_url, OPERATOR_ROLE_ID)) == operator_before
        after_downgrade = asyncio.run(_state(database_url, DECRYPTION_ROLE_ID))
        assert after_downgrade["role"] == decryption_before["role"]
        assert after_downgrade["permissions"] == decryption_before["permissions"]
        assert after_downgrade["assignments"] == {(ids["staff"], LATER_ASSIGNER)}
        assert after_downgrade["boundaries"] == {(ids["staff"], "platform", None)}
    finally:
        asyncio.run(_drop_database(database_name))
