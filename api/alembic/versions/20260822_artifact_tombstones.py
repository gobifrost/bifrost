"""add artifact workspace tombstones

Revision ID: 20260822_artifact_tombstones
Revises: 20260821_builder_org_target

Logical workspace deletes must not remove Artifact rows or S3 objects because
prior message attachments and opaque ArtifactRefs remain durable. Tombstones
hide a workspace path until a newer artifact version is written at that path.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "20260822_artifact_tombstones"
down_revision: str = "20260821_builder_org_target"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "artifact_workspace_tombstones",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("logical_path", sa.String(length=500), nullable=False),
        sa.Column(
            "created_by_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "organization_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("organizations.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("NOW()"),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "workspace_id",
            "logical_path",
            name="uq_artifact_workspace_tombstones_path",
        ),
    )
    op.create_index(
        "ix_artifact_workspace_tombstones_workspace_path",
        "artifact_workspace_tombstones",
        ["workspace_id", "logical_path"],
    )
    op.create_index(
        "ix_artifact_workspace_tombstones_created_at",
        "artifact_workspace_tombstones",
        ["created_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_artifact_workspace_tombstones_created_at",
        table_name="artifact_workspace_tombstones",
    )
    op.drop_index(
        "ix_artifact_workspace_tombstones_workspace_path",
        table_name="artifact_workspace_tombstones",
    )
    op.drop_table("artifact_workspace_tombstones")
