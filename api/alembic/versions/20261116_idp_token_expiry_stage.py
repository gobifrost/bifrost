"""Record which token-expiry warning stage was announced.

Revision ID: 20261116_idp_token_expiry_stage
Revises: 20261116_identity_providers
"""

import sqlalchemy as sa
from alembic import op

revision = "20261116_idp_token_expiry_stage"
down_revision = "20261116_identity_providers"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "identity_provider_tokens",
        sa.Column("expiry_warning_stage", sa.String(16), nullable=True),
    )
    op.create_check_constraint(
        "ck_identity_provider_tokens_expiry_warning_stage",
        "identity_provider_tokens",
        "expiry_warning_stage IN ('expiring', 'expired')",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_identity_provider_tokens_expiry_warning_stage",
        "identity_provider_tokens",
        type_="check",
    )
    op.drop_column("identity_provider_tokens", "expiry_warning_stage")
