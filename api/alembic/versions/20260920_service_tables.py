"""Service definitions and attempts (supervised workflow services).

Slice 1 of the supervised-services plan
(docs/plans/2026-09-20-supervised-workflow-services-plan.md): durable tables
for @service lifecycle. No writers yet — the reconciler, claim protocol, and
CRUD endpoints arrive in Slice 2.

- service_definitions: one row per @service workflow. Policy
  (startup/restart) is portable content; desired_state, enablement, and
  launch-suppression fields (blocked_reason, restart_eligible_at) are
  environment/runtime state.
- service_attempts: one row per supervised run. A partial unique index allows
  at most one live (starting/running/stopping) attempt per service; updates
  carry the lease token so stale workers are fenced after reassignment.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260920_service_tables"
down_revision: str | None = "20260918_profile_failover"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "service_definitions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "workflow_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("workflows.id", ondelete="RESTRICT"),
            nullable=False,
            unique=True,
        ),
        sa.Column(
            "organization_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column(
            "solution_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("solutions.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("TRUE")),
        sa.Column("startup_policy", sa.String(20), nullable=False, server_default="automatic"),
        sa.Column("restart_policy", sa.String(20), nullable=False, server_default="always"),
        sa.Column("desired_state", sa.String(20), nullable=False, server_default="stopped"),
        sa.Column("blocked_reason", sa.String(20), nullable=True),
        sa.Column("restart_eligible_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("current_revision", sa.String(64), nullable=True),
        sa.Column("graceful_shutdown_seconds", sa.Integer(), nullable=False, server_default="30"),
        sa.Column("startup_grace_seconds", sa.Integer(), nullable=False, server_default="60"),
        sa.Column("restart_backoff_initial_seconds", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("restart_backoff_max_seconds", sa.Integer(), nullable=False, server_default="300"),
        sa.Column("crash_loop_max_restarts", sa.Integer(), nullable=False, server_default="5"),
        sa.Column("crash_loop_window_seconds", sa.Integer(), nullable=False, server_default="300"),
        sa.Column("created_by", sa.String(255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
    )
    op.create_index(
        "ix_service_definitions_organization_id",
        "service_definitions",
        ["organization_id"],
    )
    op.create_index(
        "ix_service_definitions_solution_id",
        "service_definitions",
        ["solution_id"],
    )
    op.create_index(
        "ix_service_definitions_enabled",
        "service_definitions",
        ["enabled"],
    )
    op.create_table(
        "service_attempts",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "service_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("service_definitions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("revision", sa.String(64), nullable=True),
        sa.Column("worker_id", sa.String(255), nullable=True),
        sa.Column("lease_token", sa.String(64), nullable=False),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("state", sa.String(20), nullable=False, server_default="starting"),
        sa.Column("ready_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("stop_requested_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("stopped_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("exit_code", sa.Integer(), nullable=True),
        sa.Column("exit_reason", sa.Text(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("restart_number", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
    )
    op.create_index(
        "ix_service_attempts_service_id",
        "service_attempts",
        ["service_id"],
    )
    op.create_index(
        "ix_service_attempts_state",
        "service_attempts",
        ["state"],
    )
    op.create_index(
        "uq_service_attempts_single_live",
        "service_attempts",
        ["service_id"],
        unique=True,
        postgresql_where=sa.text("state IN ('starting', 'running', 'stopping')"),
    )


def downgrade() -> None:
    op.drop_table("service_attempts")
    op.drop_table("service_definitions")
