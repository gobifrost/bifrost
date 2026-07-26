"""add private solution builder tables

Revision ID: 20260725_builder_tables
Revises: 20260725_private_visibility
Create Date: 2026-07-25
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260725_builder_tables"
down_revision: str = "20260725_private_visibility"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "solution_source_revisions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("solution_id", sa.Uuid(), nullable=False),
        sa.Column("parent_revision_id", sa.Uuid(), nullable=True),
        sa.Column("restored_from_revision_id", sa.Uuid(), nullable=True),
        sa.Column("conversation_id", sa.Uuid(), nullable=True),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column("source_sha256", sa.String(length=64), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("NOW()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["solution_id"], ["solutions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["parent_revision_id"],
            ["solution_source_revisions.id"],
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["restored_from_revision_id"],
            ["solution_source_revisions.id"],
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(["conversation_id"], ["conversations.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_solution_source_revisions_solution_id",
        "solution_source_revisions",
        ["solution_id"],
    )
    op.create_index(
        "ix_solution_source_revisions_solution_created",
        "solution_source_revisions",
        ["solution_id", "created_at"],
    )

    op.create_table(
        "solution_builder_projects",
        sa.Column("solution_id", sa.Uuid(), nullable=False),
        sa.Column("current_revision_id", sa.Uuid(), nullable=True),
        sa.Column("deployed_revision_id", sa.Uuid(), nullable=True),
        sa.Column(
            "promotion_status",
            sa.String(length=16),
            server_default="none",
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("NOW()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("NOW()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["solution_id"], ["solutions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["current_revision_id"],
            ["solution_source_revisions.id"],
            name="fk_solution_builder_projects_current_revision_id",
            ondelete="SET NULL",
            use_alter=True,
        ),
        sa.ForeignKeyConstraint(
            ["deployed_revision_id"],
            ["solution_source_revisions.id"],
            name="fk_solution_builder_projects_deployed_revision_id",
            ondelete="SET NULL",
            use_alter=True,
        ),
        sa.PrimaryKeyConstraint("solution_id"),
    )

    op.create_table(
        "solution_builder_sessions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("solution_id", sa.Uuid(), nullable=False),
        sa.Column("conversation_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("NOW()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("NOW()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["solution_id"], ["solutions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["conversation_id"], ["conversations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_solution_builder_sessions_solution_id",
        "solution_builder_sessions",
        ["solution_id"],
    )
    op.create_index(
        "ix_solution_builder_sessions_conversation_id",
        "solution_builder_sessions",
        ["conversation_id"],
    )
    op.create_index(
        "ix_solution_builder_sessions_user_id",
        "solution_builder_sessions",
        ["user_id"],
    )

    op.create_table(
        "solution_builder_turns",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("session_id", sa.Uuid(), nullable=False),
        sa.Column("requested_by", sa.Uuid(), nullable=True),
        sa.Column("base_revision_id", sa.Uuid(), nullable=True),
        sa.Column("output_revision_id", sa.Uuid(), nullable=True),
        sa.Column("build_job_id", sa.Uuid(), nullable=True),
        sa.Column("deploy_job_id", sa.Uuid(), nullable=True),
        sa.Column(
            "status",
            sa.String(length=16),
            server_default="queued",
            nullable=False,
        ),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("NOW()"),
            nullable=False,
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["session_id"],
            ["solution_builder_sessions.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(["requested_by"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(
            ["base_revision_id"],
            ["solution_source_revisions.id"],
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["output_revision_id"],
            ["solution_source_revisions.id"],
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_solution_builder_turns_session_id", "solution_builder_turns", ["session_id"])
    op.create_index("ix_solution_builder_turns_status", "solution_builder_turns", ["status"])


def downgrade() -> None:
    op.drop_index("ix_solution_builder_turns_status", table_name="solution_builder_turns")
    op.drop_index("ix_solution_builder_turns_session_id", table_name="solution_builder_turns")
    op.drop_table("solution_builder_turns")

    op.drop_index(
        "ix_solution_builder_sessions_user_id",
        table_name="solution_builder_sessions",
    )
    op.drop_index(
        "ix_solution_builder_sessions_conversation_id",
        table_name="solution_builder_sessions",
    )
    op.drop_index(
        "ix_solution_builder_sessions_solution_id",
        table_name="solution_builder_sessions",
    )
    op.drop_table("solution_builder_sessions")

    op.drop_constraint(
        "fk_solution_builder_projects_current_revision_id",
        "solution_builder_projects",
        type_="foreignkey",
    )
    op.drop_constraint(
        "fk_solution_builder_projects_deployed_revision_id",
        "solution_builder_projects",
        type_="foreignkey",
    )
    op.drop_table("solution_builder_projects")

    op.drop_index(
        "ix_solution_source_revisions_solution_created",
        table_name="solution_source_revisions",
    )
    op.drop_index(
        "ix_solution_source_revisions_solution_id",
        table_name="solution_source_revisions",
    )
    op.drop_table("solution_source_revisions")
