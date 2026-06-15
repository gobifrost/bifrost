"""Solution-owned event/schedule triggers.

EventSource and EventSubscription can now be deployed as Solution-owned rows
(criterion: a Solution ships its triggers). Loose `_repo`/ad-hoc triggers keep
solution_id NULL and their existing org/global lifecycle; Solution triggers carry
solution_id and are deploy-managed + read-only outside deploy. Child rows
(schedule_sources / webhook_sources) are owned transitively via their EventSource
FK cascade, so they need no solution_id of their own.

Revision ID: 20260614_solution_triggers
Revises: 15f86c4cbc4c
"""

import sqlalchemy as sa
from alembic import op

revision = "20260614_solution_triggers"
down_revision = "15f86c4cbc4c"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for table in ("event_sources", "event_subscriptions"):
        op.add_column(
            table,
            sa.Column("solution_id", sa.UUID(), nullable=True),
        )
        op.create_foreign_key(
            f"{table}_solution_id_fkey",
            table,
            "solutions",
            ["solution_id"],
            ["id"],
            ondelete="CASCADE",
        )
        op.create_index(
            f"ix_{table}_solution_id",
            table,
            ["solution_id"],
        )


def downgrade() -> None:
    for table in ("event_sources", "event_subscriptions"):
        op.drop_index(f"ix_{table}_solution_id", table_name=table)
        op.drop_constraint(
            f"{table}_solution_id_fkey",
            table,
            type_="foreignkey",
        )
        op.drop_column(table, "solution_id")
