"""Run lineage: who each run is for, decided once where the run starts.

Every execution records its run user (who the run is for), who started its
run tree, and the tree's first execution. One rule per way a run starts:

| Start                                              | Run user and starter                                   | Root            |
|----------------------------------------------------|--------------------------------------------------------|-----------------|
| A person (REST, CLI, web SDK, app, form, data      | that person (an admin's `run_as` changes the acting    | its own id      |
| provider, MCP, chat tool, integration code run)     | user, not the run user)                                |                 |
| A child run (engine token)                         | copied from the parent execution                       | the parent's    |
| Schedule, webhook, topic, endpoint key             | the workflow's identity, else its organization's       | its own id      |
|                                                    | default identity (global workflow: global identity)    |                 |
| Anonymous or embedded form/app, service            | the default identity of the principal's organization   | its own id      |
| Agent tool call                                    | the agent run's run user                               | its own id      |

A child whose parent recorded no lineage (started before lineage was
recorded) records none. Nothing decides on lineage yet; it is attribution.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.principal import UserPrincipal
from src.models.enums import IdentityKind
from src.models.orm.executions import Execution
from src.models.orm.users import User
from src.models.orm.workflows import Workflow

_COLUMNS = ("run_user_id", "started_by_user_id", "root_execution_id")


@dataclass(frozen=True)
class RunLineage:
    run_user_id: UUID
    started_by_user_id: UUID
    root_execution_id: UUID | None  # None: this run is its tree's root

    def bound(self, execution_id: UUID | str) -> dict[str, str]:
        """The lineage of the execution `execution_id`, as stored on its pending record."""
        root = self.root_execution_id if self.root_execution_id is not None else execution_id
        return {
            "run_user_id": str(self.run_user_id),
            "started_by_user_id": str(self.started_by_user_id),
            "root_execution_id": str(root),
        }


def lineage_columns(bound: dict[str, str] | None) -> dict[str, UUID | None]:
    """Execution row values for a bound lineage; all None when none was recorded."""
    if bound is None:
        return dict.fromkeys(_COLUMNS)
    return {column: UUID(bound[column]) for column in _COLUMNS}


def person_lineage(user_id: UUID | str) -> RunLineage:
    person = UUID(str(user_id))
    return RunLineage(run_user_id=person, started_by_user_id=person, root_execution_id=None)


def run_user_lineage(run_user_id: UUID | None) -> RunLineage | None:
    """A run started by an agent run for its run user; None when unrecorded."""
    if run_user_id is None:
        return None
    return RunLineage(run_user_id=run_user_id, started_by_user_id=run_user_id, root_execution_id=None)


async def identity_lineage(session: AsyncSession, organization_id: UUID | str | None) -> RunLineage:
    """The organization's default identity; the global identity when there is no organization."""
    query = (
        select(User.id).where(User.identity_kind == IdentityKind.GLOBAL_DEFAULT)
        if organization_id is None
        else select(User.id).where(
            User.identity_kind == IdentityKind.ORG_DEFAULT,
            User.organization_id == UUID(str(organization_id)),
        )
    )
    identity = (await session.execute(query)).scalar_one()
    return RunLineage(run_user_id=identity, started_by_user_id=identity, root_execution_id=None)


async def unattended_lineage(session: AsyncSession, workflow_id: UUID | str) -> RunLineage:
    """A run no person started: the workflow's identity, else its organization's default."""
    organization_id, run_identity_id = (
        await session.execute(
            select(Workflow.organization_id, Workflow.run_identity_id).where(Workflow.id == UUID(str(workflow_id)))
        )
    ).one()
    if run_identity_id is not None:
        return RunLineage(run_user_id=run_identity_id, started_by_user_id=run_identity_id, root_execution_id=None)
    return await identity_lineage(session, organization_id)


async def child_lineage(session: AsyncSession, parent_execution_id: UUID | str) -> RunLineage | None:
    """The parent execution's lineage; None when the parent recorded none."""
    row = (
        await session.execute(
            select(Execution.run_user_id, Execution.started_by_user_id, Execution.root_execution_id).where(
                Execution.id == UUID(str(parent_execution_id))
            )
        )
    ).one_or_none()
    if row is None or None in tuple(row):
        return None
    run_user_id, started_by_user_id, root_execution_id = row
    return RunLineage(
        run_user_id=run_user_id, started_by_user_id=started_by_user_id, root_execution_id=root_execution_id
    )


async def principal_lineage(session: AsyncSession, principal: UserPrincipal) -> RunLineage | None:
    """Lineage of a run started by an authenticated caller."""
    if principal.service_id is not None or principal.embed:
        return await identity_lineage(session, principal.organization_id)
    if principal.engine_execution_id is not None:
        return await child_lineage(session, principal.engine_execution_id)
    return person_lineage(principal.user_id)
