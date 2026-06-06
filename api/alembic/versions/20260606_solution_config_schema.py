"""solution_config_schema declaration table

Revision ID: 20260606_solution_config_schema
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "20260606_solution_config_schema"
down_revision = "20260606_table_name_sol_scope"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "solution_config_schema",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "solution_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("solutions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("key", sa.String(length=255), nullable=False),
        sa.Column("type", sa.String(length=50), nullable=False),
        sa.Column("required", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("description", sa.String(length=500), nullable=True),
        sa.Column("default", sa.Text(), nullable=True),
        sa.Column("position", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()")),
    )
    op.create_index("ix_solution_config_schema_solution_id", "solution_config_schema", ["solution_id"])
    op.create_index(
        "ix_solution_config_schema_sol_key_unique",
        "solution_config_schema",
        ["solution_id", "key"],
        unique=True,
        postgresql_where=sa.text("solution_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_solution_config_schema_sol_key_unique", table_name="solution_config_schema")
    op.drop_index("ix_solution_config_schema_solution_id", table_name="solution_config_schema")
    op.drop_table("solution_config_schema")
