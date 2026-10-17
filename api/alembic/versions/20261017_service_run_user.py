"""Bind each new service attempt to its launch identity.

Revision ID: 20261017_service_run_user
Revises: 20261016_policy_authority
Create Date: 2026-10-17

Existing attempt rows deliberately remain unbound. Their workflow identity may
have changed since they were created, so inferring one during migration would
silently change their authority. New lifecycle claims bind the identity.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "20261017_service_run_user"
down_revision: str | None = "20261016_policy_authority"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "service_attempts",
        sa.Column("run_user_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    # Existing rows remain NULL. NOT VALID avoids scanning service-attempt
    # history while still enforcing the relationship for every new write.
    op.create_foreign_key(
        "fk_service_attempts_run_user_id",
        "service_attempts",
        "users",
        ["run_user_id"],
        ["id"],
        ondelete="SET NULL",
        postgresql_not_valid=True,
    )
    # User deletion invokes the foreign key action; keep it from scanning
    # the attempt history for the small subset with a bound identity.
    with op.get_context().autocommit_block():
        op.execute(
            "CREATE INDEX CONCURRENTLY ix_service_attempts_run_user_id "
            "ON service_attempts (run_user_id) WHERE run_user_id IS NOT NULL"
        )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix_service_attempts_run_user_id")
    op.drop_constraint("fk_service_attempts_run_user_id", "service_attempts", type_="foreignkey")
    op.drop_column("service_attempts", "run_user_id")
