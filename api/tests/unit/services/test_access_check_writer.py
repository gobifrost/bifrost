"""Judging a request's notes and writing ``access.check`` audit events."""

from __future__ import annotations

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
from src.models.orm.users import User
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
    person = await _person(db_session, home)
    collector = _collector(
        person.id,
        Note("run_as", None, {"run_as_user_id": str(uuid4())}),
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
