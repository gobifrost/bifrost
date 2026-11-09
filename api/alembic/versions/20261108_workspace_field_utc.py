"""Repair form field timestamps omitted by the original UTC conversion.

Revision ID: 20261108_workspace_field_utc
Revises: 20261107_running_run_lineage
"""

import sqlalchemy as sa
from alembic import op

revision = "20261108_workspace_field_utc"
down_revision = "20261107_running_run_lineage"
branch_labels = None
depends_on = None


def upgrade() -> None:
    connection = op.get_bind()
    for column in ("created_at", "updated_at"):
        data_type = connection.execute(sa.text(
            "SELECT data_type FROM information_schema.columns "
            "WHERE table_schema=current_schema() AND table_name='form_fields' "
            "AND column_name=:column"
        ), {"column": column}).scalar_one()
        if data_type == "timestamp without time zone":
            op.alter_column(
                "form_fields", column, type_=sa.DateTime(timezone=True),
                existing_type=sa.DateTime(), existing_nullable=False,
                postgresql_using=f"{column} AT TIME ZONE 'UTC'",
            )


def downgrade() -> None:
    for column in ("created_at", "updated_at"):
        op.alter_column(
            "form_fields", column, type_=sa.DateTime(),
            existing_type=sa.DateTime(timezone=True), existing_nullable=False,
            postgresql_using=f"{column} AT TIME ZONE 'UTC'",
        )
