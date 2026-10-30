"""The authority epoch: reads, run-token validation queries and the fact registry.

One global epoch in ``authz_versions`` changes whenever a committed
transaction changes a fact that an access token carries; Postgres triggers
installed by ``alembic/versions/20261030_authz_versions.py`` write it, so no
application write path does. An access token records the epoch it was minted
under (``av``) and is current only while that value still equals the row.
Values are compared for equality only, never ordered.

**Primary only.** The minter and the validator must read the primary
database. Any future read-replica routing must exclude these queries: a
lagging replica would accept a token its primary already refuses.

``AUTHZ_FACTS`` declares, per table, which events change the epoch and which
columns are read live on every request instead. The completeness tests hold
the minter's reads, this registry and the installed triggers equal; change
them together.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal
from uuid import UUID

from sqlalchemy import ColumnElement, and_, false, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.database import get_db_context
from src.models.enums import LIVE_EXECUTION_STATUSES
from src.models.orm.agent_runs import LIVE_AGENT_RUN_STATUSES, AgentRun
from src.models.orm.authz import AuthzVersion
from src.models.orm.executions import Execution
from src.models.orm.services import _SERVICE_LIVE_STATES, ServiceAttempt
from src.services.authorization.runtime import RuntimeKind

GLOBAL_SUBJECT_KIND = "global"
GLOBAL_SUBJECT_ID = UUID("00000000-0000-0000-0000-000000000000")

# The credential protocol this API speaks. 1: validates every runtime token
# from the database and redeems run refresh tokens. 2: also mints runtime
# access tokens that carry their authorization facts (``ver: 1``) and
# validates them against the authority epoch.
CREDENTIAL_PROTOCOL = 2

class AuthorityStateMissing(RuntimeError):
    """The global epoch row is missing; never treat that as a default."""


ANY_COLUMN = "*"


@dataclass(frozen=True)
class FactTable:
    """How changes to one table reach the authority epoch.

    ``update`` names the columns whose change bumps (``ANY_COLUMN`` for every
    column). ``live`` names columns read on every request instead, which
    never bump. ``row_filter`` restricts every event to matching rows.
    """

    table: str
    insert: bool
    update: frozenset[str] | Literal["*"]
    delete: bool
    live: frozenset[str] = frozenset()
    row_filter: str | None = None


_DEFAULT_MODE_ROW = (
    "category = 'workflow_permissions' AND key = 'default_mode' AND organization_id IS NULL"
)

AUTHZ_FACTS: tuple[FactTable, ...] = (
    FactTable(
        "users",
        insert=False,
        update=frozenset(
            {"id", "email", "name", "organization_id", "base_role_id", "is_external", "identity_kind", "is_active"}
        ),
        delete=True,
    ),
    FactTable("organizations", insert=False, update=frozenset({"id", "is_provider"}), delete=True),
    FactTable("user_roles", insert=True, update=ANY_COLUMN, delete=True),
    FactTable("user_role_boundaries", insert=True, update=ANY_COLUMN, delete=True),
    FactTable("role_permissions", insert=True, update=ANY_COLUMN, delete=True),
    FactTable("roles", insert=False, update=frozenset({"id", "name"}), delete=True),
    FactTable("workflows", insert=False, update=frozenset({"id", "solution_id", "permission_mode"}), delete=True),
    FactTable("workflow_permission_grants", insert=True, update=ANY_COLUMN, delete=True),
    FactTable("solution_workflow_permission_requests", insert=True, update=ANY_COLUMN, delete=True),
    FactTable("system_configs", insert=True, update=ANY_COLUMN, delete=True, row_filter=_DEFAULT_MODE_ROW),
    FactTable("solutions", insert=False, update=frozenset({"id", "status"}), delete=True),
    FactTable("agents", insert=False, update=frozenset({"id", "solution_id"}), delete=True),
    # Deleting a definition deletes its attempts; liveness then refuses.
    FactTable(
        "service_definitions", insert=False, update=frozenset({"id", "workflow_id", "organization_id"}), delete=False
    ),
    # Runs: carried columns are fixed at creation; a deleted or ended run
    # fails the liveness read.
    FactTable(
        "executions",
        insert=False,
        update=frozenset(
            {
                "id",
                "run_user_id",
                "executed_by",
                "workflow_id",
                "organization_id",
                "started_by_user_id",
                "root_execution_id",
                "impersonated_by_user_id",
                "impersonation_session_id",
            }
        ),
        delete=False,
        live=frozenset({"status"}),
    ),
    FactTable(
        "agent_runs",
        insert=False,
        update=frozenset(
            {"id", "run_user_id", "acting_user_id", "org_id", "agent_id", "impersonated_by_user_id", "impersonation_session_id"}
        ),
        delete=False,
        live=frozenset({"status"}),
    ),
    FactTable(
        "service_attempts",
        insert=False,
        update=frozenset({"id", "run_user_id", "service_id"}),
        delete=False,
        live=frozenset({"state", "lease_expires_at"}),
    ),
)


def _global_epoch_row() -> tuple[ColumnElement[bool], ColumnElement[bool]]:
    return (
        AuthzVersion.subject_kind == GLOBAL_SUBJECT_KIND,
        AuthzVersion.subject_id == GLOBAL_SUBJECT_ID,
    )


async def read_epoch(db: AsyncSession) -> int:
    """The current global epoch, read in the caller's transaction."""
    epoch = (await db.execute(select(AuthzVersion.version).where(*_global_epoch_row()))).scalar_one_or_none()
    if epoch is None:
        raise AuthorityStateMissing("The global authority epoch row is missing.")
    return epoch


@dataclass(frozen=True)
class AuthorityState:
    """One request's view of the epoch and its run. ``epoch`` is None when
    the epoch row is missing."""

    epoch: int | None
    live: bool


async def read_authority_state(
    db: AsyncSession,
    kind: RuntimeKind,
    credential_id: UUID,
    *,
    service_id: UUID | None,
    now: datetime,
) -> AuthorityState:
    """The epoch and the run's liveness in one statement.

    ``now`` is the validating host's clock, which the service lease is
    compared against, as the database-backed path does today.
    """
    if kind == "workflow_execution":
        run = Execution
        live = Execution.status.in_(LIVE_EXECUTION_STATUSES)
        join = Execution.id == credential_id
    elif kind == "agent_run":
        run = AgentRun
        live = AgentRun.status.in_(LIVE_AGENT_RUN_STATUSES)
        join = AgentRun.id == credential_id
    elif kind == "service_attempt":
        run = ServiceAttempt
        live = (
            false()
            if service_id is None
            else and_(
                ServiceAttempt.state.in_(_SERVICE_LIVE_STATES),
                ServiceAttempt.lease_expires_at > now,
                ServiceAttempt.service_id == service_id,
            )
        )
        join = ServiceAttempt.id == credential_id
    else:
        raise ValueError(f"Unknown runtime kind: {kind!r}")
    row = (
        await db.execute(
            select(AuthzVersion.version, live.label("live"))
            .select_from(AuthzVersion)
            .outerjoin(run, join)
            .where(*_global_epoch_row())
        )
    ).one_or_none()
    if row is None:
        return AuthorityState(epoch=None, live=False)
    return AuthorityState(epoch=row.version, live=row.live is True)


async def record_protocol_start() -> None:
    """Durably record that an API speaking this credential protocol started.

    Runs at API startup. Later migrations require this evidence before
    switching token formats; the first start's time is kept.
    """
    async with get_db_context() as db:
        await db.execute(
            text(
                "INSERT INTO authz_protocol_state (protocol, first_started_at) "
                "VALUES (:protocol, now()) ON CONFLICT (protocol) DO NOTHING"
            ),
            {"protocol": CREDENTIAL_PROTOCOL},
        )
