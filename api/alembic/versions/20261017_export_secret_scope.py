"""Keep backup secret classification available before archive access.

Revision ID: 20261017_export_secret_scope
Revises: 20261017_wf_capabilities
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261017_export_secret_scope"
down_revision: str | None = "20261017_wf_capabilities"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Historical options are encrypted. Do not decrypt them during migration or
    # infer that a historical archive excludes secret values.
    op.add_column("solution_export_jobs", sa.Column(
        "contains_secrets", sa.Boolean(), nullable=False, server_default=sa.true(),
    ))


def downgrade() -> None:
    op.drop_column("solution_export_jobs", "contains_secrets")
