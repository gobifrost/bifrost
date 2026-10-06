"""Custom identities may have no organization

Revision ID: 20261007_custom_global_identity
Revises: 20261006_run_retention
Create Date: 2026-10-07

A custom identity can belong to Global (no organization) as well as to an
organization, so ck_users_org_requires_superuser admits any identity without
an organization, not only the one global default.
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op

revision: str = "20261007_custom_global_identity"
down_revision: Union[str, None] = "20261006_run_retention"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_constraint("ck_users_org_requires_superuser", "users", type_="check")
    op.create_check_constraint(
        "ck_users_org_requires_superuser",
        "users",
        "organization_id IS NOT NULL OR is_superuser = true OR identity_kind IS NOT NULL",
    )


def downgrade() -> None:
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
