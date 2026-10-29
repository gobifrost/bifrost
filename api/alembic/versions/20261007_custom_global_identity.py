"""Custom identities may have no organization; defaults named Default Identity; names unique per organization

Revision ID: 20261007_custom_global_identity
Revises: 20261008_webhook_raw_body
Create Date: 2026-10-07

A custom identity can belong to Global (no organization) as well as to an
organization, so ck_users_org_requires_superuser admits any identity without
an organization, not only the one global default.

Every default identity (org_default, global_default) is named "Default
Identity", an edited name too: defaults have no editable name, and the UI
shows their organization. The downgrade restores the names generated at
creation ("{organization} identity", "Global identity").

Identity names are unique per organization, ignoring case, with Global
counted as one organization (uq_users_identity_name_per_org). Created after
the rename: each organization has one default, so no names collide.

Recommended Access reads a workflow's recorded access checks
(details->>'workflow_id' over the hot window) through
ix_audit_logs_access_check_workflow, created concurrently: audit_logs is the
largest table and is written on every request.
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op

revision: str = "20261007_custom_global_identity"
down_revision: Union[str, None] = "20261008_webhook_raw_body"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_RESTORE_ORG = (
    "UPDATE users u SET name = o.name || ' identity' FROM organizations o "
    "WHERE u.organization_id = o.id AND u.identity_kind = 'org_default'"
)
_RESTORE_GLOBAL = "UPDATE users SET name = 'Global identity' WHERE identity_kind = 'global_default'"
_NAME_INDEX = (
    "CREATE UNIQUE INDEX uq_users_identity_name_per_org ON users "
    "(COALESCE(organization_id, '00000000-0000-0000-0000-000000000000'::uuid), lower(name)) "
    "WHERE identity_kind IS NOT NULL"
)


def upgrade() -> None:
    op.execute("UPDATE users SET name = 'Default Identity' WHERE identity_kind IN ('org_default', 'global_default')")
    op.drop_constraint("ck_users_org_requires_superuser", "users", type_="check")
    op.create_check_constraint(
        "ck_users_org_requires_superuser",
        "users",
        "organization_id IS NOT NULL OR is_superuser = true OR identity_kind IS NOT NULL",
    )
    op.execute(_NAME_INDEX)
    with op.get_context().autocommit_block():
        op.execute(
            "CREATE INDEX CONCURRENTLY ix_audit_logs_access_check_workflow "
            "ON audit_logs ((details ->> 'workflow_id'), created_at) WHERE action = 'access.check'"
        )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix_audit_logs_access_check_workflow")
    op.drop_index("uq_users_identity_name_per_org", table_name="users")
    op.execute(_RESTORE_ORG)
    op.execute(_RESTORE_GLOBAL)
    op.execute(
        "UPDATE workflows SET run_identity_id = NULL WHERE run_identity_id IN "
        "(SELECT id FROM users WHERE identity_kind = 'custom' AND organization_id IS NULL)"
    )
    op.execute("DELETE FROM users WHERE identity_kind = 'custom' AND organization_id IS NULL")
    op.drop_constraint("ck_users_org_requires_superuser", "users", type_="check")
    op.create_check_constraint(
        "ck_users_org_requires_superuser",
        "users",
        "organization_id IS NOT NULL OR is_superuser = true OR identity_kind = 'global_default'",
    )
