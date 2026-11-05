"""Every change to a fact an access token carries bumps the authority epoch.

Most cases run inside one transaction that is rolled back: constraint
triggers are set IMMEDIATE so each statement's trigger has fired when the
statement returns, the once-per-transaction flag is cleared before the
measured statement, and the bump count is the number of updates this
transaction made to ``authz_versions`` (``pg_stat_xact_user_tables``). The
commit-path cases at the end commit for real.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID, uuid4

import pytest
from sqlalchemy import delete, text, update
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, AsyncSession

from src.models.enums import AgentAccessLevel, ExecutionStatus
from src.models.orm.agent_runs import AgentRun
from src.models.orm.agents import Agent
from src.models.orm.config import SystemConfig
from src.models.orm.executions import Execution
from src.models.orm.organizations import Organization
from src.models.orm.services import ServiceAttempt, ServiceDefinition
from src.models.orm.solution_workflow_permission_requests import SolutionWorkflowPermissionRequest
from src.models.orm.solutions import Solution
from src.models.orm.users import Role, RolePermission, User, UserRole, UserRoleBoundary
from src.models.orm.workflow_permissions import WorkflowPermissionGrant
from src.models.orm.workflows import Workflow

_EPOCH = text(
    "SELECT version FROM authz_versions "
    "WHERE subject_kind = 'global' AND subject_id = '00000000-0000-0000-0000-000000000000'"
)
_CLEAR_FLAG = text("SELECT set_config('bifrost.authz_epoch_bumped', '', true)")
_EPOCH_UPDATES = text(
    "SELECT COALESCE((SELECT n_tup_upd FROM pg_stat_xact_user_tables "
    "WHERE relname = 'authz_versions'), 0)"
)


@dataclass
class World:
    org: UUID
    other_org: UUID
    user: UUID
    other_user: UUID
    role: UUID
    workflow: UUID
    other_workflow: UUID
    solution: UUID
    agent: UUID
    definition: UUID
    execution: UUID
    agent_run: UUID
    attempt: UUID
    default_config: UUID
    other_config: UUID


class Probe:
    def __init__(self, connection: AsyncConnection, session: AsyncSession) -> None:
        self.connection = connection
        self.session = session

    async def epoch(self) -> int:
        return (await self.connection.execute(_EPOCH)).scalar_one()

    async def epoch_updates(self) -> int:
        return (await self.connection.execute(_EPOCH_UPDATES)).scalar_one()

    async def bumps(self, statement: Any, params: dict[str, Any] | None = None) -> int:
        """Bumps caused by one statement, counted from a cleared flag."""
        await self.connection.execute(_CLEAR_FLAG)
        before = await self.epoch_updates()
        await self.connection.execute(statement, params or {})
        return await self.epoch_updates() - before


@asynccontextmanager
async def _probe(engine: AsyncEngine) -> AsyncIterator[tuple[Probe, World]]:
    async with engine.connect() as connection:
        transaction = await connection.begin()
        try:
            await connection.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
            session = AsyncSession(bind=connection, expire_on_commit=False)
            world = await _seed(session)
            yield Probe(connection, session), world
        finally:
            await transaction.rollback()


async def _seed(session: AsyncSession) -> World:
    suffix = uuid4().hex[:8]
    org = Organization(name=f"Epoch Org {suffix}", created_by="epoch-test")
    other_org = Organization(name=f"Epoch Other {suffix}", created_by="epoch-test")
    session.add_all([org, other_org])
    await session.flush()
    user = User(email=f"epoch-{suffix}@example.test", name="Epoch", organization_id=org.id)
    other_user = User(email=f"epoch-other-{suffix}@example.test", name="Other", organization_id=org.id)
    role = Role(name=f"Epoch Role {suffix}", created_by="epoch-test")
    session.add_all([user, other_user, role])
    await session.flush()
    session.add(UserRole(user_id=user.id, role_id=role.id, assigned_by="epoch-test"))
    await session.flush()
    session.add_all(
        [
            UserRoleBoundary(user_id=user.id, role_id=role.id, kind="organization", organization_id=org.id),
            RolePermission(role_id=role.id, permission="workflows.read"),
        ]
    )
    solution = Solution(slug=f"epoch-{suffix}", name="Epoch Solution", organization_id=org.id)
    session.add(solution)
    await session.flush()
    workflow = Workflow(
        name=f"epoch_{suffix}", function_name=f"epoch_{suffix}", path=f"workflows/epoch_{suffix}.py",
        organization_id=org.id,
    )
    other_workflow = Workflow(
        name=f"epoch_other_{suffix}", function_name=f"epoch_other_{suffix}",
        path=f"workflows/epoch_other_{suffix}.py", organization_id=org.id,
    )
    agent = Agent(
        name=f"epoch_agent_{suffix}", description="Epoch", system_prompt="Epoch", channels=["chat"],
        access_level=AgentAccessLevel.ROLE_BASED, organization_id=org.id, is_active=True,
        knowledge_sources=[], system_tools=[], created_by="epoch-test",
    )
    session.add_all([workflow, other_workflow, agent])
    await session.flush()
    definition = ServiceDefinition(workflow_id=workflow.id, organization_id=org.id, created_by="epoch-test")
    session.add_all(
        [
            definition,
            WorkflowPermissionGrant(workflow_id=workflow.id, permission="secrets.read"),
            SolutionWorkflowPermissionRequest(
                solution_id=solution.id, workflow_id=workflow.id, requested_mode="restricted",
                requested_grants=[], request_digest="0" * 64, status="pending",
            ),
        ]
    )
    await session.flush()
    execution = Execution(
        workflow_name="epoch", executed_by_name="Epoch", workflow_id=workflow.id, run_user_id=user.id,
        executed_by=user.id, organization_id=org.id, status=ExecutionStatus.RUNNING,
        started_by_user_id=user.id, root_execution_id=uuid4(),
    )
    agent_run = AgentRun(
        trigger_type="chat", status="running", org_id=org.id, run_user_id=user.id,
        acting_user_id=user.id, agent_id=agent.id,
    )
    attempt = ServiceAttempt(
        service_id=definition.id, run_user_id=user.id, lease_token="epoch-lease",
        lease_expires_at=datetime.now(timezone.utc) + timedelta(minutes=1), state="running",
    )
    default_config = SystemConfig(
        category="workflow_permissions", key="default_mode", value_json={"mode": "full"},
        organization_id=None,
    )
    other_config = SystemConfig(category="epoch_test", key="unrelated", value_json={"x": 1}, organization_id=None)
    # At most one global default-mode row may exist (a partial unique index).
    await session.execute(
        delete(SystemConfig).where(
            SystemConfig.category == "workflow_permissions",
            SystemConfig.key == "default_mode",
            SystemConfig.organization_id.is_(None),
        )
    )
    session.add_all([execution, agent_run, attempt, default_config, other_config])
    await session.flush()
    return World(
        org=org.id, other_org=other_org.id, user=user.id, other_user=other_user.id, role=role.id,
        workflow=workflow.id, other_workflow=other_workflow.id, solution=solution.id, agent=agent.id,
        definition=definition.id, execution=execution.id, agent_run=agent_run.id, attempt=attempt.id,
        default_config=default_config.id, other_config=other_config.id,
    )


_FREE_DEFAULT_MODE = "DELETE FROM system_configs WHERE id = :default_config; "

# (case id, SQL with :named world fields, expected bumps). A leading
# _FREE_DEFAULT_MODE runs first, unmeasured.
_CASES: list[tuple[str, str, int]] = [
    # users: UPDATE of carried columns, DELETE; never INSERT.
    ("users email", "UPDATE users SET email = 'moved-' || email WHERE id = :user", 1),
    ("users name", "UPDATE users SET name = 'Renamed' WHERE id = :user", 1),
    ("users organization", "UPDATE users SET organization_id = :other_org WHERE id = :user", 1),
    ("users base role", "UPDATE users SET base_role_id = :role WHERE id = :user", 1),
    ("users external", "UPDATE users SET is_external = NOT is_external WHERE id = :user", 1),
    ("users identity kind", "UPDATE users SET identity_kind = 'custom' WHERE id = :user", 1),
    ("users active", "UPDATE users SET is_active = NOT is_active WHERE id = :user", 1),
    ("users last login", "UPDATE users SET last_login = now() WHERE id = :user", 0),
    ("users same value", "UPDATE users SET name = name WHERE id = :user", 0),
    ("users delete", "DELETE FROM users WHERE id = :other_user", 1),
    # organizations: provider flag, DELETE.
    ("organizations provider", "UPDATE organizations SET is_provider = NOT is_provider WHERE id = :other_org", 1),
    ("organizations name", "UPDATE organizations SET name = name || ' renamed' WHERE id = :org", 0),
    ("organizations delete", "DELETE FROM organizations WHERE id = :other_org", 1),
    # user_roles, user_role_boundaries, role_permissions: every event.
    (
        "user_roles insert",
        "INSERT INTO user_roles (user_id, role_id, assigned_by, assigned_at) VALUES (:other_user, :role, 't', now())",
        1,
    ),
    ("user_roles update", "UPDATE user_roles SET assigned_by = 'other' WHERE user_id = :user", 1),
    ("user_roles delete", "DELETE FROM user_roles WHERE user_id = :user", 1),
    (
        "user_role_boundaries insert",
        "INSERT INTO user_role_boundaries (id, user_id, role_id, kind, organization_id) "
        "VALUES (gen_random_uuid(), :user, :role, 'organization', :other_org)",
        1,
    ),
    ("user_role_boundaries update", "UPDATE user_role_boundaries SET organization_id = :other_org WHERE user_id = :user", 1),
    ("user_role_boundaries delete", "DELETE FROM user_role_boundaries WHERE user_id = :user", 1),
    ("role_permissions insert", "INSERT INTO role_permissions (role_id, permission) VALUES (:role, 'secrets.read')", 1),
    ("role_permissions update", "UPDATE role_permissions SET permission = 'secrets.read' WHERE role_id = :role", 1),
    ("role_permissions delete", "DELETE FROM role_permissions WHERE role_id = :role", 1),
    # roles: name, DELETE.
    ("roles name", "UPDATE roles SET name = name || ' renamed' WHERE id = :role", 1),
    ("roles description", "UPDATE roles SET description = 'changed' WHERE id = :role", 0),
    ("roles delete", "DELETE FROM roles WHERE id = :role", 1),
    # workflows: ownership and mode, DELETE.
    ("workflows solution", "UPDATE workflows SET solution_id = :solution WHERE id = :other_workflow", 1),
    ("workflows mode", "UPDATE workflows SET permission_mode = 'restricted' WHERE id = :workflow", 1),
    ("workflows description", "UPDATE workflows SET description = 'changed' WHERE id = :workflow", 0),
    ("workflows delete", "DELETE FROM workflows WHERE id = :other_workflow", 1),
    # workflow_permission_grants and solution_workflow_permission_requests: every event.
    (
        "workflow_permission_grants insert",
        "INSERT INTO workflow_permission_grants (id, workflow_id, permission, created_at) "
        "VALUES (gen_random_uuid(), :workflow, 'workflows.read', now())",
        1,
    ),
    ("workflow_permission_grants update", "UPDATE workflow_permission_grants SET permission = 'x.read' WHERE workflow_id = :workflow", 1),
    ("workflow_permission_grants delete", "DELETE FROM workflow_permission_grants WHERE workflow_id = :workflow", 1),
    (
        "solution_workflow_permission_requests insert",
        "INSERT INTO solution_workflow_permission_requests (id, solution_id, workflow_id, requested_mode, "
        "requested_grants, request_digest, status, created_at, updated_at) VALUES (gen_random_uuid(), "
        ":solution, :other_workflow, 'full', '[]'::jsonb, repeat('1', 64), 'pending', now(), now())",
        1,
    ),
    ("solution_workflow_permission_requests update", "UPDATE solution_workflow_permission_requests SET status = 'approved' WHERE solution_id = :solution", 1),
    ("solution_workflow_permission_requests delete", "DELETE FROM solution_workflow_permission_requests WHERE solution_id = :solution", 1),
    # system_configs: only the platform default workflow mode row.
    (
        "system_configs default insert",
        _FREE_DEFAULT_MODE + "INSERT INTO system_configs (id, category, key, value_json, organization_id, created_at, updated_at) "
        "VALUES (gen_random_uuid(), 'workflow_permissions', 'default_mode', '{\"mode\": \"restricted\"}', NULL, now(), now())",
        1,
    ),
    (
        "system_configs other insert",
        "INSERT INTO system_configs (id, category, key, value_json, organization_id, created_at, updated_at) "
        "VALUES (gen_random_uuid(), 'epoch_test', 'other', '{}', NULL, now(), now())",
        0,
    ),
    (
        "system_configs org-scoped default-mode insert",
        "INSERT INTO system_configs (id, category, key, value_json, organization_id, created_at, updated_at) "
        "VALUES (gen_random_uuid(), 'workflow_permissions', 'default_mode', '{}', :org, now(), now())",
        0,
    ),
    ("system_configs default update", "UPDATE system_configs SET value_json = '{\"mode\": \"restricted\"}' WHERE id = :default_config", 1),
    ("system_configs other update", "UPDATE system_configs SET value_json = '{\"x\": 2}' WHERE id = :other_config", 0),
    ("system_configs moves into default", _FREE_DEFAULT_MODE + "UPDATE system_configs SET category = 'workflow_permissions', key = 'default_mode' WHERE id = :other_config", 1),
    ("system_configs moves out of default", "UPDATE system_configs SET organization_id = :org WHERE id = :default_config", 1),
    ("system_configs default delete", "DELETE FROM system_configs WHERE id = :default_config", 1),
    ("system_configs other delete", "DELETE FROM system_configs WHERE id = :other_config", 0),
    # solutions: status, DELETE.
    ("solutions status", "UPDATE solutions SET status = 'inactive' WHERE id = :solution", 1),
    ("solutions name", "UPDATE solutions SET name = 'Renamed' WHERE id = :solution", 0),
    ("solutions delete", "DELETE FROM solutions WHERE id = :solution", 1),
    # agents: Solution binding, DELETE.
    ("agents solution", "UPDATE agents SET solution_id = :solution WHERE id = :agent", 1),
    ("agents description", "UPDATE agents SET description = 'changed' WHERE id = :agent", 0),
    ("agents delete", "DELETE FROM agents WHERE id = :agent", 1),
    # service_definitions: workflow and organization; DELETE leaves liveness to refuse.
    ("service_definitions workflow", "UPDATE service_definitions SET workflow_id = :other_workflow WHERE id = :definition", 1),
    ("service_definitions organization", "UPDATE service_definitions SET organization_id = :other_org WHERE id = :definition", 1),
    ("service_definitions enabled", "UPDATE service_definitions SET enabled = NOT enabled WHERE id = :definition", 0),
    ("service_definitions delete", "DELETE FROM service_definitions WHERE id = :definition", 0),
    # Run tables: status, state and lease are live reads; carried columns bump.
    ("executions status", "UPDATE executions SET status = 'Success' WHERE id = :execution", 0),
    ("executions run user", "UPDATE executions SET run_user_id = :other_user WHERE id = :execution", 1),
    ("executions actor", "UPDATE executions SET executed_by = :other_user WHERE id = :execution", 1),
    ("executions organization", "UPDATE executions SET organization_id = :other_org WHERE id = :execution", 1),
    ("executions impersonation", "UPDATE executions SET impersonation_session_id = gen_random_uuid() WHERE id = :execution", 1),
    ("executions delete", "DELETE FROM executions WHERE id = :execution", 0),
    ("agent_runs status", "UPDATE agent_runs SET status = 'completed' WHERE id = :agent_run", 0),
    ("agent_runs actor", "UPDATE agent_runs SET acting_user_id = :other_user WHERE id = :agent_run", 1),
    ("agent_runs agent", "UPDATE agent_runs SET agent_id = NULL WHERE id = :agent_run", 1),
    ("service_attempts state", "UPDATE service_attempts SET state = 'stopped' WHERE id = :attempt", 0),
    ("service_attempts lease", "UPDATE service_attempts SET lease_expires_at = now() WHERE id = :attempt", 0),
    ("service_attempts run user", "UPDATE service_attempts SET run_user_id = :other_user WHERE id = :attempt", 1),
]


@pytest.mark.parametrize(("sql", "expected"), [(sql, n) for _, sql, n in _CASES], ids=[c[0] for c in _CASES])
async def test_each_carried_fact_change_bumps_and_others_do_not(
    async_engine: AsyncEngine, sql: str, expected: int
) -> None:
    async with _probe(async_engine) as (probe, world):
        if sql.startswith(_FREE_DEFAULT_MODE):
            await probe.connection.execute(
                text(_FREE_DEFAULT_MODE.rstrip("; ")), {"default_config": world.default_config}
            )
            sql = sql.removeprefix(_FREE_DEFAULT_MODE)
        params = {key: value for key, value in vars(world).items() if f":{key}" in sql}
        before = await probe.epoch()

        assert await probe.bumps(text(sql), params) == expected
        assert (await probe.epoch() != before) is (expected == 1)


async def test_new_entities_and_runs_do_not_bump(async_engine: AsyncEngine) -> None:
    """A new user, organization, role, workflow, Solution, agent or run
    carries no existing token, so inserting one leaves the epoch alone."""
    async with _probe(async_engine) as (probe, world):
        await probe.connection.execute(_CLEAR_FLAG)
        before = await probe.epoch_updates()
        session = probe.session
        suffix = uuid4().hex[:8]
        org = Organization(name=f"Epoch New {suffix}", created_by="epoch-test")
        role = Role(name=f"Epoch New {suffix}", created_by="epoch-test")
        solution = Solution(slug=f"epoch-new-{suffix}", name="New", organization_id=world.org)
        workflow = Workflow(
            name=f"epoch_new_{suffix}", function_name=f"epoch_new_{suffix}",
            path=f"workflows/epoch_new_{suffix}.py", organization_id=world.org,
        )
        session.add_all([org, role, solution, workflow])
        await session.flush()
        user = User(email=f"epoch-new-{suffix}@example.test", name="New", organization_id=org.id)
        agent = Agent(
            name=f"epoch_new_agent_{suffix}", description="New", system_prompt="New", channels=["chat"],
            access_level=AgentAccessLevel.ROLE_BASED, organization_id=org.id, is_active=True,
            knowledge_sources=[], system_tools=[], created_by="epoch-test",
        )
        definition = ServiceDefinition(workflow_id=workflow.id, organization_id=org.id, created_by="epoch-test")
        session.add_all([user, agent, definition])
        await session.flush()
        session.add_all(
            [
                Execution(
                    workflow_name="new", executed_by_name="New", workflow_id=workflow.id, run_user_id=user.id,
                    executed_by=user.id, organization_id=org.id, status=ExecutionStatus.RUNNING,
                    started_by_user_id=user.id, root_execution_id=uuid4(),
                ),
                AgentRun(
                    trigger_type="chat", status="running", org_id=org.id, run_user_id=user.id,
                    acting_user_id=user.id, agent_id=agent.id,
                ),
                ServiceAttempt(
                    service_id=definition.id, run_user_id=user.id, lease_token="new-lease",
                    lease_expires_at=datetime.now(timezone.utc) + timedelta(minutes=1), state="running",
                ),
            ]
        )
        await session.flush()

        assert await probe.epoch_updates() - before == 0


async def test_a_change_made_by_a_before_trigger_is_seen(async_engine: AsyncEngine) -> None:
    """The UPDATE trigger compares final row values, so it sees a column a
    BEFORE trigger changed even when the statement never named it."""
    async with _probe(async_engine) as (probe, world):
        name = f"epoch_rewrite_{uuid4().hex[:8]}"
        await probe.connection.execute(
            text(
                f"CREATE FUNCTION {name}() RETURNS trigger LANGUAGE plpgsql AS $$ "
                "BEGIN NEW.email := 'rewritten-' || NEW.email; RETURN NEW; END; $$"
            )
        )
        await probe.connection.execute(
            text(f"CREATE TRIGGER {name} BEFORE UPDATE ON users FOR EACH ROW EXECUTE FUNCTION {name}()")
        )

        assert await probe.bumps(text("UPDATE users SET last_login = now() WHERE id = :user"), {"user": world.user}) == 1


async def test_a_foreign_key_cascade_bumps_through_the_child_table(async_engine: AsyncEngine) -> None:
    async with _probe(async_engine) as (probe, world):
        # Silence the parent's own trigger so only the cascaded child rows can bump.
        await probe.connection.execute(text("ALTER TABLE roles DISABLE TRIGGER authz_epoch_del"))

        assert await probe.bumps(text("DELETE FROM roles WHERE id = :role"), {"role": world.role}) == 1


async def test_copy_from_bumps(async_engine: AsyncEngine) -> None:
    async with _probe(async_engine) as (probe, world):
        await probe.connection.execute(_CLEAR_FLAG)
        before = await probe.epoch_updates()
        raw = await probe.connection.get_raw_connection()
        await raw.driver_connection.copy_records_to_table(
            "role_permissions", records=[(world.role, "secrets.read")], columns=["role_id", "permission"]
        )

        assert await probe.epoch_updates() - before == 1


async def test_a_generic_orm_update_bumps(async_engine: AsyncEngine) -> None:
    async with _probe(async_engine) as (probe, world):
        await probe.connection.execute(_CLEAR_FLAG)
        before = await probe.epoch_updates()
        values = {"name": "Generic", "is_external": True}
        await probe.session.execute(update(User).where(User.id == world.user).values(**values))

        assert await probe.epoch_updates() - before == 1


async def test_truncate_bumps_immediately(async_engine: AsyncEngine) -> None:
    async with _probe(async_engine) as (probe, _world):
        await probe.connection.execute(text("SET CONSTRAINTS ALL DEFERRED"))

        assert await probe.bumps(text("TRUNCATE workflow_permission_grants")) == 1


async def test_a_ten_thousand_row_update_bumps_once(async_engine: AsyncEngine) -> None:
    async with _probe(async_engine) as (probe, world):
        await probe.connection.execute(
            text(
                "INSERT INTO workflow_permission_grants (id, workflow_id, permission, created_at) "
                "SELECT gen_random_uuid(), :workflow, 'bulk.' || n, now() FROM generate_series(1, 10000) n"
            ),
            {"workflow": world.workflow},
        )
        await probe.connection.execute(text("SET CONSTRAINTS ALL DEFERRED"))
        await probe.connection.execute(_CLEAR_FLAG)
        before = await probe.epoch_updates()

        result = await probe.connection.execute(
            text("UPDATE workflow_permission_grants SET created_by = 'bulk' WHERE workflow_id = :workflow"),
            {"workflow": world.workflow},
        )
        assert result.rowcount == 10001
        # Deferred: nothing has fired yet.
        assert await probe.epoch_updates() == before
        await probe.connection.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))

        assert await probe.epoch_updates() - before == 1


# --- Commit path -----------------------------------------------------------


async def _committed_epoch(engine: AsyncEngine) -> int:
    async with engine.connect() as connection:
        return (await connection.execute(_EPOCH)).scalar_one()


@asynccontextmanager
async def _committed_role(engine: AsyncEngine) -> AsyncIterator[UUID]:
    async with AsyncSession(engine, expire_on_commit=False) as session:
        role = Role(name=f"Epoch Commit {uuid4().hex[:8]}", created_by="epoch-test")
        session.add(role)
        await session.commit()
        role_id = role.id
    try:
        yield role_id
    finally:
        async with AsyncSession(engine) as session:
            await session.execute(delete(Role).where(Role.id == role_id))
            await session.commit()


_GRANT = text("INSERT INTO role_permissions (role_id, permission) VALUES (:role, :permission)")


async def test_the_bump_is_deferred_to_commit(async_engine: AsyncEngine) -> None:
    async with _committed_role(async_engine) as role:
        before = await _committed_epoch(async_engine)
        async with async_engine.begin() as connection:
            await connection.execute(_GRANT, {"role": role, "permission": "secrets.read"})
            assert (await connection.execute(_EPOCH)).scalar_one() == before

        assert await _committed_epoch(async_engine) != before


async def test_immediate_constraints_then_later_changes_bump_once_and_commit_a_new_epoch(
    async_engine: AsyncEngine,
) -> None:
    async with _committed_role(async_engine) as role:
        before = await _committed_epoch(async_engine)
        async with async_engine.begin() as connection:
            start = (await connection.execute(_EPOCH_UPDATES)).scalar_one()
            await connection.execute(_GRANT, {"role": role, "permission": "secrets.read"})
            await connection.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
            await connection.execute(_GRANT, {"role": role, "permission": "workflows.read"})
            await connection.execute(text("UPDATE roles SET name = name || ' later' WHERE id = :role"), {"role": role})
            assert (await connection.execute(_EPOCH_UPDATES)).scalar_one() - start == 1

        assert await _committed_epoch(async_engine) != before


async def test_a_bump_rolled_back_with_its_savepoint_is_redone_at_commit(async_engine: AsyncEngine) -> None:
    async with _committed_role(async_engine) as role:
        before = await _committed_epoch(async_engine)
        async with async_engine.begin() as connection:
            await connection.execute(_GRANT, {"role": role, "permission": "secrets.read"})
            savepoint = await connection.begin_nested()
            # Fire the outer change's deferred event inside the savepoint.
            await connection.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
            assert (await connection.execute(_EPOCH)).scalar_one() != before
            await savepoint.rollback()
            assert (await connection.execute(_EPOCH)).scalar_one() == before

        assert await _committed_epoch(async_engine) != before


async def test_a_rolled_back_change_does_not_bump(async_engine: AsyncEngine) -> None:
    async with _committed_role(async_engine) as role:
        before = await _committed_epoch(async_engine)
        async with async_engine.connect() as connection:
            transaction = await connection.begin()
            await connection.execute(_GRANT, {"role": role, "permission": "secrets.read"})
            await transaction.rollback()

        assert await _committed_epoch(async_engine) == before
