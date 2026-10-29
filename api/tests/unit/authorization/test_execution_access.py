"""Execution-read authority is based on current original-user facts."""

from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

import src.services.authorization.execution_access as access
from src.services.authorization.reach import OrgReach


def _execution(*, user_id, organization_id, root_id=None, started_by_user_id=None):
    execution_id = root_id or uuid4()
    return SimpleNamespace(
        id=execution_id,
        executed_by=user_id,
        started_by_user_id=started_by_user_id or user_id,
        root_execution_id=root_id or execution_id,
        organization_id=organization_id,
    )


def test_own_basic_execution_does_not_include_diagnostics(monkeypatch):
    user_id = uuid4()
    organization_id = uuid4()
    caller = SimpleNamespace(principal=SimpleNamespace(user_id=user_id, runtime_authority=None))
    monkeypatch.setattr(access, "user_reach", lambda _caller: OrgReach(organization_ids=frozenset({organization_id})))
    monkeypatch.setattr(access, "allows_permission", lambda *_args: False)

    result = access.execution_read_access(caller, _execution(user_id=user_id, organization_id=organization_id))

    assert result.visible is True
    assert result.diagnostics is False


def test_named_detail_permission_enables_own_execution_diagnostics(monkeypatch):
    user_id = uuid4()
    organization_id = uuid4()
    caller = SimpleNamespace(principal=SimpleNamespace(user_id=user_id, runtime_authority=None))
    monkeypatch.setattr(access, "user_reach", lambda _caller: OrgReach(organization_ids=frozenset({organization_id})))
    monkeypatch.setattr(access, "allows_permission", lambda _caller, permission, _org: permission == "executions.read")

    result = access.execution_read_access(caller, _execution(user_id=user_id, organization_id=organization_id))

    assert result.visible is True
    assert result.diagnostics is True


def test_read_all_cannot_escape_original_user_reach(monkeypatch):
    owner_id = uuid4()
    actor_id = uuid4()
    caller = SimpleNamespace(principal=SimpleNamespace(user_id=actor_id, runtime_authority=None))
    reachable_org_id = uuid4()
    monkeypatch.setattr(
        access,
        "user_reach",
        lambda _caller: OrgReach(organization_ids=frozenset({reachable_org_id})),
    )
    monkeypatch.setattr(access, "allows_permission", lambda *_args: True)

    result = access.execution_read_access(
        caller,
        _execution(user_id=owner_id, organization_id=uuid4()),
    )

    assert result.visible is False
    assert result.diagnostics is False


def test_runtime_effects_actor_cannot_grant_original_user_history_access(monkeypatch):
    original_user_id = uuid4()
    effects_actor_id = uuid4()
    organization_id = uuid4()
    caller = SimpleNamespace(
        principal=SimpleNamespace(
            user_id=effects_actor_id,
            runtime_authority=SimpleNamespace(
                run_user=SimpleNamespace(user_id=original_user_id)
            ),
        )
    )
    monkeypatch.setattr(
        access,
        "user_reach",
        lambda _caller: OrgReach(organization_ids=frozenset({organization_id})),
    )
    monkeypatch.setattr(access, "allows_permission", lambda *_args: False)

    result = access.execution_read_access(
        caller,
        _execution(user_id=effects_actor_id, organization_id=organization_id),
    )

    assert result.visible is False
    assert result.diagnostics is False


def test_runtime_reader_uses_original_run_user_not_actor(monkeypatch):
    original_user_id = uuid4()
    actor_id = uuid4()
    organization_id = uuid4()
    caller = SimpleNamespace(
        principal=SimpleNamespace(
            user_id=actor_id,
            runtime_authority=SimpleNamespace(run_user=SimpleNamespace(user_id=original_user_id)),
        )
    )
    monkeypatch.setattr(access, "user_reach", lambda _caller: OrgReach(organization_ids=frozenset({organization_id})))
    monkeypatch.setattr(access, "allows_permission", lambda *_args: False)

    assert access.execution_reader_id(caller) == original_user_id
    assert access.execution_read_access(
        caller,
        _execution(user_id=original_user_id, organization_id=organization_id),
    ).visible is True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("embed_kind", "grant"),
    [("app", "hmac"), ("form", "hmac")],
)
async def test_current_execution_read_allows_verified_embed_session_owning_exact_org(
    monkeypatch, embed_kind, grant
):
    organization_id = uuid4()
    execution = _execution(user_id=uuid4(), organization_id=organization_id)
    caller = SimpleNamespace(
        principal=SimpleNamespace(
            embed=True,
            is_verified=True,
            embed_kind=embed_kind,
            grant=grant,
            jti="own-session",
            organization_id=organization_id,
        )
    )
    owns_execution = AsyncMock(return_value=True)
    monkeypatch.setattr(
        "src.core.embed_middleware.embed_session_owns_execution",
        owns_execution,
    )

    result = await access.current_execution_read_access(caller, execution)

    assert result.visible is True
    assert result.diagnostics is False
    owns_execution.assert_awaited_once_with("own-session", str(execution.id))


@pytest.mark.asyncio
async def test_current_execution_read_denies_foreign_embed_session(monkeypatch):
    organization_id = uuid4()
    execution = _execution(user_id=uuid4(), organization_id=organization_id)
    caller = SimpleNamespace(
        principal=SimpleNamespace(
            embed=True,
            is_verified=True,
            embed_kind="app",
            grant="hmac",
            jti="foreign-session",
            organization_id=organization_id,
        )
    )
    owns_execution = AsyncMock(return_value=False)
    monkeypatch.setattr(
        "src.core.embed_middleware.embed_session_owns_execution",
        owns_execution,
    )

    assert await access.current_execution_read_access(caller, execution) == access.ExecutionReadAccess(False, False)
    owns_execution.assert_awaited_once_with("foreign-session", str(execution.id))


@pytest.mark.asyncio
async def test_current_execution_read_denies_public_or_foreign_org_embed(monkeypatch):
    organization_id = uuid4()
    execution = _execution(user_id=uuid4(), organization_id=organization_id)
    owns_execution = AsyncMock(return_value=True)
    monkeypatch.setattr(
        "src.core.embed_middleware.embed_session_owns_execution",
        owns_execution,
    )

    public_form = SimpleNamespace(
        principal=SimpleNamespace(
            embed=True,
            is_verified=True,
            embed_kind="form",
            grant="public",
            jti="public-session",
            organization_id=organization_id,
        )
    )
    foreign_org_app = SimpleNamespace(
        principal=SimpleNamespace(
            embed=True,
            is_verified=True,
            embed_kind="app",
            grant="hmac",
            jti="own-session",
            organization_id=uuid4(),
        )
    )

    assert await access.current_execution_read_access(public_form, execution) == access.ExecutionReadAccess(False, False)
    assert await access.current_execution_read_access(foreign_org_app, execution) == access.ExecutionReadAccess(False, False)
    owns_execution.assert_not_awaited()


@pytest.mark.asyncio
async def test_current_execution_read_delegates_human_to_canonical_access(monkeypatch):
    caller = SimpleNamespace(principal=SimpleNamespace(embed=False))
    execution = _execution(user_id=uuid4(), organization_id=uuid4())
    expected = access.ExecutionReadAccess(True, True)
    def canonical(_caller, _execution):
        return expected

    monkeypatch.setattr(access, "execution_read_access", canonical)

    assert await access.current_execution_read_access(caller, execution) is expected
