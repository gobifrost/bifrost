"""Committed runs and their authority facts, for tests of the token minter.

The minter reads in its own transaction, so it sees only committed rows.
``committed_world`` commits what a test builds and deletes it afterwards, in
reverse creation order (children before parents), with Core statements.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from shared.builtin_roles import PLATFORM_ADMIN_ROLE_ID
from src.models.enums import AgentAccessLevel, ExecutionStatus
from src.models.orm.agent_runs import AgentRun
from src.models.orm.agents import Agent
from src.models.orm.executions import Execution
from src.models.orm.organizations import Organization
from src.models.orm.services import ServiceAttempt, ServiceDefinition
from src.models.orm.solutions import Solution
from src.models.orm.users import Role, RolePermission, User, UserRole, UserRoleBoundary
from src.models.orm.workflow_permissions import WorkflowPermissionGrant
from src.models.orm.workflows import Workflow


class World:
    """Builds rows in one session; the caller commits."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self._created: list[tuple[Any, UUID]] = []

    async def _add(self, row: Any) -> Any:
        self.session.add(row)
        await self.session.flush()
        self._created.append((type(row), row.id))
        return row

    async def organization(self, *, provider: bool = False) -> Organization:
        return await self._add(
            Organization(name=f"Token World {uuid4().hex[:8]}", created_by="token-test", is_provider=provider)
        )

    async def person(self, organization: Organization, *, external: bool = False) -> User:
        return await self._add(
            User(
                email=f"token-{uuid4().hex[:8]}@example.test",
                name=f"Person {uuid4().hex[:4]}",
                organization_id=organization.id,
                is_external=external,
            )
        )

    async def role(self, *permissions: str) -> Role:
        role = await self._add(Role(name=f"Token Role {uuid4().hex[:8]}", created_by="token-test"))
        self.session.add_all([RolePermission(role_id=role.id, permission=p) for p in permissions])
        await self.session.flush()
        return role

    async def assign(
        self, user: User, role_id: UUID, *boundaries: tuple[str, UUID | None]
    ) -> None:
        """Hold a role at boundaries; no boundary holds it unbounded."""
        self.session.add(UserRole(user_id=user.id, role_id=role_id, assigned_by="token-test"))
        await self.session.flush()
        self.session.add_all(
            [
                UserRoleBoundary(user_id=user.id, role_id=role_id, kind=kind, organization_id=organization)
                for kind, organization in boundaries
            ]
        )
        await self.session.flush()

    async def platform_admin(self, user: User) -> None:
        await self.assign(user, PLATFORM_ADMIN_ROLE_ID, ("platform", None))

    async def solution(self, organization: Organization, *, status: str = "active") -> Solution:
        return await self._add(
            Solution(slug=f"token-{uuid4().hex[:8]}", name="Token", organization_id=organization.id, status=status)
        )

    async def workflow(
        self, organization: Organization, *, grants: tuple[str, ...] = (), **fields: Any
    ) -> Workflow:
        name = f"token_{uuid4().hex[:8]}"
        workflow = await self._add(
            Workflow(name=name, function_name=name, path=f"workflows/{name}.py", organization_id=organization.id, **fields)
        )
        self.session.add_all([WorkflowPermissionGrant(workflow_id=workflow.id, permission=p) for p in grants])
        await self.session.flush()
        return workflow

    async def execution(
        self,
        *,
        run_user: User,
        actor: User | None = None,
        workflow: Workflow | None = None,
        organization: Organization,
        status: ExecutionStatus = ExecutionStatus.RUNNING,
        **fields: Any,
    ) -> Execution:
        actor = run_user if actor is None else actor
        return await self._add(
            Execution(
                workflow_name="token",
                executed_by_name="Token",
                workflow_id=None if workflow is None else workflow.id,
                run_user_id=run_user.id,
                executed_by=actor.id,
                organization_id=organization.id,
                status=status,
                started_by_user_id=actor.id,
                root_execution_id=uuid4(),
                **fields,
            )
        )

    async def service_attempt(
        self, *, run_user: User, workflow: Workflow, organization: Organization, lease: timedelta = timedelta(minutes=5)
    ) -> ServiceAttempt:
        definition = await self._add(
            ServiceDefinition(workflow_id=workflow.id, organization_id=organization.id, created_by="token-test")
        )
        return await self._add(
            ServiceAttempt(
                service_id=definition.id,
                run_user_id=run_user.id,
                lease_token="token-lease",
                lease_expires_at=datetime.now(timezone.utc) + lease,
                state="running",
            )
        )

    async def agent(self, organization: Organization, *, solution: Solution | None = None) -> Agent:
        return await self._add(
            Agent(
                name=f"token_agent_{uuid4().hex[:8]}",
                description="Token",
                system_prompt="Token",
                channels=["chat"],
                access_level=AgentAccessLevel.ROLE_BASED,
                organization_id=organization.id,
                is_active=True,
                knowledge_sources=[],
                system_tools=[],
                created_by="token-test",
                solution_id=None if solution is None else solution.id,
            )
        )

    async def agent_run(
        self, *, run_user: User, actor: User, organization: Organization, agent: Agent | None = None, **fields: Any
    ) -> AgentRun:
        return await self._add(
            AgentRun(
                trigger_type="chat",
                status="running",
                org_id=organization.id,
                run_user_id=run_user.id,
                acting_user_id=actor.id,
                agent_id=None if agent is None else agent.id,
                **fields,
            )
        )

    async def cleanup(self, engine: AsyncEngine) -> None:
        async with AsyncSession(engine) as session:
            for model, row_id in reversed(self._created):
                await session.execute(delete(model).where(model.id == row_id))
            await session.commit()


@asynccontextmanager
async def committed_world(engine: AsyncEngine) -> AsyncIterator[World]:
    """A ``World`` whose session the test commits; its rows are removed after."""
    async with AsyncSession(engine, expire_on_commit=False) as session:
        world = World(session)
        try:
            yield world
        finally:
            await session.rollback()
            await world.cleanup(engine)
