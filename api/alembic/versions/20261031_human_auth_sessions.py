"""Add durable human refresh sessions and version system-account changes.

Revision ID: 20261031_human_auth_sessions
Revises: 20261031_authz_claims_gate
Create Date: 2026-10-31

Human refresh sessions retain only the SHA-256 hash of their current JTI. The
registry is durable so Redis loss cannot make refresh authentication succeed.
The users authority trigger also begins versioning ``is_system``: a system
account change invalidates human access claims in the same transaction.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "20261031_human_auth_sessions"
down_revision: Union[str, None] = "20261031_authz_claims_gate"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_LOCK_WAIT = "1s"


def _take_users_lock() -> tuple[str, str]:
    """Take the bounded users DDL lock used by the authority migration."""
    previous = op.get_bind().execute(
        sa.text("SELECT current_setting('lock_timeout'), current_setting('statement_timeout')")
    ).one()
    op.execute(f"SET LOCAL lock_timeout = '{_LOCK_WAIT}'")
    op.execute(f"SET LOCAL statement_timeout = '{_LOCK_WAIT}'")
    op.execute("LOCK TABLE users IN SHARE ROW EXCLUSIVE MODE")
    return previous[0], previous[1]


def _restore_timeouts(previous: tuple[str, str]) -> None:
    """Restore the migration run's prior bounded-statement settings."""
    op.get_bind().execute(
        sa.text(
            "SELECT set_config('lock_timeout', :lock, true), "
            "set_config('statement_timeout', :statement, true)"
        ),
        {"lock": previous[0], "statement": previous[1]},
    )


def _create_users_update_trigger(*, include_is_system: bool) -> None:
    """Install the users update trigger with this release's authority facts."""
    is_system_condition = (
        "\n            OR OLD.is_system IS DISTINCT FROM NEW.is_system"
        if include_is_system
        else ""
    )
    op.execute(
        f"""
        CREATE CONSTRAINT TRIGGER authz_epoch_upd AFTER UPDATE ON users
            DEFERRABLE INITIALLY DEFERRED FOR EACH ROW
            WHEN (OLD.id IS DISTINCT FROM NEW.id
                OR OLD.email IS DISTINCT FROM NEW.email
                OR OLD.name IS DISTINCT FROM NEW.name
                OR OLD.organization_id IS DISTINCT FROM NEW.organization_id
                OR OLD.base_role_id IS DISTINCT FROM NEW.base_role_id
                OR OLD.is_external IS DISTINCT FROM NEW.is_external
                OR OLD.identity_kind IS DISTINCT FROM NEW.identity_kind
                OR OLD.is_active IS DISTINCT FROM NEW.is_active{is_system_condition})
            EXECUTE FUNCTION authz_epoch_bump_trigger()
        """
    )


def upgrade() -> None:
    previous = _take_users_lock()
    op.create_table(
        "human_auth_sessions",
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "user_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("current_jti_hash", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("current_jti_hash"),
    )
    op.create_index("ix_human_auth_sessions_user_id", "human_auth_sessions", ["user_id"])
    op.execute("DROP TRIGGER authz_epoch_upd ON users")
    _create_users_update_trigger(include_is_system=True)
    _restore_timeouts(previous)


def downgrade() -> None:
    previous = _take_users_lock()
    op.execute("DROP TRIGGER authz_epoch_upd ON users")
    _create_users_update_trigger(include_is_system=False)
    op.drop_index("ix_human_auth_sessions_user_id", table_name="human_auth_sessions")
    op.drop_table("human_auth_sessions")
    _restore_timeouts(previous)
