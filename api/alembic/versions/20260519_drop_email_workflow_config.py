"""drop email_workflow_config system config row

Revision ID: 20260519_drop_email_workflow_config
Revises: 20260518_merge_heads
Create Date: 2026-05-19

"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "20260519_drop_email_config"
down_revision: Union[str, Sequence[str]] = "20260518_merge_heads"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        "DELETE FROM system_configs WHERE category = 'email' AND key = 'workflow_config'"
    )


def downgrade() -> None:
    # No restore — the consuming code has been deleted.
    pass
