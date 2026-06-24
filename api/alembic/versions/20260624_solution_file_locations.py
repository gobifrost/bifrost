"""solution file location declarations

Revision ID: 20260624_solution_file_locations
Revises: 20260624_sol_status
Create Date: 2026-06-24
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "20260624_solution_file_locations"
down_revision: str = "20260624_sol_status"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "solution_file_locations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("solution_id", sa.Uuid(), nullable=False),
        sa.Column("location", sa.String(length=255), nullable=False),
        sa.Column("position", sa.Integer(), server_default=sa.text("0"), nullable=False),
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
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_solution_file_locations_solution_id",
        "solution_file_locations",
        ["solution_id"],
    )
    op.create_index(
        "uq_solution_file_locations_solution_location",
        "solution_file_locations",
        ["solution_id", "location"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index(
        "uq_solution_file_locations_solution_location",
        table_name="solution_file_locations",
    )
    op.drop_index(
        "ix_solution_file_locations_solution_id",
        table_name="solution_file_locations",
    )
    op.drop_table("solution_file_locations")
