"""Solution setup_complete column.

Tracks whether a Solution's required configs are all set. Default true — a
solution with no unset required configs is immediately complete.

Revision ID: 20260614_solution_setup_complete
Revises: 20260613_solution_custom_claims
"""

import sqlalchemy as sa
from alembic import op

revision = "20260614_solution_setup_complete"
down_revision = "20260613_solution_custom_claims"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "solutions",
        sa.Column("setup_complete", sa.Boolean(), nullable=False, server_default=sa.text("true")),
    )


def downgrade() -> None:
    op.drop_column("solutions", "setup_complete")
