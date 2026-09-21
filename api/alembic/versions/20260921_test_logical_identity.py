"""Stable logical test identity and default collection marker.

Revision ID: 20260921_test_logical_identity
Revises: 20260920_recurring_triggers
Create Date: 2026-09-21 00:00:00.000000
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "20260921_test_logical_identity"
down_revision: str | Sequence[str] | None = "20260920_recurring_triggers"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "agent_evaluation_suites",
        sa.Column(
            "is_default",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    # Scope the pre-existing suite name key to non-default rows so every
    # default collection can share its reserved name/version per agent.
    op.drop_constraint(
        "uq_eval_suites_org_name_version",
        "agent_evaluation_suites",
        type_="unique",
    )
    op.create_index(
        "uq_eval_suites_org_name_version",
        "agent_evaluation_suites",
        ["org_id", "name", "version"],
        unique=True,
        postgresql_where=sa.text("NOT is_default"),
    )
    op.add_column(
        "agent_evaluation_cases",
        sa.Column("logical_test_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    # Backfill: every existing row becomes its own logical test. No invented
    # lineage across rows; version history grouping starts from here.
    op.execute(
        sa.text(
            "UPDATE agent_evaluation_cases "
            "SET logical_test_id = gen_random_uuid() "
            "WHERE logical_test_id IS NULL"
        )
    )
    op.alter_column("agent_evaluation_cases", "logical_test_id", nullable=False)
    op.create_unique_constraint(
        "uq_eval_cases_logical_version",
        "agent_evaluation_cases",
        ["logical_test_id", "version"],
    )
    op.create_index(
        "ix_eval_cases_logical_test_id",
        "agent_evaluation_cases",
        ["logical_test_id"],
    )
    op.create_index(
        "ix_eval_suites_is_default",
        "agent_evaluation_suites",
        ["is_default"],
        postgresql_where=sa.text("is_default"),
    )
    op.create_index(
        "uq_eval_suites_default_org_agent",
        "agent_evaluation_suites",
        ["org_id", "agent_id"],
        unique=True,
        postgresql_where=sa.text("is_default"),
    )
    op.create_check_constraint(
        "ck_eval_suites_default_scope",
        "agent_evaluation_suites",
        sa.text(
            "(NOT is_default) OR (org_id IS NOT NULL AND agent_id IS NOT NULL)"
        ),
    )
    # Agent-owned lifecycle for defaults, enforced for EVERY deletion path
    # (HTTP route, git sync stale sweep, indexers, future writers): removing
    # an agent removes its default suites (cases/matrices/executions follow
    # their FK cascades) before the SET NULL agent FK runs for named suites.
    op.execute(
        sa.text(
            """
            CREATE OR REPLACE FUNCTION delete_agent_default_suites()
            RETURNS trigger AS $$
            BEGIN
                DELETE FROM agent_evaluation_suites
                WHERE agent_id = OLD.id AND is_default;
                RETURN OLD;
            END;
            $$ LANGUAGE plpgsql
            """
        )
    )
    op.execute(
        sa.text(
            """
            CREATE TRIGGER trg_agents_delete_default_suites
            BEFORE DELETE ON agents
            FOR EACH ROW
            EXECUTE FUNCTION delete_agent_default_suites()
            """
        )
    )


def downgrade() -> None:
    op.execute(sa.text("DROP TRIGGER IF EXISTS trg_agents_delete_default_suites ON agents"))
    op.execute(sa.text("DROP FUNCTION IF EXISTS delete_agent_default_suites()"))
    # Post-upgrade data may hold several defaults sharing (org, name,
    # version). Rename them apart first so the restored legacy unique key
    # holds without deleting user data.
    op.execute(
        sa.text(
            "UPDATE agent_evaluation_suites "
            "SET name = 'Default Tests (' || LEFT(agent_id::text, 8) || ')' "
            "WHERE is_default"
        )
    )
    op.drop_constraint(
        "ck_eval_suites_default_scope", "agent_evaluation_suites", type_="check"
    )
    op.drop_index(
        "uq_eval_suites_default_org_agent",
        table_name="agent_evaluation_suites",
    )
    op.drop_index(
        "uq_eval_suites_org_name_version",
        table_name="agent_evaluation_suites",
    )
    op.create_unique_constraint(
        "uq_eval_suites_org_name_version",
        "agent_evaluation_suites",
        ["org_id", "name", "version"],
    )
    op.drop_index(
        "ix_eval_suites_is_default", table_name="agent_evaluation_suites"
    )
    op.drop_column("agent_evaluation_suites", "is_default")
    op.drop_index(
        "ix_eval_cases_logical_test_id", table_name="agent_evaluation_cases"
    )
    op.drop_constraint(
        "uq_eval_cases_logical_version",
        "agent_evaluation_cases",
        type_="unique",
    )
    op.alter_column("agent_evaluation_cases", "logical_test_id", nullable=True)
    op.execute(sa.text("UPDATE agent_evaluation_cases SET logical_test_id = NULL"))
    op.drop_column("agent_evaluation_cases", "logical_test_id")
