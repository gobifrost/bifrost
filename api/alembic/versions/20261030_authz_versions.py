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

Locking: every table lock is taken first, in two LOCK TABLE statements each
capped at 1 s (``_take_locks``). Product traffic on the locked tables can
still queue behind the migration: at most about 2 s on the three tables that
get columns and about 1 s on the other trigger tables, plus milliseconds of
DDL and commit. Behind a long-running transaction the upgrade times out
rather than holding traffic longer; the upgrade run is one transaction, so a
failure leaves the prior revision and the operator re-runs it. Splitting this
into several migrations would not shorten any lock hold: ``alembic/env.py``
runs a whole upgrade in one transaction.
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


# Every table holding a fact an access token carries (plan §3.2). A change
# queues a deferred constraint-trigger event that bumps the epoch before the
# transaction commits; TRUNCATE bumps immediately. INSERT fires only where a
# new row can change what an existing token means; run tables fire only when
# a carried creation-time column changes (never expected). Status, state and
# lease are read live, never versioned. "Any column" compares whole rows as
# jsonb: system_configs.value_json is json, which has no equality operator.
_TRIGGER_STATEMENTS: tuple[str, ...] = (
    """
    CREATE CONSTRAINT TRIGGER authz_epoch_upd AFTER UPDATE ON users
        DEFERRABLE INITIALLY DEFERRED FOR EACH ROW
        WHEN (OLD.id IS DISTINCT FROM NEW.id
            OR OLD.email IS DISTINCT FROM NEW.email
            OR OLD.name IS DISTINCT FROM NEW.name
            OR OLD.organization_id IS DISTINCT FROM NEW.organization_id
            OR OLD.base_role_id IS DISTINCT FROM NEW.base_role_id
            OR OLD.is_external IS DISTINCT FROM NEW.is_external
            OR OLD.identity_kind IS DISTINCT FROM NEW.identity_kind
            OR OLD.is_active IS DISTINCT FROM NEW.is_active)
        EXECUTE FUNCTION authz_epoch_bump_trigger()
    """,
    """
    CREATE CONSTRAINT TRIGGER authz_epoch_del AFTER DELETE ON users
        DEFERRABLE INITIALLY DEFERRED FOR EACH ROW
        EXECUTE FUNCTION authz_epoch_bump_trigger()
    """,
    """
    CREATE TRIGGER authz_epoch_truncate AFTER TRUNCATE ON users
        FOR EACH STATEMENT EXECUTE FUNCTION authz_epoch_bump_trigger()
    """,
    """
    CREATE CONSTRAINT TRIGGER authz_epoch_upd AFTER UPDATE ON organizations
        DEFERRABLE INITIALLY DEFERRED FOR EACH ROW
        WHEN (OLD.id IS DISTINCT FROM NEW.id
            OR OLD.is_provider IS DISTINCT FROM NEW.is_provider)
        EXECUTE FUNCTION authz_epoch_bump_trigger()
    """,
    """
    CREATE CONSTRAINT TRIGGER authz_epoch_del AFTER DELETE ON organizations
        DEFERRABLE INITIALLY DEFERRED FOR EACH ROW
        EXECUTE FUNCTION authz_epoch_bump_trigger()
    """,
    """
    CREATE TRIGGER authz_epoch_truncate AFTER TRUNCATE ON organizations
        FOR EACH STATEMENT EXECUTE FUNCTION authz_epoch_bump_trigger()
    """,
    """
    CREATE CONSTRAINT TRIGGER authz_epoch_ins AFTER INSERT ON user_roles
        DEFERRABLE INITIALLY DEFERRED FOR EACH ROW
        EXECUTE FUNCTION authz_epoch_bump_trigger()
    """,
    """
    CREATE CONSTRAINT TRIGGER authz_epoch_upd AFTER UPDATE ON user_roles
        DEFERRABLE INITIALLY DEFERRED FOR EACH ROW
        WHEN (to_jsonb(OLD) IS DISTINCT FROM to_jsonb(NEW))
        EXECUTE FUNCTION authz_epoch_bump_trigger()
    """,
    """
    CREATE CONSTRAINT TRIGGER authz_epoch_del AFTER DELETE ON user_roles
        DEFERRABLE INITIALLY DEFERRED FOR EACH ROW
        EXECUTE FUNCTION authz_epoch_bump_trigger()
    """,
    """
    CREATE TRIGGER authz_epoch_truncate AFTER TRUNCATE ON user_roles
        FOR EACH STATEMENT EXECUTE FUNCTION authz_epoch_bump_trigger()
    """,
    """
    CREATE CONSTRAINT TRIGGER authz_epoch_ins AFTER INSERT ON user_role_boundaries
        DEFERRABLE INITIALLY DEFERRED FOR EACH ROW
        EXECUTE FUNCTION authz_epoch_bump_trigger()
    """,
    """
    CREATE CONSTRAINT TRIGGER authz_epoch_upd AFTER UPDATE ON user_role_boundaries
        DEFERRABLE INITIALLY DEFERRED FOR EACH ROW
        WHEN (to_jsonb(OLD) IS DISTINCT FROM to_jsonb(NEW))
        EXECUTE FUNCTION authz_epoch_bump_trigger()
    """,
    """
    CREATE CONSTRAINT TRIGGER authz_epoch_del AFTER DELETE ON user_role_boundaries
        DEFERRABLE INITIALLY DEFERRED FOR EACH ROW
        EXECUTE FUNCTION authz_epoch_bump_trigger()
    """,
    """
    CREATE TRIGGER authz_epoch_truncate AFTER TRUNCATE ON user_role_boundaries
        FOR EACH STATEMENT EXECUTE FUNCTION authz_epoch_bump_trigger()
    """,
    """
    CREATE CONSTRAINT TRIGGER authz_epoch_ins AFTER INSERT ON role_permissions
        DEFERRABLE INITIALLY DEFERRED FOR EACH ROW
        EXECUTE FUNCTION authz_epoch_bump_trigger()
    """,
    """
    CREATE CONSTRAINT TRIGGER authz_epoch_upd AFTER UPDATE ON role_permissions
        DEFERRABLE INITIALLY DEFERRED FOR EACH ROW
        WHEN (to_jsonb(OLD) IS DISTINCT FROM to_jsonb(NEW))
        EXECUTE FUNCTION authz_epoch_bump_trigger()
    """,
    """
    CREATE CONSTRAINT TRIGGER authz_epoch_del AFTER DELETE ON role_permissions
        DEFERRABLE INITIALLY DEFERRED FOR EACH ROW
        EXECUTE FUNCTION authz_epoch_bump_trigger()
    """,
    """
    CREATE TRIGGER authz_epoch_truncate AFTER TRUNCATE ON role_permissions
        FOR EACH STATEMENT EXECUTE FUNCTION authz_epoch_bump_trigger()
    """,
    """
    CREATE CONSTRAINT TRIGGER authz_epoch_upd AFTER UPDATE ON roles
        DEFERRABLE INITIALLY DEFERRED FOR EACH ROW
        WHEN (OLD.id IS DISTINCT FROM NEW.id
            OR OLD.name IS DISTINCT FROM NEW.name)
        EXECUTE FUNCTION authz_epoch_bump_trigger()
    """,
    """
    CREATE CONSTRAINT TRIGGER authz_epoch_del AFTER DELETE ON roles
        DEFERRABLE INITIALLY DEFERRED FOR EACH ROW
        EXECUTE FUNCTION authz_epoch_bump_trigger()
    """,
    """
    CREATE TRIGGER authz_epoch_truncate AFTER TRUNCATE ON roles
        FOR EACH STATEMENT EXECUTE FUNCTION authz_epoch_bump_trigger()
    """,
    """
    CREATE CONSTRAINT TRIGGER authz_epoch_upd AFTER UPDATE ON workflows
        DEFERRABLE INITIALLY DEFERRED FOR EACH ROW
        WHEN (OLD.id IS DISTINCT FROM NEW.id
            OR OLD.solution_id IS DISTINCT FROM NEW.solution_id
            OR OLD.permission_mode IS DISTINCT FROM NEW.permission_mode)
        EXECUTE FUNCTION authz_epoch_bump_trigger()
    """,
    """
    CREATE CONSTRAINT TRIGGER authz_epoch_del AFTER DELETE ON workflows
        DEFERRABLE INITIALLY DEFERRED FOR EACH ROW
        EXECUTE FUNCTION authz_epoch_bump_trigger()
    """,
    """
    CREATE TRIGGER authz_epoch_truncate AFTER TRUNCATE ON workflows
        FOR EACH STATEMENT EXECUTE FUNCTION authz_epoch_bump_trigger()
    """,
    """
    CREATE CONSTRAINT TRIGGER authz_epoch_ins AFTER INSERT ON workflow_permission_grants
        DEFERRABLE INITIALLY DEFERRED FOR EACH ROW
        EXECUTE FUNCTION authz_epoch_bump_trigger()
    """,
    """
    CREATE CONSTRAINT TRIGGER authz_epoch_upd AFTER UPDATE ON workflow_permission_grants
        DEFERRABLE INITIALLY DEFERRED FOR EACH ROW
        WHEN (to_jsonb(OLD) IS DISTINCT FROM to_jsonb(NEW))
        EXECUTE FUNCTION authz_epoch_bump_trigger()
    """,
    """
    CREATE CONSTRAINT TRIGGER authz_epoch_del AFTER DELETE ON workflow_permission_grants
        DEFERRABLE INITIALLY DEFERRED FOR EACH ROW
        EXECUTE FUNCTION authz_epoch_bump_trigger()
    """,
    """
    CREATE TRIGGER authz_epoch_truncate AFTER TRUNCATE ON workflow_permission_grants
        FOR EACH STATEMENT EXECUTE FUNCTION authz_epoch_bump_trigger()
    """,
    """
    CREATE CONSTRAINT TRIGGER authz_epoch_ins AFTER INSERT ON solution_workflow_permission_requests
        DEFERRABLE INITIALLY DEFERRED FOR EACH ROW
        EXECUTE FUNCTION authz_epoch_bump_trigger()
    """,
    """
    CREATE CONSTRAINT TRIGGER authz_epoch_upd AFTER UPDATE ON solution_workflow_permission_requests
        DEFERRABLE INITIALLY DEFERRED FOR EACH ROW
        WHEN (to_jsonb(OLD) IS DISTINCT FROM to_jsonb(NEW))
        EXECUTE FUNCTION authz_epoch_bump_trigger()
    """,
    """
    CREATE CONSTRAINT TRIGGER authz_epoch_del AFTER DELETE ON solution_workflow_permission_requests
        DEFERRABLE INITIALLY DEFERRED FOR EACH ROW
        EXECUTE FUNCTION authz_epoch_bump_trigger()
    """,
    """
    CREATE TRIGGER authz_epoch_truncate AFTER TRUNCATE ON solution_workflow_permission_requests
        FOR EACH STATEMENT EXECUTE FUNCTION authz_epoch_bump_trigger()
    """,
    """
    CREATE CONSTRAINT TRIGGER authz_epoch_ins AFTER INSERT ON system_configs
        DEFERRABLE INITIALLY DEFERRED FOR EACH ROW
        WHEN (NEW.category = 'workflow_permissions' AND NEW.key = 'default_mode' AND NEW.organization_id IS NULL)
        EXECUTE FUNCTION authz_epoch_bump_trigger()
    """,
    """
    CREATE CONSTRAINT TRIGGER authz_epoch_upd AFTER UPDATE ON system_configs
        DEFERRABLE INITIALLY DEFERRED FOR EACH ROW
        WHEN (((OLD.category = 'workflow_permissions' AND OLD.key = 'default_mode' AND OLD.organization_id IS NULL) OR (NEW.category = 'workflow_permissions' AND NEW.key = 'default_mode' AND NEW.organization_id IS NULL))
            AND to_jsonb(OLD) IS DISTINCT FROM to_jsonb(NEW))
        EXECUTE FUNCTION authz_epoch_bump_trigger()
    """,
    """
    CREATE CONSTRAINT TRIGGER authz_epoch_del AFTER DELETE ON system_configs
        DEFERRABLE INITIALLY DEFERRED FOR EACH ROW
        WHEN (OLD.category = 'workflow_permissions' AND OLD.key = 'default_mode' AND OLD.organization_id IS NULL)
        EXECUTE FUNCTION authz_epoch_bump_trigger()
    """,
    """
    CREATE TRIGGER authz_epoch_truncate AFTER TRUNCATE ON system_configs
        FOR EACH STATEMENT EXECUTE FUNCTION authz_epoch_bump_trigger()
    """,
    """
    CREATE CONSTRAINT TRIGGER authz_epoch_upd AFTER UPDATE ON solutions
        DEFERRABLE INITIALLY DEFERRED FOR EACH ROW
        WHEN (OLD.id IS DISTINCT FROM NEW.id
            OR OLD.status IS DISTINCT FROM NEW.status)
        EXECUTE FUNCTION authz_epoch_bump_trigger()
    """,
    """
    CREATE CONSTRAINT TRIGGER authz_epoch_del AFTER DELETE ON solutions
        DEFERRABLE INITIALLY DEFERRED FOR EACH ROW
        EXECUTE FUNCTION authz_epoch_bump_trigger()
    """,
    """
    CREATE TRIGGER authz_epoch_truncate AFTER TRUNCATE ON solutions
        FOR EACH STATEMENT EXECUTE FUNCTION authz_epoch_bump_trigger()
    """,
    """
    CREATE CONSTRAINT TRIGGER authz_epoch_upd AFTER UPDATE ON agents
        DEFERRABLE INITIALLY DEFERRED FOR EACH ROW
        WHEN (OLD.id IS DISTINCT FROM NEW.id
            OR OLD.solution_id IS DISTINCT FROM NEW.solution_id)
        EXECUTE FUNCTION authz_epoch_bump_trigger()
    """,
    """
    CREATE CONSTRAINT TRIGGER authz_epoch_del AFTER DELETE ON agents
        DEFERRABLE INITIALLY DEFERRED FOR EACH ROW
        EXECUTE FUNCTION authz_epoch_bump_trigger()
    """,
    """
    CREATE TRIGGER authz_epoch_truncate AFTER TRUNCATE ON agents
        FOR EACH STATEMENT EXECUTE FUNCTION authz_epoch_bump_trigger()
    """,
    """
    CREATE CONSTRAINT TRIGGER authz_epoch_upd AFTER UPDATE ON service_definitions
        DEFERRABLE INITIALLY DEFERRED FOR EACH ROW
        WHEN (OLD.id IS DISTINCT FROM NEW.id
            OR OLD.workflow_id IS DISTINCT FROM NEW.workflow_id
            OR OLD.organization_id IS DISTINCT FROM NEW.organization_id)
        EXECUTE FUNCTION authz_epoch_bump_trigger()
    """,
    """
    CREATE TRIGGER authz_epoch_truncate AFTER TRUNCATE ON service_definitions
        FOR EACH STATEMENT EXECUTE FUNCTION authz_epoch_bump_trigger()
    """,
    """
    CREATE CONSTRAINT TRIGGER authz_epoch_upd AFTER UPDATE ON executions
        DEFERRABLE INITIALLY DEFERRED FOR EACH ROW
        WHEN (OLD.id IS DISTINCT FROM NEW.id
            OR OLD.run_user_id IS DISTINCT FROM NEW.run_user_id
            OR OLD.executed_by IS DISTINCT FROM NEW.executed_by
            OR OLD.workflow_id IS DISTINCT FROM NEW.workflow_id
            OR OLD.organization_id IS DISTINCT FROM NEW.organization_id
            OR OLD.started_by_user_id IS DISTINCT FROM NEW.started_by_user_id
            OR OLD.root_execution_id IS DISTINCT FROM NEW.root_execution_id
            OR OLD.impersonated_by_user_id IS DISTINCT FROM NEW.impersonated_by_user_id
            OR OLD.impersonation_session_id IS DISTINCT FROM NEW.impersonation_session_id)
        EXECUTE FUNCTION authz_epoch_bump_trigger()
    """,
    """
    CREATE TRIGGER authz_epoch_truncate AFTER TRUNCATE ON executions
        FOR EACH STATEMENT EXECUTE FUNCTION authz_epoch_bump_trigger()
    """,
    """
    CREATE CONSTRAINT TRIGGER authz_epoch_upd AFTER UPDATE ON agent_runs
        DEFERRABLE INITIALLY DEFERRED FOR EACH ROW
        WHEN (OLD.id IS DISTINCT FROM NEW.id
            OR OLD.run_user_id IS DISTINCT FROM NEW.run_user_id
            OR OLD.acting_user_id IS DISTINCT FROM NEW.acting_user_id
            OR OLD.org_id IS DISTINCT FROM NEW.org_id
            OR OLD.agent_id IS DISTINCT FROM NEW.agent_id
            OR OLD.impersonated_by_user_id IS DISTINCT FROM NEW.impersonated_by_user_id
            OR OLD.impersonation_session_id IS DISTINCT FROM NEW.impersonation_session_id)
        EXECUTE FUNCTION authz_epoch_bump_trigger()
    """,
    """
    CREATE TRIGGER authz_epoch_truncate AFTER TRUNCATE ON agent_runs
        FOR EACH STATEMENT EXECUTE FUNCTION authz_epoch_bump_trigger()
    """,
    """
    CREATE CONSTRAINT TRIGGER authz_epoch_upd AFTER UPDATE ON service_attempts
        DEFERRABLE INITIALLY DEFERRED FOR EACH ROW
        WHEN (OLD.id IS DISTINCT FROM NEW.id
            OR OLD.run_user_id IS DISTINCT FROM NEW.run_user_id
            OR OLD.service_id IS DISTINCT FROM NEW.service_id)
        EXECUTE FUNCTION authz_epoch_bump_trigger()
    """,
    """
    CREATE TRIGGER authz_epoch_truncate AFTER TRUNCATE ON service_attempts
        FOR EACH STATEMENT EXECUTE FUNCTION authz_epoch_bump_trigger()
    """,
)

# (table, trigger names) for downgrade.
_TRIGGERS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("users", ("authz_epoch_upd", "authz_epoch_del", "authz_epoch_truncate")),
    ("organizations", ("authz_epoch_upd", "authz_epoch_del", "authz_epoch_truncate")),
    ("user_roles", ("authz_epoch_ins", "authz_epoch_upd", "authz_epoch_del", "authz_epoch_truncate")),
    ("user_role_boundaries", ("authz_epoch_ins", "authz_epoch_upd", "authz_epoch_del", "authz_epoch_truncate")),
    ("role_permissions", ("authz_epoch_ins", "authz_epoch_upd", "authz_epoch_del", "authz_epoch_truncate")),
    ("roles", ("authz_epoch_upd", "authz_epoch_del", "authz_epoch_truncate")),
    ("workflows", ("authz_epoch_upd", "authz_epoch_del", "authz_epoch_truncate")),
    ("workflow_permission_grants", ("authz_epoch_ins", "authz_epoch_upd", "authz_epoch_del", "authz_epoch_truncate")),
    ("solution_workflow_permission_requests", ("authz_epoch_ins", "authz_epoch_upd", "authz_epoch_del", "authz_epoch_truncate")),
    ("system_configs", ("authz_epoch_ins", "authz_epoch_upd", "authz_epoch_del", "authz_epoch_truncate")),
    ("solutions", ("authz_epoch_upd", "authz_epoch_del", "authz_epoch_truncate")),
    ("agents", ("authz_epoch_upd", "authz_epoch_del", "authz_epoch_truncate")),
    ("service_definitions", ("authz_epoch_upd", "authz_epoch_truncate")),
    ("executions", ("authz_epoch_upd", "authz_epoch_truncate")),
    ("agent_runs", ("authz_epoch_upd", "authz_epoch_truncate")),
    ("service_attempts", ("authz_epoch_upd", "authz_epoch_truncate")),
)


# The cap on each lock wait and on each statement (see _take_locks).
_LOCK_WAIT = "1s"
# Tables whose column adds need ACCESS EXCLUSIVE.
_COLUMN_TABLES = ("executions", "service_attempts", "execution_workers")
# Every other table that receives epoch triggers.
_TRIGGER_ONLY_TABLES = tuple(table for table, _ in _TRIGGERS if table not in _COLUMN_TABLES)


def _started_on_empty_schema() -> bool:
    """Whether this upgrade run began on an empty schema (a fresh install)."""
    attributes = context.config.attributes
    return _STARTING_REVISION in attributes and attributes[_STARTING_REVISION] is None


def _take_locks() -> tuple[str, str]:
    """Acquire every table lock up front with short, capped waits.

    A queued lock request blocks every later request on that table, so
    product traffic waits for as long as this migration waits plus until it
    commits. Each wait is capped: one ``LOCK TABLE`` per lock mode, each
    bounded by ``statement_timeout`` (all of its tables together) and
    ``lock_timeout`` (each table) of 1 s, and every later statement keeps the
    1 s cap. Worst case, traffic on ``executions``, ``service_attempts`` and
    ``execution_workers`` waits about 2 s (both LOCK statements) and traffic
    writing a trigger table about 1 s, plus the DDL and commit, which wait on
    no other session and take milliseconds (nullable columns, no rewrite).
    A timeout fails the whole upgrade run, which is one transaction: the
    database stays at the prior revision, queued traffic proceeds at once,
    and the operator re-runs it.

    The column tables are locked first, while nothing else is held: a long
    reader on ``executions`` is the likeliest wait, and then only those three
    tables are blocked. Locking the trigger tables first would block
    ``agent_runs`` status writes and ``users`` sign-in writes for that wait
    as well.

    Returns the session's previous ``lock_timeout`` and ``statement_timeout``
    for ``_restore_timeouts``.
    """
    previous = op.get_bind().execute(
        sa.text("SELECT current_setting('lock_timeout'), current_setting('statement_timeout')")
    ).one()
    op.execute(f"SET LOCAL lock_timeout = '{_LOCK_WAIT}'")
    op.execute(f"SET LOCAL statement_timeout = '{_LOCK_WAIT}'")
    op.execute(f"LOCK TABLE {', '.join(_COLUMN_TABLES)} IN ACCESS EXCLUSIVE MODE")
    op.execute(f"LOCK TABLE {', '.join(_TRIGGER_ONLY_TABLES)} IN SHARE ROW EXCLUSIVE MODE")
    return previous[0], previous[1]


def _restore_timeouts(previous: tuple[str, str]) -> None:
    """Restore the exact earlier settings for later migrations in the run."""
    op.get_bind().execute(
        sa.text("SELECT set_config('lock_timeout', :lock, true), set_config('statement_timeout', :statement, true)"),
        {"lock": previous[0], "statement": previous[1]},
    )


def upgrade() -> None:
    previous = _take_locks()
    op.add_column("executions", sa.Column("credential_version", sa.SmallInteger(), nullable=True))
    op.add_column(
        "service_attempts", sa.Column("credential_version", sa.SmallInteger(), nullable=True)
    )
    op.add_column(
        "execution_workers",
        sa.Column("remote_credential_protocol", sa.SmallInteger(), nullable=True),
    )

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

    op.execute(
        """
        CREATE FUNCTION authz_epoch_bump_trigger() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            PERFORM authz_epoch_bump_once();
            RETURN NULL;
        END;
        $$
        """
    )
    for statement in _TRIGGER_STATEMENTS:
        op.execute(statement)

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
    _restore_timeouts(previous)



def downgrade() -> None:
    previous = _take_locks()
    op.drop_column("execution_workers", "remote_credential_protocol")
    op.drop_column("service_attempts", "credential_version")
    op.drop_column("executions", "credential_version")
    op.drop_table("authz_protocol_state")
    for table, names in _TRIGGERS:
        for name in names:
            op.execute(f"DROP TRIGGER {name} ON {table}")
    op.execute("DROP FUNCTION authz_epoch_bump_trigger()")
    op.execute("DROP FUNCTION authz_epoch_bump_once()")
    op.execute("DROP TABLE authz_versions")
    op.execute("DROP FUNCTION authz_versions_protect()")
    _restore_timeouts(previous)
