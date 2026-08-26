"""Replace inert event filters with structured criteria and decision evidence.

Revision ID: 20260827_event_criteria
Revises: 20260823_job_memory_profiles
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260827_event_criteria"
down_revision: str | Sequence[str] = "20260823_job_memory_profiles"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # The legacy field was advertised but never evaluated. Do not guess how an
    # arbitrary stored string should map to bounded criteria: deployed values
    # require explicit operator review before this migration may proceed.
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1
                FROM event_subscriptions
                WHERE filter_expression IS NOT NULL
                  AND BTRIM(filter_expression) <> ''
            ) THEN
                RAISE EXCEPTION
                    'event_subscriptions.filter_expression contains values that require operator review'
                    USING HINT =
                        'Replace each legacy expression with approved structured criteria or clear it before upgrading.';
            END IF;
        END
        $$;
        """
    )
    op.add_column(
        "event_subscriptions",
        sa.Column("criteria", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.drop_column("event_subscriptions", "filter_expression")
    op.add_column(
        "event_deliveries",
        sa.Column(
            "rule_decision",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("event_deliveries", "rule_decision")
    op.add_column(
        "event_subscriptions",
        sa.Column("filter_expression", sa.Text(), nullable=True),
    )
    op.drop_column("event_subscriptions", "criteria")
