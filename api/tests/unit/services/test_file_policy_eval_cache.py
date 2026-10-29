"""A request-scoped cache removes repeated policy loading without changing decisions."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy import event

from src.models.contracts.policies import FilePolicies
from src.models.orm.organizations import Organization
from src.services.file_policy_service import (
    FilePolicyDecision,
    FilePolicyEvalCache,
    FilePolicyService,
)


def _user(org_id):
    return SimpleNamespace(
        user_id=str(uuid4()), email="u@example.com", organization_id=str(org_id),
        is_platform_admin=False, role_ids=[], role_names=[], claims={},
    )


async def _org_with_policy(db_session):
    org = Organization(id=uuid4(), name=f"Cache-{uuid4().hex[:8]}", created_by="test")
    db_session.add(org)
    await db_session.flush()
    await FilePolicyService(db_session).upsert_policy(
        organization_id=org.id, location="docs", path="campus-a",
        policies=FilePolicies.model_validate(
            {"policies": [{"name": "r", "actions": ["read"], "when": None}]}
        ),
        created_by=uuid4(),
    )
    return org


@pytest.fixture
def policy_selects(db_session):
    """Statements against file_policies issued through the test session."""
    seen: list[str] = []
    engine = db_session.bind.sync_engine

    def before(_conn, _cursor, statement, *_args):
        if "FROM file_policies" in statement:
            seen.append(statement)

    event.listen(engine, "before_cursor_execute", before)
    yield seen
    event.remove(engine, "before_cursor_execute", before)


async def test_cache_loads_policy_rows_once_and_keeps_decisions(db_session, policy_selects) -> None:
    org = await _org_with_policy(db_session)
    service = FilePolicyService(db_session)
    user = _user(org.id)
    seen = policy_selects
    seen.clear()

    cache = FilePolicyEvalCache()
    decisions = [
        (
            await service.evaluate(
                "read", organization_id=org.id, location="docs", path=p, user=user, cache=cache
            )
        ).allowed
        for p in ("campus-a/1.pdf", "campus-a/2.pdf", "campus-b/3.pdf")
    ]
    cached_queries = len(seen)

    seen.clear()
    uncached = [
        await service.is_allowed("read", organization_id=org.id, location="docs", path=p, user=user)
        for p in ("campus-a/1.pdf", "campus-a/2.pdf", "campus-b/3.pdf")
    ]

    assert decisions == uncached == [True, True, False]
    assert cached_queries < len(seen)
    assert cached_queries <= 2  # org arm + global arm, once each


async def test_authorize_forwards_cache_to_evaluator(monkeypatch) -> None:
    from shared import file_access

    evaluate = AsyncMock(
        return_value=FilePolicyDecision(True, object(), FilePolicies.model_validate({"policies": []}))
    )
    monkeypatch.setattr(FilePolicyService, "evaluate", evaluate)
    monkeypatch.setattr(file_access, "record_file_policy", AsyncMock())
    cache = FilePolicyEvalCache()
    caller = SimpleNamespace(user=SimpleNamespace(is_superuser=False), db=object())

    assert await file_access.authorize_file_policy(
        caller, action="read", location="docs", scope=str(uuid4()), path="a",
        organization_id=None, policy_cache=cache,
    )
    assert evaluate.await_args.kwargs["cache"] is cache


async def test_cache_builds_the_enforced_principal_once_per_caller(db_session, monkeypatch) -> None:
    from src.services import access_check_policies

    org = await _org_with_policy(db_session)
    built: list[object] = []

    async def enforced(_db, principal):
        built.append(principal)
        # A fresh object per call, like a runtime credential's run user.
        return SimpleNamespace(**vars(principal))

    monkeypatch.setattr(access_check_policies, "enforced_policy_principal", enforced)
    service = FilePolicyService(db_session)
    cache = FilePolicyEvalCache()
    first, second = _user(org.id), _user(org.id)
    decisions = [
        await service.evaluate(
            "read", organization_id=org.id, location="docs", path=p, user=u, cache=cache
        )
        for u in (first, second)
        for p in ("campus-a/1.pdf", "campus-a/2.pdf")
    ]

    assert built == [first, second]
    assert [d.allowed for d in decisions] == [True] * 4
    assert decisions[0].principal is decisions[1].principal
    assert decisions[0].principal is not decisions[2].principal


async def test_resolution_memo_is_per_organization(db_session) -> None:
    """A ref resolved under one org's cascade is not reused for another org."""
    from src.models.orm.file_metadata import FilePolicy

    org_a = Organization(id=uuid4(), name=f"CacheA-{uuid4().hex[:8]}", created_by="test")
    org_b = Organization(id=uuid4(), name=f"CacheB-{uuid4().hex[:8]}", created_by="test")
    db_session.add_all([org_a, org_b])
    await db_session.flush()
    row = FilePolicy(
        id=uuid4(), organization_id=None, location=f"glob-{uuid4().hex[:8]}", path="",
        policies={"policies": [{"name": "r", "actions": ["read"], "when": None}]},
        created_by=uuid4(),
    )
    db_session.add(row)
    await db_session.flush()

    service = FilePolicyService(db_session)
    cache = FilePolicyEvalCache()
    for org in (org_a, org_b):
        decision = await service.evaluate(
            "read", organization_id=org.id, location=row.location, path="x",
            user=_user(org.id), cache=cache,
        )
        assert decision.allowed

    assert set(cache.resolved) == {(org_a.id, row.id), (org_b.id, row.id)}
