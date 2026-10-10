"""Judging a request's notes and writing ``access.check`` audit events."""

from __future__ import annotations

import asyncio
import logging
from unittest.mock import patch
from uuid import UUID, uuid4

import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from shared.access_checks import Collector, Note
from shared.identities import ensure_default_identity
from src.core.cache.redis_client import close_shared_redis
from src.core.constants import PROVIDER_ORG_ID
from src.models.enums import IdentityKind
from src.models.orm.audit import AuditLog
from src.models.orm.organizations import Organization
from src.models.orm.users import Role, RolePermission, User, UserRole, UserRoleBoundary
from src.services.access_check_writer import flush, flush_detached

ROUTE = {"operation": "POST /api/tables/{name}/documents", "route": ("POST", "/api/tables/{name}/documents")}


@pytest_asyncio.fixture(autouse=True)
async def _fresh_redis_client(monkeypatch):
    # The shared client binds to the event loop that created it; each test
    # runs in its own loop, so it starts without one (an earlier test's client
    # belongs to a closed loop) and closes the one it made.
    from src.core.cache import redis_client

    monkeypatch.setattr(redis_client, "_shared_client", None)
    yield
    await close_shared_redis()


@pytest_asyncio.fixture
async def db_session(async_engine):
    async with async_engine.connect() as connection:
        transaction = await connection.begin()
        async with AsyncSession(
            bind=connection, expire_on_commit=False, join_transaction_mode="create_savepoint"
        ) as session:
            try:
                yield session
            finally:
                await session.rollback()
                await transaction.rollback()


async def _org(session: AsyncSession) -> Organization:
    org = Organization(name=f"Check Org {uuid4().hex[:8]}", created_by="access-check-test")
    session.add(org)
    await session.flush()
    await ensure_default_identity(session, org)
    return org


async def _person(session: AsyncSession, org: Organization) -> User:
    person = User(email=f"{uuid4()}@check.example", name="Person", organization_id=org.id)
    session.add(person)
    await session.flush()
    return person


async def _provider_identity(session: AsyncSession) -> UUID:
    return (
        await session.execute(
            select(User.id).where(
                User.identity_kind == IdentityKind.ORG_DEFAULT, User.organization_id == PROVIDER_ORG_ID
            )
        )
    ).scalar_one()


def _collector(run_user_id: UUID | None, *notes: Note) -> Collector:
    return Collector(execution_id=uuid4(), run_user_id=run_user_id, workflow_id=None, notes=list(notes))


async def _rows(session: AsyncSession, collector: Collector) -> list[AuditLog]:
    return list(
        (
            await session.execute(
                select(AuditLog).where(AuditLog.execution_id == collector.execution_id).order_by(AuditLog.action)
            )
        ).scalars()
    )


async def test_a_target_outside_reach_is_written_as_a_failure(db_session: AsyncSession) -> None:
    home, other = await _org(db_session), await _org(db_session)
    person = await _person(db_session, home)
    collector = _collector(person.id, Note("scope_switch", other.id))

    await flush(db_session, collector, **ROUTE)

    [row] = await _rows(db_session, collector)
    assert (row.action, row.outcome, row.resource_type) == ("access.check", "failure", "scope_switch")
    assert row.organization_id == other.id
    assert row.user_id == person.id
    assert row.operation_id == ROUTE["operation"]
    assert row.details is not None
    assert row.details["enforced"] is False
    assert row.details["trace"]["steps"][2]["status"] == "stopped"
    assert row.details["inputs"]["target"] == str(other.id)
    assert row.details["today"] == "allowed"
    assert collector.closed is True


async def test_home_and_global_successes_write_nothing(db_session: AsyncSession) -> None:
    home = await _org(db_session)
    person = await _person(db_session, home)
    collector = _collector(person.id, Note("scope_switch", home.id), Note("scope_switch", None))

    await flush(db_session, collector, **ROUTE)

    assert await _rows(db_session, collector) == []


async def test_a_cross_org_success_is_written(db_session: AsyncSession) -> None:
    customer = await _org(db_session)
    collector = _collector(await _provider_identity(db_session), Note("child_run", customer.id))

    await flush(db_session, collector, **ROUTE)

    [row] = await _rows(db_session, collector)
    assert (row.outcome, row.resource_type) == ("success", "child_run")


async def test_powers_and_policies_are_written_when_allowed(db_session: AsyncSession) -> None:
    home = await _org(db_session)
    person, colleague = await _person(db_session, home), await _person(db_session, home)
    collector = _collector(
        person.id,
        Note("run_as", home.id, {"run_as_user_id": str(colleague.id)}),
        Note("secret", home.id, {"kind": "config", "name": "api_key"}),
        Note("policy", home.id, {"today": True, "model": True, "missing": [], "table": "t"}),
    )

    await flush(db_session, collector, **ROUTE)

    assert sorted(row.resource_type or "" for row in await _rows(db_session, collector)) == [
        "policy",
        "run_as",
        "secret",
    ]


async def test_a_decision_is_written_once_per_run(db_session: AsyncSession) -> None:
    home, other = await _org(db_session), await _org(db_session)
    person = await _person(db_session, home)
    collector = _collector(person.id, Note("scope_switch", other.id), Note("scope_switch", other.id))
    again = Collector(collector.execution_id, person.id, None, [Note("scope_switch", other.id)])

    await flush(db_session, collector, **ROUTE)
    await flush(db_session, again, **ROUTE)

    assert len(await _rows(db_session, collector)) == 1


async def test_missing_lineage_is_a_coverage_gap(db_session: AsyncSession) -> None:
    collector = _collector(None, Note("scope_switch", uuid4()), Note("secret", None, {"kind": "config", "name": "k"}))

    await flush(db_session, collector, **ROUTE)

    rows = await _rows(db_session, collector)
    assert {(row.action, row.resource_type) for row in rows} == {
        ("access.check_gap", "scope_switch"),
        ("access.check_gap", "secret"),
    }
    assert all(row.details == {"reason": "missing_lineage", "operation": ROUTE["operation"]} for row in rows)


async def test_a_run_as_user_that_no_longer_exists_is_a_coverage_gap(db_session: AsyncSession) -> None:
    home = await _org(db_session)
    person = await _person(db_session, home)
    collector = _collector(person.id, Note("run_as", home.id, {"run_as_user_id": str(uuid4())}))

    await flush(db_session, collector, **ROUTE)

    [row] = await _rows(db_session, collector)
    assert (row.action, row.resource_type) == ("access.check_gap", "run_as")
    assert row.details is not None and row.details["reason"] == "run_as_user_missing"


async def test_a_run_user_that_no_longer_exists_is_a_coverage_gap(db_session: AsyncSession) -> None:
    collector = _collector(uuid4(), Note("scope_switch", uuid4()))

    await flush(db_session, collector, **ROUTE)

    [row] = await _rows(db_session, collector)
    assert row.action == "access.check_gap"
    assert row.details is not None and row.details["reason"] == "run_user_missing"


async def test_a_failing_check_is_a_gap_and_never_raises(db_session: AsyncSession) -> None:
    home = await _org(db_session)
    person = await _person(db_session, home)
    collector = _collector(person.id, Note("policy", home.id, {"today": True}))  # model/missing absent

    await flush(db_session, collector, **ROUTE)

    [row] = await _rows(db_session, collector)
    assert row.action == "access.check_gap"
    assert row.details is not None and row.details["reason"] == "observer_error:KeyError"


async def test_without_redis_nothing_is_written_and_nothing_raises(db_session: AsyncSession, caplog) -> None:
    home, other = await _org(db_session), await _org(db_session)
    person = await _person(db_session, home)
    collector = _collector(person.id, Note("scope_switch", other.id))

    async def unavailable():
        raise ConnectionError("redis down")

    with patch("src.services.access_check_writer.get_shared_redis", unavailable), caplog.at_level(logging.WARNING):
        await flush(db_session, collector, **ROUTE)

    assert await _rows(db_session, collector) == []
    assert "access checks not written" in caplog.text


async def test_a_check_that_could_not_be_computed_is_a_gap(db_session: AsyncSession) -> None:
    home = await _org(db_session)
    person = await _person(db_session, home)
    collector = _collector(person.id, Note("entry", home.id, {"gap": "observer_error:TimeoutError"}))

    await flush(db_session, collector, **ROUTE)

    [row] = await _rows(db_session, collector)
    assert (row.action, row.resource_type, row.user_id) == ("access.check_gap", "entry", person.id)
    assert row.details == {"reason": "observer_error:TimeoutError", "operation": ROUTE["operation"]}


async def test_a_denial_that_also_happens_today_is_not_written(db_session: AsyncSession) -> None:
    home = await _org(db_session)
    person = await _person(db_session, home)
    collector = _collector(
        person.id, Note("policy", home.id, {"today": False, "model": False, "missing": ["role:HR"], "table": "t"})
    )

    await flush(db_session, collector, **ROUTE)

    assert await _rows(db_session, collector) == []


async def test_each_table_and_secret_is_written_once_per_run(db_session: AsyncSession) -> None:
    home = await _org(db_session)
    person = await _person(db_session, home)
    collector = _collector(
        person.id,
        Note("policy", home.id, {"today": True, "model": True, "missing": [], "table": "a"}),
        Note("policy", home.id, {"today": True, "model": True, "missing": [], "table": "b"}),
        Note("secret", home.id, {"kind": "config", "name": "one"}),
        Note("secret", home.id, {"kind": "config", "name": "two"}),
        Note("secret", home.id, {"kind": "config", "name": "one"}),
    )

    await flush(db_session, collector, **ROUTE)

    assert sorted((r.resource_type, r.details["inputs"].get("table") or r.details["inputs"]["name"]) for r in await _rows(db_session, collector)) == [
        ("policy", "a"),
        ("policy", "b"),
        ("secret", "one"),
        ("secret", "two"),
    ]


def _person_collector(person_id: UUID, *notes: Note) -> Collector:
    return Collector(execution_id=None, run_user_id=person_id, workflow_id=None, notes=list(notes), direct=True)


def _power(permission: str, target: UUID | None, subject: str = "agent:1") -> Note:
    return Note("permission", target, {"permission": permission, "subject": subject})


async def _person_rows(session: AsyncSession, person_id: UUID) -> list[AuditLog]:
    return list((await session.execute(select(AuditLog).where(AuditLog.user_id == person_id))).scalars())


async def test_a_persons_elevated_branch_without_the_permission_is_a_would_deny(db_session: AsyncSession) -> None:
    home, other = await _org(db_session), await _org(db_session)
    person = await _person(db_session, home)
    collector = _person_collector(person.id, _power("agents.read", other.id))

    await flush(db_session, collector, **ROUTE)

    [row] = await _person_rows(db_session, person.id)
    assert (row.action, row.outcome, row.resource_type) == ("access.check", "failure", "permission")
    assert row.organization_id == other.id and row.execution_id is None
    assert row.details is not None
    assert (row.details["enforced"], row.details["direct"]) == (False, True)
    assert row.details["inputs"]["permission"] == "agents.read"
    assert row.details["trace"]["steps"][-1]["facts"] == {
        "permission": "agents.read",
        "permission_display_name": "Read Agents",
    }


async def test_a_permission_the_person_holds_writes_nothing(db_session: AsyncSession) -> None:
    home = await _org(db_session)
    person = await _person(db_session, home)
    collector = _person_collector(person.id, _power("workflows.execute", home.id, "workflow:1"))

    await flush(db_session, collector, **ROUTE)

    assert await _person_rows(db_session, person.id) == []


async def test_a_persons_decision_is_written_once_a_day(db_session: AsyncSession) -> None:
    home = await _org(db_session)
    person = await _person(db_session, home)

    for _ in range(2):
        await flush(db_session, _person_collector(person.id, _power("agents.read", home.id)), **ROUTE)
    await flush(db_session, _person_collector(person.id, _power("agents.read", home.id, "agent:2")), **ROUTE)

    assert sorted(row.details["inputs"]["subject"] for row in await _person_rows(db_session, person.id)) == [
        "agent:1",
        "agent:2",
    ]


async def test_a_full_runs_permission_is_held(db_session: AsyncSession) -> None:
    home = await _org(db_session)
    person = await _person(db_session, home)
    collector = _collector(person.id, _power("agents.read", home.id))

    await flush(db_session, collector, **ROUTE)

    assert await _rows(db_session, collector) == []


async def _impersonator(session: AsyncSession, home: Organization, at: Organization) -> User:
    """A person in ``home`` holding Impersonate Users at ``at``."""
    person = await _person(session, home)
    role = Role(name=f"Impersonators {uuid4().hex[:8]}", created_by="access-check-test")
    session.add(role)
    await session.flush()
    session.add_all(
        [
            RolePermission(role_id=role.id, permission="users.impersonate"),
            UserRole(user_id=person.id, role_id=role.id, assigned_by="access-check-test"),
        ]
    )
    await session.flush()
    session.add(UserRoleBoundary(user_id=person.id, role_id=role.id, kind="organization", organization_id=at.id))
    await session.flush()
    return person


def _run_as(user: User) -> Note:
    return Note("run_as", user.organization_id, {"run_as_user_id": str(user.id), "enforced": True})


async def test_a_persons_run_as_is_written_enforced_in_both_outcomes(db_session: AsyncSession) -> None:
    provider, contoso, fabrikam = await _org(db_session), await _org(db_session), await _org(db_session)
    person = await _impersonator(db_session, provider, contoso)
    in_contoso, in_fabrikam = await _person(db_session, contoso), await _person(db_session, fabrikam)

    await flush(db_session, _person_collector(person.id, _run_as(in_contoso), _run_as(in_fabrikam)), **ROUTE)

    rows = {row.organization_id: row for row in await _person_rows(db_session, person.id)}
    outcomes = {org: row.outcome for org, row in rows.items()}
    assert outcomes == {contoso.id: "success", fabrikam.id: "failure"}
    for row in rows.values():
        assert row.details is not None
        assert (row.resource_type, row.details["enforced"], row.details["direct"]) == ("run_as", True, True)
        assert row.details["trace"]["enforced"] is True
    failure = rows[fabrikam.id].details
    assert failure is not None
    stopped = [step["key"] for step in failure["trace"]["steps"] if step["status"] == "stopped"]
    assert stopped == ["target"]


async def test_a_runs_run_as_is_written_report_only(db_session: AsyncSession) -> None:
    home = await _org(db_session)
    person, colleague = await _person(db_session, home), await _person(db_session, home)
    collector = _collector(person.id, Note("run_as", home.id, {"run_as_user_id": str(colleague.id)}))

    await flush(db_session, collector, **ROUTE)

    [row] = await _rows(db_session, collector)
    assert (row.resource_type, row.outcome) == ("run_as", "success")
    assert row.details is not None
    assert (row.details["enforced"], row.details["trace"]["enforced"]) == (False, False)


async def test_every_enforced_run_as_is_written_and_a_report_only_one_once(db_session: AsyncSession) -> None:
    provider, contoso = await _org(db_session), await _org(db_session)
    person = await _impersonator(db_session, provider, contoso)
    in_contoso = await _person(db_session, contoso)
    execution_id = uuid4()

    for _ in range(2):
        await flush(db_session, _person_collector(person.id, _run_as(in_contoso)), **ROUTE)
        report_only = Note("run_as", contoso.id, {"run_as_user_id": str(in_contoso.id)})
        collector = Collector(execution_id=execution_id, run_user_id=person.id, workflow_id=None, notes=[report_only])
        await flush(db_session, collector, **ROUTE)

    rows = await _person_rows(db_session, person.id)
    enforced = sorted(row.details["enforced"] for row in rows if row.details is not None)
    assert enforced == [False, True, True]


async def test_nothing_noted_opens_no_session() -> None:
    collector = _person_collector(uuid4())

    def no_session():
        raise AssertionError("a session was opened")

    with patch("src.services.access_check_writer.get_db_context", no_session):
        await flush_detached(collector, operation="GET /api/agents", route=("GET", "/api/agents"))

    assert collector.closed is True


async def test_an_execution_noted_unloaded_is_judged_in_its_own_org(db_session: AsyncSession) -> None:
    from src.models.enums import ExecutionStatus
    from src.models.orm import Execution

    home, other = await _org(db_session), await _org(db_session)
    person, someone = await _person(db_session, home), await _person(db_session, other)
    theirs = Execution(
        workflow_name="w", status=ExecutionStatus.SUCCESS, organization_id=other.id, executed_by=someone.id, executed_by_name="S"
    )
    own = Execution(
        workflow_name="w", status=ExecutionStatus.SUCCESS, organization_id=other.id, executed_by=person.id, executed_by_name="P"
    )
    db_session.add_all([theirs, own])
    await db_session.flush()

    def owned(execution: Execution) -> Note:
        return Note(
            "permission",
            None,
            {
                "permission": "executions.read.all",
                "subject": f"execution:{execution.id}",
                "owned": "execution",
                "object_id": str(execution.id),
                "actor": str(person.id),
            },
        )

    await flush(db_session, _person_collector(person.id, owned(theirs), owned(own)), **ROUTE)

    [row] = await _person_rows(db_session, person.id)
    assert row.organization_id == other.id
    assert row.details is not None
    assert row.details["inputs"]["subject"] == f"execution:{theirs.id}"
    assert "owned" not in row.details["inputs"]


async def test_a_note_judged_today_is_not_judged_again(db_session: AsyncSession) -> None:
    from src.services import access_check_writer

    home = await _org(db_session)
    person = await _person(db_session, home)
    loads: list[UUID] = []
    real = access_check_writer.load_run_user

    async def counting(db, user_id):
        loads.append(user_id)
        return await real(db, user_id)

    with patch.object(access_check_writer, "load_run_user", counting):
        for _ in range(3):
            await flush(db_session, _person_collector(person.id, _power("apps.readbasic", home.id)), **ROUTE)

    assert loads == [person.id]


async def test_a_persons_scope_switch_writes_would_denies_only(db_session: AsyncSession) -> None:
    home, other = await _org(db_session), await _org(db_session)
    person = await _person(db_session, home)
    collector = _person_collector(person.id, Note("scope_switch", other.id), Note("scope_switch", home.id))

    await flush(db_session, collector, **ROUTE)

    [row] = await _person_rows(db_session, person.id)
    assert (row.resource_type, row.outcome, row.organization_id) == ("scope_switch", "failure", other.id)
    assert row.details is not None
    assert row.details["trace"]["steps"][1]["reason"] == "no_workflow"


def _owned(permission: str, kind: str, object_id: UUID, actor: UUID, **extra) -> Note:
    return Note(
        "permission",
        None,
        {
            "permission": permission,
            "subject": f"{kind}:{object_id}",
            "owned": kind,
            "object_id": str(object_id),
            "actor": str(actor),
            **extra,
        },
    )


async def test_an_agent_run_noted_unloaded_is_judged_in_its_own_org(db_session: AsyncSession) -> None:
    from src.models.orm import AgentRun

    home, other = await _org(db_session), await _org(db_session)
    person, someone = await _person(db_session, home), await _person(db_session, other)
    theirs = AgentRun(trigger_type="api", org_id=other.id, caller_user_id=str(someone.id))
    own = AgentRun(trigger_type="api", org_id=other.id, caller_user_id=str(person.id))
    db_session.add_all([theirs, own])
    await db_session.flush()

    collector = _person_collector(
        person.id,
        _owned("agentruns.read.all", "agent_run", theirs.id, person.id),
        _owned("agentruns.read.all", "agent_run", own.id, person.id),
    )
    await flush(db_session, collector, **ROUTE)

    [row] = await _person_rows(db_session, person.id)
    assert row.organization_id == other.id
    assert row.details is not None and row.details["inputs"]["subject"] == f"agent_run:{theirs.id}"


async def test_writing_into_someone_elses_workspace_is_judged_where_their_artifacts_are(
    db_session: AsyncSession,
) -> None:
    from src.models.orm import Artifact

    home, other = await _org(db_session), await _org(db_session)
    person, someone = await _person(db_session, home), await _person(db_session, other)
    shared, mine = uuid4(), uuid4()
    for workspace, owner, org in ((shared, someone, other), (mine, person, home)):
        db_session.add(
            Artifact(
                organization_id=org.id,
                created_by_user_id=owner.id,
                workspace_id=workspace,
                s3_key=f"_artifact_workspaces/{workspace}/{uuid4()}/a.txt",
                filename="a.txt",
                content_type="text/plain",
                size_bytes=1,
            )
        )
    await db_session.flush()

    collector = _person_collector(
        person.id,
        _owned("artifacts.readwrite.all", "artifact_workspace", shared, person.id),
        _owned("artifacts.readwrite.all", "artifact_workspace", mine, person.id),
    )
    await flush(db_session, collector, **ROUTE)

    [row] = await _person_rows(db_session, person.id)
    assert row.organization_id == other.id
    assert row.details is not None and row.details["inputs"]["subject"] == f"artifact_workspace:{shared}"


async def test_tools_the_actor_could_not_attach_are_judged_at_the_agent(db_session: AsyncSession) -> None:
    from src.models.orm import Agent, Workflow

    home = await _org(db_session)
    person = await _person(db_session, home)
    agent = Agent(
        name=f"a-{uuid4().hex[:6]}", system_prompt="Hi", organization_id=home.id, owner_user_id=person.id, created_by="t"
    )
    locked = Workflow(
        name=f"w_{uuid4().hex[:6]}", function_name="w", path="workflows/w.py", access_level="role_based", type="tool"
    )
    db_session.add_all([agent, locked])
    await db_session.flush()

    collector = _person_collector(
        person.id, _owned("agents.readwrite", "agent_tools", agent.id, person.id, tools=[str(locked.id)])
    )
    await flush(db_session, collector, **ROUTE)

    [row] = await _person_rows(db_session, person.id)
    assert (row.outcome, row.organization_id) == ("failure", home.id)
    assert row.details is not None and row.details["inputs"]["permission"] == "agents.readwrite"


async def test_an_object_that_does_not_exist_yet_is_judged_by_a_later_request(db_session: AsyncSession) -> None:
    from src.models.enums import ExecutionStatus
    from src.models.orm import Execution

    home, other = await _org(db_session), await _org(db_session)
    person, someone = await _person(db_session, home), await _person(db_session, other)
    execution_id = uuid4()
    note = _owned("executions.read.all", "execution", execution_id, person.id)

    await flush(db_session, _person_collector(person.id, note), **ROUTE)
    assert await _person_rows(db_session, person.id) == []

    db_session.add(
        Execution(
            id=execution_id,
            workflow_name="w",
            status=ExecutionStatus.SUCCESS,
            organization_id=other.id,
            executed_by=someone.id,
            executed_by_name="S",
        )
    )
    await db_session.flush()
    await flush(db_session, _person_collector(person.id, note), **ROUTE)

    [row] = await _person_rows(db_session, person.id)
    assert row.organization_id == other.id


async def test_a_judgement_that_failed_is_judged_again(db_session: AsyncSession) -> None:
    from src.services import access_check_writer

    home, other = await _org(db_session), await _org(db_session)
    person = await _person(db_session, home)
    calls: list[str] = []
    real = access_check_writer.judge

    def flaky(run_user, powers, note, entry, run_as):
        calls.append(note.kind)
        if len(calls) == 1:
            raise TimeoutError("transient")
        return real(run_user, powers, note, entry, run_as)

    with patch.object(access_check_writer, "judge", flaky):
        for _ in range(3):
            await flush(db_session, _person_collector(person.id, _power("agents.read", other.id)), **ROUTE)

    assert len(calls) == 2
    rows = await _person_rows(db_session, person.id)
    assert sorted((row.action, row.outcome) for row in rows) == [
        ("access.check", "failure"),
        ("access.check_gap", "failure"),
    ]


async def test_a_write_that_failed_is_written_by_a_later_attempt(db_session: AsyncSession) -> None:
    from src.repositories.audit_logs import AuditLogRepository

    home, other = await _org(db_session), await _org(db_session)
    person = await _person(db_session, home)
    real = AuditLogRepository.create
    attempts: list[str] = []

    async def failing_once(self, **row):
        attempts.append(row["action"])
        if len(attempts) == 1:
            raise ConnectionError("database unavailable")
        return await real(self, **row)

    with patch.object(AuditLogRepository, "create", failing_once):
        for _ in range(3):
            await flush(db_session, _person_collector(person.id, _power("agents.read", other.id)), **ROUTE)

    assert attempts == ["access.check", "access.check"]
    [row] = await _person_rows(db_session, person.id)
    assert (row.action, row.outcome) == ("access.check", "failure")


async def test_a_row_another_writer_is_inserting_is_written_by_a_later_attempt(db_session: AsyncSession) -> None:
    from src.models.orm import Agent, Workflow
    from src.repositories.audit_logs import AuditLogRepository

    home = await _org(db_session)
    person = await _person(db_session, home)
    agent = Agent(
        name=f"a-{uuid4().hex[:6]}", system_prompt="Hi", organization_id=home.id, owner_user_id=person.id, created_by="t"
    )
    locked = [
        Workflow(name=f"w_{n}", function_name=f"w_{n}", path=f"workflows/{n}.py", access_level="role_based", type="tool")
        for n in (uuid4().hex[:6], uuid4().hex[:6])
    ]
    db_session.add_all([agent, *locked])
    await db_session.flush()
    # Two updates attach different tools, so their judgements differ, but
    # resolution strips the tools and both would write the same row.
    first, second = (_owned("agents.readwrite", "agent_tools", agent.id, person.id, tools=[str(w.id)]) for w in locked)

    inserting, fail = asyncio.Event(), asyncio.Event()
    real = AuditLogRepository.create
    attempts: list[str] = []

    async def failing_once_while_held(self, **row):
        attempts.append(row["action"])
        if len(attempts) == 1:
            inserting.set()
            await fail.wait()
            raise ConnectionError("database unavailable")
        return await real(self, **row)

    with patch.object(AuditLogRepository, "create", failing_once_while_held):
        holder = asyncio.create_task(flush(db_session, _person_collector(person.id, first), **ROUTE))
        await inserting.wait()
        await flush(db_session, _person_collector(person.id, second), **ROUTE)
        assert attempts == ["access.check"]
        fail.set()
        held = await holder
        assert held is None
        await flush(db_session, _person_collector(person.id, second), **ROUTE)

    assert attempts == ["access.check", "access.check"]
    [row] = await _person_rows(db_session, person.id)
    assert (row.action, row.outcome, row.organization_id) == ("access.check", "failure", home.id)


async def _writer(db_session: AsyncSession):
    from src.core.cache.redis_client import get_shared_redis
    from src.services.access_check_writer import _Writer

    redis = await get_shared_redis()
    return redis, _Writer(db_session, redis, _person_collector(uuid4()), ROUTE["operation"])


async def test_a_stale_claim_never_releases_its_successors(db_session: AsyncSession) -> None:
    redis, writer = await _writer(db_session)
    note = _power("agents.read", uuid4())
    stale = await writer.reserve(note)
    assert stale is not None
    # The claim expired and another judgement took the decision.
    await redis.set(stale.key, "successor", ex=300)

    await writer.settle(stale, done=False)

    assert await redis.get(stale.key) == "successor"


async def test_a_stale_claim_cannot_keep_the_decision_for_the_day(db_session: AsyncSession) -> None:
    redis, writer = await _writer(db_session)
    note = _power("agents.read", uuid4())
    stale = await writer.reserve(note)
    assert stale is not None
    await redis.set(stale.key, "successor", ex=300)

    await writer.settle(stale, done=True)

    assert await redis.ttl(stale.key) <= 300
    assert await writer.reserve(note) is None


async def test_a_held_claim_is_kept_for_the_day_or_released(db_session: AsyncSession) -> None:
    redis, writer = await _writer(db_session)
    kept, released = await writer.reserve(_power("agents.read", uuid4())), await writer.reserve(
        _power("agents.read", uuid4())
    )
    assert kept is not None and released is not None

    await writer.settle(kept, done=True)
    await writer.settle(released, done=False)

    assert await redis.ttl(kept.key) > 300
    assert await redis.get(kept.key) == "done"
    assert await redis.exists(released.key) == 0
