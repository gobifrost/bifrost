"""Batch identity provider grants into a periodic access pass.

Revision ID: 20261117_idp_access_pending
Revises: 20261116_idp_token_expiry_stage

Every role or activation change bumps the global authority epoch, which
retires every access token on the instance. Provisioning applies revocations
in the request but records grants here for a pass that applies them all in
one transaction.
"""

import sqlalchemy as sa
from alembic import op

revision = "20261117_idp_access_pending"
down_revision = "20261116_idp_token_expiry_stage"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "identity_provider_users",
        sa.Column("display_name", sa.String(255), nullable=True),
    )
    op.add_column(
        "identity_provider_users",
        sa.Column("access_pending_since", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_identity_provider_users_access_pending",
        "identity_provider_users",
        ["access_pending_since"],
        postgresql_where=sa.text("access_pending_since IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index(
        "ix_identity_provider_users_access_pending", table_name="identity_provider_users"
    )
    op.drop_column("identity_provider_users", "access_pending_since")
    op.drop_column("identity_provider_users", "display_name")
