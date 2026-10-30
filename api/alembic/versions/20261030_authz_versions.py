"""Authority version, credential protocol records and credential formats.

Revision ID: 20261030_authz_versions
Revises: 20261025_remote_worker_ownership
Create Date: 2026-10-30

Token PR1a (docs/plans/2026-10-30-token-pr1-implementation-plan.md §3.2,
§8.2):

- ``authz_versions`` holds one global authority epoch, a random signed 64-bit
  value compared for equality only. ``authz_epoch_bump_once()`` replaces it
  at most once per transaction (a transaction-local flag). The row can be
  neither deleted nor truncated.
- ``authz_protocol_state`` durably records the first start of each API
  credential protocol. A fresh install records protocol 1 here, since no
  older pod, token or run can exist.
- ``executions.credential_version`` / ``service_attempts.credential_version``
  record the format of a run's credentials (NULL = legacy), and
  ``execution_workers.remote_credential_protocol`` the protocol a remote
  launcher negotiated (NULL = protocol 1).
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import context, op


revision: str = "20261030_authz_versions"
down_revision: Union[str, None] = "20261025_remote_worker_ownership"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Set by alembic/env.py before any migration of this run executes.
_STARTING_REVISION = "bifrost_starting_revision"

# 64 random bits from one UUIDv4: hex digits 1-12 and 18-21 carry no version
# or variant bits.
_RANDOM_EPOCH_SQL = (
    "('x' || substr(replace(gen_random_uuid()::text, '-', ''), 1, 12)"
    " || substr(replace(gen_random_uuid()::text, '-', ''), 18, 4))::bit(64)::bigint"
)


def _started_on_empty_schema() -> bool:
    """Whether this upgrade run began on an empty schema (a fresh install)."""
    attributes = context.config.attributes
    return _STARTING_REVISION in attributes and attributes[_STARTING_REVISION] is None


def upgrade() -> None:
    op.create_table(
        "authz_versions",
        sa.Column("subject_kind", sa.String(length=32), nullable=False),
        sa.Column("subject_id", sa.dialects.postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("version", sa.BigInteger(), nullable=False),
        sa.PrimaryKeyConstraint("subject_kind", "subject_id"),
    )
    op.execute(
        "INSERT INTO authz_versions (subject_kind, subject_id, version) VALUES "
        f"('global', '00000000-0000-0000-0000-000000000000', {_RANDOM_EPOCH_SQL})"
    )
    op.execute(
        """
        CREATE FUNCTION authz_versions_protect() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'authz_versions rows cannot be deleted or truncated; run SELECT authz_epoch_bump_once() instead';
        END;
        $$
        """
    )
    op.execute(
        "CREATE TRIGGER authz_versions_no_delete BEFORE DELETE ON authz_versions "
        "FOR EACH STATEMENT EXECUTE FUNCTION authz_versions_protect()"
    )
    op.execute(
        "CREATE TRIGGER authz_versions_no_truncate BEFORE TRUNCATE ON authz_versions "
        "FOR EACH STATEMENT EXECUTE FUNCTION authz_versions_protect()"
    )
    # The bump runs once per transaction: the flag is transaction-local, and
    # a rolled-back savepoint undoes its bump and its flag together.
    op.execute(
        f"""
        CREATE FUNCTION authz_epoch_bump_once() RETURNS void
        LANGUAGE plpgsql AS $$
        BEGIN
            IF current_setting('bifrost.authz_epoch_bumped', true) IS DISTINCT FROM '1' THEN
                UPDATE authz_versions
                   SET version = {_RANDOM_EPOCH_SQL}
                 WHERE subject_kind = 'global'
                   AND subject_id = '00000000-0000-0000-0000-000000000000';
                PERFORM set_config('bifrost.authz_epoch_bumped', '1', true);
            END IF;
        END;
        $$
        """
    )

    op.create_table(
        "authz_protocol_state",
        sa.Column("protocol", sa.Integer(), autoincrement=False, nullable=False),
        sa.Column("first_started_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("protocol"),
    )
    if _started_on_empty_schema():
        op.execute(
            "INSERT INTO authz_protocol_state (protocol, first_started_at) VALUES (1, now())"
        )

    op.add_column("executions", sa.Column("credential_version", sa.SmallInteger(), nullable=True))
    op.add_column(
        "service_attempts", sa.Column("credential_version", sa.SmallInteger(), nullable=True)
    )
    op.add_column(
        "execution_workers",
        sa.Column("remote_credential_protocol", sa.SmallInteger(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("execution_workers", "remote_credential_protocol")
    op.drop_column("service_attempts", "credential_version")
    op.drop_column("executions", "credential_version")
    op.drop_table("authz_protocol_state")
    op.execute("DROP FUNCTION authz_epoch_bump_once()")
    op.execute("DROP TABLE authz_versions")
    op.execute("DROP FUNCTION authz_versions_protect()")
