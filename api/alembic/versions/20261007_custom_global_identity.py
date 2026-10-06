"""Custom identities may have no organization; default identity names in Title Case

Revision ID: 20261007_custom_global_identity
Revises: 20261006_run_retention
Create Date: 2026-10-07

A custom identity can belong to Global (no organization) as well as to an
organization, so ck_users_org_requires_superuser admits any identity without
an organization, not only the one global default.

Default identities whose name is still the one generated at creation
("{organization} identity", "Global identity") are renamed to Title Case;
a name someone edited is left alone.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20261007_custom_global_identity"
down_revision: Union[str, None] = "20261006_run_retention"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_RENAME_ORG = (
    "UPDATE users u SET name = o.name || :new FROM organizations o "
    "WHERE u.organization_id = o.id AND u.identity_kind = 'org_default' AND u.name = o.name || :old"
)
_RENAME_GLOBAL = "UPDATE users SET name = :new WHERE identity_kind = 'global_default' AND name = :old"


def _rename_defaults(old: str, new: str, old_global: str, new_global: str) -> None:
    op.get_bind().execute(sa.text(_RENAME_ORG), {"old": old, "new": new})
    op.get_bind().execute(sa.text(_RENAME_GLOBAL), {"old": old_global, "new": new_global})


def upgrade() -> None:
    _rename_defaults(" identity", " Identity", "Global identity", "Global Identity")
    op.drop_constraint("ck_users_org_requires_superuser", "users", type_="check")
    op.create_check_constraint(
        "ck_users_org_requires_superuser",
        "users",
        "organization_id IS NOT NULL OR is_superuser = true OR identity_kind IS NOT NULL",
    )


def downgrade() -> None:
    _rename_defaults(" Identity", " identity", "Global Identity", "Global identity")
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
