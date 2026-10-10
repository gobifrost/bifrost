"""Explaining access checks stored without a trace, without inputs, or in an
older shape.

Older and worker-written ``access.check`` rows may lack ``details.trace``
(then is unknown) or ``details.inputs`` too (now can't be judged).
"""

from __future__ import annotations

import asyncio
from uuid import uuid4

import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.orm.audit import AuditLog
from src.models.orm.organizations import Organization
from src.models.orm.users import User
from src.services.access_explain import rerun, stored_trace

_TRACE = {
    "outcome": "failure",
    "enforced": False,
    "steps": [{"key": "target", "label": "Target in reach", "status": "stopped", "reason": "outside", "facts": {}}],
}


def _row(details: dict) -> AuditLog:
    return AuditLog(action="access.check", resource_type="scope_switch", outcome="failure", user_id=uuid4(), details=details)


def test_stored_trace_is_the_trace_the_writer_kept() -> None:
    trace = stored_trace(_row({"trace": _TRACE, "inputs": {}}))

    assert trace is not None and (trace.outcome, trace.steps[0].key) == ("failure", "target")


def test_no_stored_trace_is_none() -> None:
    assert stored_trace(_row({"workflow_id": None, "inputs": {"operation": "GET /api/tables", "target": None}})) is None


def test_no_stored_inputs_cannot_be_judged_now() -> None:
    # Nothing is read: the row alone says it can't be re-run.
    assert asyncio.run(rerun(AsyncSession(), _row({"workflow_id": None}))) == (None, "inputs_not_stored")


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


def _run_as_row(run_user_id, run_as_user_id) -> AuditLog:
    """A run's ``run_as`` check as written before the target was judged: the
    note was Global and its facts carried only the user it named."""
    return AuditLog(
        action="access.check",
        resource_type="run_as",
        outcome="success",
        user_id=run_user_id,
        details={
            "enforced": False,
            "direct": False,
            "workflow_id": None,
            "inputs": {"operation": "POST /api/workflows/execute", "target": None, "run_as_user_id": str(run_as_user_id)},
        },
    )


async def test_an_older_run_as_check_is_judged_against_the_user_it_named(db_session: AsyncSession) -> None:
    org = Organization(name=f"Contoso {uuid4().hex[:8]}", created_by="explain-test")
    db_session.add(org)
    await db_session.flush()
    person = User(email=f"{uuid4()}@contoso.example", name="Contoso Person", organization_id=org.id)
    colleague = User(email=f"{uuid4()}@contoso.example", name="Contoso Colleague", organization_id=org.id)
    db_session.add_all([person, colleague])
    await db_session.flush()

    now, unavailable = await rerun(db_session, _run_as_row(person.id, colleague.id))

    assert unavailable is None and now is not None
    run_as_user = next(step for step in now.steps if step.key == "run_as_user")
    expected = {"run_as_user_id": str(colleague.id), "organization_id": str(org.id)}
    assert now.outcome == "success"
    assert run_as_user.facts == expected


async def test_a_run_as_check_naming_a_user_that_no_longer_exists_cannot_be_judged_now(
    db_session: AsyncSession,
) -> None:
    org = Organization(name=f"Contoso {uuid4().hex[:8]}", created_by="explain-test")
    db_session.add(org)
    await db_session.flush()
    person = User(email=f"{uuid4()}@contoso.example", name="Contoso Person", organization_id=org.id)
    db_session.add(person)
    await db_session.flush()

    result = await rerun(db_session, _run_as_row(person.id, uuid4()))

    assert result == (None, "run_as_user_missing")


async def test_a_refusal_recorded_before_any_lookup_is_judged_without_the_user(db_session: AsyncSession) -> None:
    org = Organization(name=f"Contoso {uuid4().hex[:8]}", created_by="explain-test")
    db_session.add(org)
    await db_session.flush()
    person = User(email=f"{uuid4()}@contoso.example", name="Contoso Person", organization_id=org.id)
    db_session.add(person)
    await db_session.flush()
    row = AuditLog(
        action="access.check",
        resource_type="run_as",
        outcome="failure",
        user_id=person.id,
        organization_id=org.id,
        details={
            "enforced": True,
            "direct": True,
            "workflow_id": None,
            "inputs": {
                "operation": "POST /api/workflows/execute",
                "target": str(org.id),
                "run_as_user_id": str(uuid4()),
                "enforced": True,
                "held_nowhere": True,
            },
        },
    )

    now, unavailable = await rerun(db_session, row)

    assert unavailable is None and now is not None
    stopped = [step.key for step in now.steps if step.status == "stopped"]
    assert (now.outcome, stopped) == ("failure", ["permission"])
