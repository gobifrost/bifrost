"""add artifact workspaces

Revision ID: 20260816_artifact_workspace
Revises: 20260816_artifact_identity
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "20260816_artifact_workspace"
down_revision = "20260816_artifact_identity"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "artifacts",
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "artifacts",
        sa.Column("logical_path", sa.String(length=500), nullable=True),
    )
    op.create_index(
        "ix_artifacts_workspace_path",
        "artifacts",
        ["workspace_id", "logical_path"],
    )
    op.execute(
        """
        UPDATE artifacts AS artifact
        SET workspace_id = binding.conversation_id,
            logical_path = artifact.filename
        FROM message_attachments AS binding
        WHERE binding.artifact_id = artifact.id
        """
    )


def downgrade() -> None:
    op.drop_index("ix_artifacts_workspace_path", table_name="artifacts")
    op.drop_column("artifacts", "logical_path")
    op.drop_column("artifacts", "workspace_id")
