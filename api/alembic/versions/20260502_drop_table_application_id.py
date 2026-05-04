"""drop tables.application_id column

Revision ID: 20260502_drop_table_app
Revises: 20260429_table_access
Create Date: 2026-05-02

The same-name-different-app-within-one-org table model is no longer
supported. Tables are scoped only by ``organization_id``. Apps that
need their own document storage create org-scoped tables with
distinct names. The CLI table-document handlers that used
``Table.application_id`` as a lookup axis were removed in the
previous commit.
"""
from alembic import op
import sqlalchemy as sa


revision = "20260502_drop_table_app"
down_revision = "20260429_table_access"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # FK was named by SQLAlchemy when the column was added; resolve at
    # runtime so we don't depend on the auto-generated name.
    bind = op.get_bind()
    fk_name = bind.execute(
        sa.text(
            """
            SELECT tc.constraint_name
            FROM information_schema.table_constraints tc
            JOIN information_schema.key_column_usage kcu
              ON tc.constraint_name = kcu.constraint_name
            WHERE tc.table_name = 'tables'
              AND tc.constraint_type = 'FOREIGN KEY'
              AND kcu.column_name = 'application_id'
            """
        )
    ).scalar()
    if fk_name:
        op.drop_constraint(fk_name, "tables", type_="foreignkey")
    op.drop_column("tables", "application_id")


def downgrade() -> None:
    op.add_column(
        "tables",
        sa.Column("application_id", sa.UUID(), nullable=True),
    )
    op.create_foreign_key(
        "tables_application_id_fkey",
        "tables",
        "applications",
        ["application_id"],
        ["id"],
        ondelete="SET NULL",
        onupdate="CASCADE",
    )
