"""Retain the permission source of workflow-launched agent runs."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "20261114_agent_wf_authority"
down_revision = "20261110_form_layout"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("agent_runs", sa.Column("permission_workflow_id", postgresql.UUID(as_uuid=True), nullable=True))


def downgrade() -> None:
    op.drop_column("agent_runs", "permission_workflow_id")
