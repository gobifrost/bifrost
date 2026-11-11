"""Add setup notes to integrations.

Revision ID: 20261111_integration_notes
Revises: 20261111_config_option_labels
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261111_integration_notes"
down_revision: str | None = "20261111_config_option_labels"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("integrations", sa.Column("notes", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("integrations", "notes")
