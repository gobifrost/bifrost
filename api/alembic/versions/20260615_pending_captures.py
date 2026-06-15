"""pending_captures queue table

Queue of entities captured into a Solution (UI or CLI) but not yet pulled into
source. Deploy 409-blocks while any row for the install is absent from the
incoming manifest, so a captured-but-unpulled entity is never silently deleted
by deploy's full-replace reconcile sweep.

Revision ID: 20260615_pending_captures
Revises: 20260614_solution_triggers
"""

import sqlalchemy as sa
from alembic import op

revision = "20260615_pending_captures"
down_revision = "20260614_solution_triggers"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "pending_captures",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("solution_id", sa.UUID(), nullable=False),
        sa.Column("entity_type", sa.String(length=32), nullable=False),
        sa.Column("entity_id", sa.String(length=255), nullable=False),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("captured_by", sa.UUID(), nullable=True),
        sa.ForeignKeyConstraint(
            ["solution_id"], ["solutions.id"], ondelete="CASCADE"
        ),
        sa.UniqueConstraint(
            "solution_id",
            "entity_type",
            "entity_id",
            name="uq_pending_capture_entity",
        ),
    )
    op.create_index(
        "ix_pending_captures_solution_id", "pending_captures", ["solution_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_pending_captures_solution_id", table_name="pending_captures")
    op.drop_table("pending_captures")
