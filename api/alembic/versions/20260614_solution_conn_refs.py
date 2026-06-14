"""solution connection refs + solution readme column

Revision ID: 20260614_solution_conn_refs
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "20260614_solution_conn_refs"
down_revision = "20260614_solution_setup_complete"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "solution_connection_schema",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "solution_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("solutions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("integration_name", sa.String(length=255), nullable=False),
        sa.Column(
            "template",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default="{}",
        ),
        sa.Column("position", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
    )
    op.create_index(
        "ix_solution_connection_schema_solution_id",
        "solution_connection_schema",
        ["solution_id"],
    )
    op.create_index(
        "ix_solution_connection_schema_sol_name_unique",
        "solution_connection_schema",
        ["solution_id", "integration_name"],
        unique=True,
    )
    op.add_column("solutions", sa.Column("readme", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("solutions", "readme")
    op.drop_index(
        "ix_solution_connection_schema_sol_name_unique",
        table_name="solution_connection_schema",
    )
    op.drop_index(
        "ix_solution_connection_schema_solution_id",
        table_name="solution_connection_schema",
    )
    op.drop_table("solution_connection_schema")
