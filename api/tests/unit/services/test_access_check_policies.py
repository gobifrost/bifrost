"""Table and file policies, evaluated again against the run's user (report-only)."""

from __future__ import annotations

from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from shared import access_checks
from shared.identities import ensure_default_identity
from src.models.contracts.policies import TablePolicies
from src.models.orm.organizations import Organization
from src.models.orm.tables import Table
from src.models.orm.users import Role, User, UserRole
from src.services.access_check_policies import check_table_rows, check_table_write

ADMIN_RULE = {"name": "admins", "actions": ["read", "create"], "when": {"eq": [{"user": "is_platform_admin"}, True]}}
HR_RULE = {"name": "hr", "actions": ["create"], "when": {"call": "has_role", "args": ["HR"]}}
OWN_ROWS = {"name": "own", "actions": ["read"], "when": {"eq": [{"row": "owner"}, {"user": "email"}]}}
REGION_RULE = {"name": "region", "actions": ["create"], "when": {"in": [{"row": "region"}, {"claims": "regions"}]}}


def _policies(*rules: dict) -> TablePolicies:
    return TablePolicies.model_validate({"policies": list(rules)})


@pytest_asyncio.fixture(autouse=True)
async def _fresh_redis_client(monkeypatch):
    from src.core.cache import redis_client

    monkeypatch.setattr(redis_client, "_shared_client", None)
    yield
    await redis_client.close_shared_redis()


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


@pytest_asyncio.fixture
async def world(db_session: AsyncSession):
    org = Organization(name=f"Policy Org {uuid4().hex[:8]}", created_by="policy-test")
    db_session.add(org)
    await db_session.flush()
    await ensure_default_identity(db_session, org)
    person = User(email=f"{uuid4()}@policy.example", name="Person", organization_id=org.id)
    hr = User(email=f"{uuid4()}@policy.example", name="HR", organization_id=org.id)
    role = Role(name="HR", created_by="policy-test")
    db_session.add_all([person, hr, role])
    await db_session.flush()
    db_session.add(UserRole(user_id=hr.id, role_id=role.id, assigned_by="policy-test"))
    table = Table(name=f"policy_{uuid4().hex[:8]}", organization_id=org.id)
    db_session.add(table)
    await db_session.flush()
    return {"org": org, "person": person, "hr": hr, "table": table}


def _collecting(run_user_id):
    return access_checks.start_collecting(
        {"sub": str(uuid4()), "is_superuser": True, "engine_execution_id": str(uuid4()), "engine_run_user_id": str(run_user_id)}
    )


async def _check_write(db, world, run_user, policies, *, today: bool) -> access_checks.Note:
    token = _collecting(run_user.id)
    try:
        await check_table_write(db, "create", world["table"], [{"id": "r1"}], policies, allowed_today=today)
        collector = access_checks.current()
        assert collector is not None
        [note] = collector.notes
        return note
    finally:
        access_checks.stop_collecting(token)


async def test_a_role_the_run_user_lacks_is_recorded_as_missing(db_session, world) -> None:
    note = await _check_write(db_session, world, world["person"], _policies(HR_RULE), today=False)

    assert note.kind == "policy" and note.target == world["org"].id
    assert note.facts["model"] is False
    assert note.facts["missing"] == ["role:HR"]
    assert note.facts["today"] is False


async def test_a_run_user_holding_the_role_passes(db_session, world) -> None:
    note = await _check_write(db_session, world, world["hr"], _policies(HR_RULE), today=False)

    assert note.facts["model"] is True
    assert note.facts["missing"] == []


async def test_a_full_workflow_passes_a_policy_that_allows_admins(db_session, world) -> None:
    note = await _check_write(db_session, world, world["person"], _policies(ADMIN_RULE, HR_RULE), today=True)

    assert note.facts["model"] is True


async def test_an_unresolvable_claim_is_recorded_as_missing_without_raising(db_session, world) -> None:
    note = await _check_write(db_session, world, world["person"], _policies(REGION_RULE), today=False)

    assert note.facts["model"] is False
    assert note.facts["missing"] == ["claim:regions"]


async def test_rows_the_run_user_would_not_see_are_counted(db_session, world) -> None:
    rows = [{"id": "a", "owner": world["person"].email}, {"id": "b", "owner": "x@y"}, {"id": "c", "owner": "z@y"}]
    token = _collecting(world["person"].id)
    try:
        await check_table_rows(db_session, world["table"], _policies(OWN_ROWS), rows)
        collector = access_checks.current()
        assert collector is not None
        [note] = collector.notes
    finally:
        access_checks.stop_collecting(token)

    assert note.facts["model"] is False
    assert (note.facts["hidden"], note.facts["returned"]) == (2, 3)


@pytest.mark.parametrize("helper", ["write", "rows"])
async def test_outside_a_run_nothing_is_evaluated(helper) -> None:
    table = Table(name="t", organization_id=uuid4())
    if helper == "write":
        await check_table_write(None, "create", table, [{}], _policies(HR_RULE), allowed_today=True)  # type: ignore[arg-type]
    else:
        await check_table_rows(None, table, _policies(OWN_ROWS), [{"id": "a"}])  # type: ignore[arg-type]
    assert access_checks.current() is None
