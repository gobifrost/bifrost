"""Authorization invariants shared by native and external Builder runtimes."""

from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from src.models.orm.solution_builder import SolutionBuilderProject
from src.models.orm.solutions import Solution
from src.services.builder import runtime_authorization as runtime_auth
from src.services.builder.runtime_authorization import BuilderRuntimeForbidden
from src.services.solutions.access import SolutionAction


class _Authorization:
    def __init__(self, capabilities: set[str]) -> None:
        self.capabilities = capabilities
        self.checked: list[str] = []
        self.role_ids = (uuid4(),)

    def has_capability(self, capability: str) -> bool:
        self.checked.append(capability)
        return capability in self.capabilities

    def has_delegated_capability(self, capability: str) -> bool:
        return capability in self.capabilities


def _db_for_project(*, target_kind: str = "solution"):
    solution = SimpleNamespace(
        id=uuid4(),
        organization_id=uuid4(),
    )
    project = SimpleNamespace(
        solution_id=solution.id,
        target_kind=target_kind,
    )
    db = SimpleNamespace(get=AsyncMock())

    async def get(model, key):
        assert key == solution.id
        if model is Solution:
            return solution
        if model is SolutionBuilderProject:
            return project
        raise AssertionError(f"unexpected model {model}")

    db.get.side_effect = get
    return db, solution, project


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("action", "repository_capability"),
    [
        (SolutionAction.VIEW, "repository.read"),
        (SolutionAction.EDIT, "repository.readwrite"),
        (SolutionAction.BUILD, "repository.readwrite"),
    ],
)
async def test_global_workspace_adds_repository_capability(
    monkeypatch: pytest.MonkeyPatch,
    action: SolutionAction,
    repository_capability: str,
) -> None:
    db, solution, project = _db_for_project(target_kind="global_repo")
    principal = SimpleNamespace(user_id=uuid4(), is_external=False)
    authorization = _Authorization({"builder.execute", repository_capability})
    load_access = AsyncMock(return_value=(solution, project))
    monkeypatch.setattr(runtime_auth, "load_builder_principal", AsyncMock(return_value=principal))
    monkeypatch.setattr(
        runtime_auth,
        "resolve_authorization_context",
        AsyncMock(return_value=authorization),
    )
    monkeypatch.setattr(runtime_auth, "load_accessible_private_solution", load_access)

    result = await runtime_auth.authorize_builder_project(
        db,
        solution_id=solution.id,
        requester_user_id=principal.user_id,
        action=action,
        required_capabilities=("builder.execute",),
    )

    assert result.solution is solution
    assert result.project is project
    assert authorization.checked[:2] == ["builder.execute", repository_capability]
    load_access.assert_not_awaited()


@pytest.mark.asyncio
async def test_organization_workspace_uses_boundary_capabilities_not_private_owner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db, solution, project = _db_for_project(target_kind="organization")
    principal = SimpleNamespace(user_id=uuid4(), is_external=False)
    authorization = _Authorization({"builder.execute", "agents.readwrite"})
    load_access = AsyncMock()
    monkeypatch.setattr(
        runtime_auth,
        "load_builder_principal",
        AsyncMock(return_value=principal),
    )
    monkeypatch.setattr(
        runtime_auth,
        "resolve_authorization_context",
        AsyncMock(return_value=authorization),
    )
    monkeypatch.setattr(runtime_auth, "load_accessible_private_solution", load_access)

    result = await runtime_auth.authorize_builder_project(
        db,
        solution_id=solution.id,
        requester_user_id=principal.user_id,
        action=SolutionAction.BUILD,
        required_capabilities=("builder.execute", "agents.readwrite"),
    )

    assert result.solution is solution
    assert result.project is project
    load_access.assert_not_awaited()


@pytest.mark.asyncio
async def test_missing_required_capability_fails_before_resource_admission(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db, solution, _project = _db_for_project()
    principal = SimpleNamespace(user_id=uuid4(), is_external=False)
    authorization = _Authorization({"builder.execute"})
    load_access = AsyncMock()
    monkeypatch.setattr(runtime_auth, "load_builder_principal", AsyncMock(return_value=principal))
    monkeypatch.setattr(
        runtime_auth,
        "resolve_authorization_context",
        AsyncMock(return_value=authorization),
    )
    monkeypatch.setattr(runtime_auth, "load_accessible_private_solution", load_access)

    with pytest.raises(BuilderRuntimeForbidden):
        await runtime_auth.authorize_builder_project(
            db,
            solution_id=solution.id,
            requester_user_id=principal.user_id,
            action=SolutionAction.EDIT,
            required_capabilities=("builder.execute", "solutions.readwrite"),
        )

    load_access.assert_not_awaited()


@pytest.mark.asyncio
async def test_resource_admission_is_required_after_capability_checks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db, solution, _project = _db_for_project()
    principal = SimpleNamespace(user_id=uuid4(), is_external=False)
    authorization = _Authorization({"builder.execute"})
    monkeypatch.setattr(runtime_auth, "load_builder_principal", AsyncMock(return_value=principal))
    monkeypatch.setattr(
        runtime_auth,
        "resolve_authorization_context",
        AsyncMock(return_value=authorization),
    )
    monkeypatch.setattr(
        runtime_auth,
        "load_accessible_private_solution",
        AsyncMock(return_value=None),
    )

    with pytest.raises(BuilderRuntimeForbidden):
        await runtime_auth.authorize_builder_project(
            db,
            solution_id=solution.id,
            requester_user_id=principal.user_id,
            action=SolutionAction.EDIT,
            required_capabilities=("builder.execute",),
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("is_active", "is_external"),
    [(False, False), (True, True)],
)
async def test_inactive_and_external_requesters_cannot_run_builder(
    is_active: bool,
    is_external: bool,
) -> None:
    user = SimpleNamespace(is_active=is_active, is_external=is_external)
    db = SimpleNamespace(get=AsyncMock(return_value=user))

    with pytest.raises(BuilderRuntimeForbidden):
        await runtime_auth.load_builder_principal(db, uuid4())
