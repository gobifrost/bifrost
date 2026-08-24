"""add global Builder operation changesets

Revision ID: 20260823_global_op_changes
Revises: 20260822_artifact_tombstones

Global Builder loose-resource mutations are reviewed operation changes, not
live model-turn writes. This table stores validated canonical operation payloads
and before-state fingerprints until a platform user explicitly applies or rolls
them back.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "20260823_global_op_changes"
down_revision: str = "20260822_artifact_tombstones"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "solution_global_operation_changes",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "solution_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("solutions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("operation_id", sa.String(length=128), nullable=False),
        sa.Column("resource_type", sa.String(length=64), nullable=False),
        sa.Column("resource_id", sa.String(length=128), nullable=True),
        sa.Column("apply_job_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("rollback_job_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "state",
            sa.String(length=16),
            server_default="staged",
            nullable=False,
        ),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("before_state", sa.JSON(), nullable=True),
        sa.Column("before_fingerprint", sa.String(length=64), nullable=True),
        sa.Column("applied_state", sa.JSON(), nullable=True),
        sa.Column("applied_fingerprint", sa.String(length=64), nullable=True),
        sa.Column("validation_errors", sa.JSON(), nullable=False),
        sa.Column(
            "created_by",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "applied_by",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "rolled_back_by",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("NOW()"),
            nullable=False,
        ),
        sa.Column("applied_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("rolled_back_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "state IN ('staged', 'applying', 'applied', 'rolled_back', 'discarded', 'failed')",
            name="ck_solution_global_operation_changes_state",
        ),
    )
    op.create_index(
        "ix_solution_global_operation_changes_solution_state",
        "solution_global_operation_changes",
        ["solution_id", "state", "created_at"],
    )
    op.create_index(
        "ix_solution_global_operation_changes_solution_id",
        "solution_global_operation_changes",
        ["solution_id"],
    )
    op.create_index(
        "ix_solution_global_operation_changes_apply_job_id",
        "solution_global_operation_changes",
        ["apply_job_id"],
    )
    op.create_index(
        "ix_solution_global_operation_changes_rollback_job_id",
        "solution_global_operation_changes",
        ["rollback_job_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_solution_global_operation_changes_rollback_job_id",
        table_name="solution_global_operation_changes",
    )
    op.drop_index(
        "ix_solution_global_operation_changes_apply_job_id",
        table_name="solution_global_operation_changes",
    )
    op.drop_index(
        "ix_solution_global_operation_changes_solution_id",
        table_name="solution_global_operation_changes",
    )
    op.drop_index(
        "ix_solution_global_operation_changes_solution_state",
        table_name="solution_global_operation_changes",
    )
    op.drop_table("solution_global_operation_changes")
