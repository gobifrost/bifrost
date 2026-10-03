"""Create identities: a default per organization, a global one, and the provider organization's as Platform Admin

Revision ID: 20261003_r3b_identities
Revises: 20261003_r3_operator_secrets
Create Date: 2026-10-03

An identity is an ordinary user account that runs work no person started
(schedules, webhooks, events, endpoint keys). `users.identity_kind` marks it:
`org_default` (one per organization, including the provider organization),
`global_default` (one, with no organization) or `custom` (created later by
admins). Identities never sign in.

Data written:
- a default identity for every organization, base role User;
- the global identity (no organization), base role User — the users
  organization check is widened to allow it;
- the provider organization's identity holds Platform Admin at the platform
  boundary (and `is_superuser`, which mirrors that assignment);
- every global workflow that can run unattended (an event subscription or
  an enabled endpoint) runs unattended as the provider organization's
  identity (`workflows.run_identity_id`).

Nothing reads `run_identity_id` or the identities for execution yet.
Idempotent; downgrade removes exactly what this created.
Migrations never import live application code; the literals below are frozen.
tests/unit/test_builtin_roles.py asserts they equal the live constants.
"""
from __future__ import annotations

from typing import Sequence, Union
from uuid import UUID

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql
from sqlalchemy.engine import Connection

revision: str = "20261003_r3b_identities"
down_revision: Union[str, None] = "20261003_r3_operator_secrets"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

PROVIDER_ORG_ID = UUID("00000000-0000-0000-0000-000000000002")
PLATFORM_ADMIN_ROLE_ID = UUID("00000000-0000-0000-0000-000000000005")
USER_ROLE_ID = UUID("00000000-0000-0000-0000-000000000006")
ASSIGNED_BY = "migration:20261003_r3b_identities"
EMAIL_DOMAIN = "identities.bifrost.internal"
_PARAMS = {
    "provider": str(PROVIDER_ORG_ID),
    "admin": str(PLATFORM_ADMIN_ROLE_ID),
    "user_role": str(USER_ROLE_ID),
    "assigned_by": ASSIGNED_BY,
    "domain": EMAIL_DOMAIN,
}

_ORG_IDENTITIES = """
    INSERT INTO users (id, email, name, is_active, is_superuser, is_verified, is_registered,
                       is_system, is_external, organization_id, base_role_id, identity_kind)
    SELECT gen_random_uuid(), 'identity-' || o.id || '@' || :domain, o.name || ' identity',
           true, false, true, true, false, false, o.id, CAST(:user_role AS uuid), 'org_default'
    FROM organizations o
    WHERE NOT EXISTS (SELECT 1 FROM users u
                      WHERE u.organization_id = o.id AND u.identity_kind = 'org_default')
"""
_GLOBAL_IDENTITY = """
    INSERT INTO users (id, email, name, is_active, is_superuser, is_verified, is_registered,
                       is_system, is_external, organization_id, base_role_id, identity_kind)
    SELECT gen_random_uuid(), 'identity-global@' || :domain, 'Global identity',
           true, false, true, true, false, false, NULL, CAST(:user_role AS uuid), 'global_default'
    WHERE NOT EXISTS (SELECT 1 FROM users WHERE identity_kind = 'global_default')
"""
_PROVIDER_IDENTITY = (
    "(SELECT id FROM users WHERE organization_id = CAST(:provider AS uuid) "
    "AND identity_kind = 'org_default')"
)
_PROVIDER_ADMIN = f"""
    INSERT INTO user_roles (user_id, role_id, assigned_by)
    SELECT {_PROVIDER_IDENTITY}, CAST(:admin AS uuid), :assigned_by
    WHERE NOT EXISTS (SELECT 1 FROM user_roles WHERE user_id = {_PROVIDER_IDENTITY}
                      AND role_id = CAST(:admin AS uuid))
"""
_PROVIDER_ADMIN_BOUNDARY = f"""
    INSERT INTO user_role_boundaries (id, user_id, role_id, kind, organization_id)
    SELECT gen_random_uuid(), {_PROVIDER_IDENTITY}, CAST(:admin AS uuid), 'platform', NULL
    WHERE NOT EXISTS (SELECT 1 FROM user_role_boundaries WHERE user_id = {_PROVIDER_IDENTITY}
                      AND role_id = CAST(:admin AS uuid) AND kind = 'platform')
"""
_PROVIDER_SUPERUSER = f"UPDATE users SET is_superuser = true WHERE id = {_PROVIDER_IDENTITY}"
_TRIGGERED_GLOBALS = f"""
    UPDATE workflows w SET run_identity_id = {_PROVIDER_IDENTITY}
    WHERE w.organization_id IS NULL AND w.run_identity_id IS NULL
      AND (w.endpoint_enabled
           OR EXISTS (SELECT 1 FROM event_subscriptions s WHERE s.workflow_id = w.id))
"""


def create_identities(bind: Connection) -> None:
    """Data step; safe to run again."""
    for statement in (
        _ORG_IDENTITIES,
        _GLOBAL_IDENTITY,
        _PROVIDER_ADMIN,
        _PROVIDER_ADMIN_BOUNDARY,
        _PROVIDER_SUPERUSER,
        _TRIGGERED_GLOBALS,
    ):
        bind.execute(sa.text(statement), _PARAMS)


def upgrade() -> None:
    op.add_column("users", sa.Column("identity_kind", sa.String(16), nullable=True))
    op.create_check_constraint(
        "ck_users_identity_kind",
        "users",
        "identity_kind IS NULL OR identity_kind IN ('org_default', 'global_default', 'custom')",
    )
    op.create_index(
        "ux_users_org_default_identity",
        "users",
        ["organization_id"],
        unique=True,
        postgresql_where=sa.text("identity_kind = 'org_default'"),
    )
    op.create_index(
        "ux_users_global_identity",
        "users",
        [sa.text("(identity_kind)")],
        unique=True,
        postgresql_where=sa.text("identity_kind = 'global_default'"),
    )
    op.drop_constraint("ck_users_org_requires_superuser", "users", type_="check")
    op.create_check_constraint(
        "ck_users_org_requires_superuser",
        "users",
        "organization_id IS NOT NULL OR is_superuser = true OR identity_kind = 'global_default'",
    )
    op.add_column(
        "workflows",
        sa.Column(
            "run_identity_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=True,
        ),
    )
    op.create_index("ix_workflows_run_identity_id", "workflows", ["run_identity_id"])

    create_identities(op.get_bind())


def downgrade() -> None:
    op.drop_index("ix_workflows_run_identity_id", table_name="workflows")
    op.drop_column("workflows", "run_identity_id")
    # Their role assignments and boundaries cascade with them.
    op.execute("DELETE FROM users WHERE identity_kind IS NOT NULL")
    op.drop_constraint("ck_users_org_requires_superuser", "users", type_="check")
    op.create_check_constraint(
        "ck_users_org_requires_superuser",
        "users",
        "organization_id IS NOT NULL OR is_superuser = true",
    )
    op.drop_index("ux_users_global_identity", table_name="users")
    op.drop_index("ux_users_org_default_identity", table_name="users")
    op.drop_constraint("ck_users_identity_kind", "users", type_="check")
    op.drop_column("users", "identity_kind")
