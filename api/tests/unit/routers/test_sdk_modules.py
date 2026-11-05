import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID, uuid4

import pytest
from fastapi import HTTPException

from shared.sdk_modules import ModuleSourceError, ModuleSourceScope
from src.core.principal import UserPrincipal
from src.models.contracts.workflow_permissions import WorkflowPermissionMode
from src.services.authorization.context import AuthorizationContext
from src.services.authorization.explain import Powers, RunUser
from src.services.authorization.runtime import RuntimeAuthority

SERVICE = "shared.sdk_modules"

SOLUTION_A = "12345678-1234-5678-1234-567812345678"
SOLUTION_B = "87654321-4321-8765-4321-876543218765"


@pytest.fixture(autouse=True)
def resolver_generation():
    with patch(f"{SERVICE}.get_module_resolution_generation", new_callable=AsyncMock, return_value="generation"):
        yield


def _runtime_user(*, workflow_id: UUID | None, full: bool = True) -> UserPrincipal:
    """A concrete typed credential, without legacy claim or sentinel authority."""
    run_user_id = uuid4()
    actor_id = uuid4()
    context = AuthorizationContext(
        user_id=run_user_id,
        home_organization_id=None,
        base_role_id=uuid4(),
        is_external=False,
        base_permissions=frozenset({"*"}) if full else frozenset(),
    )
    principal = UserPrincipal(
        user_id=actor_id,
        email="runtime-actor@example.test",
        organization_id=None,
    )
    principal.runtime_authority = RuntimeAuthority(
        kind="workflow_execution",
        credential_id=uuid4(),
        run_user=RunUser(run_user_id, context, None, is_active=True),
        powers=Powers(WorkflowPermissionMode.FULL, ()) if workflow_id is not None else None,
        acting_user_id=actor_id,
        organization_id=None,
        workflow_id=workflow_id,
        started_by_user_id=run_user_id,
        root_execution_id=uuid4(),
    )
    return principal


def _agent_run_user() -> UserPrincipal:
    principal = _runtime_user(workflow_id=None)
    assert principal.runtime_authority is not None
    principal.runtime_authority = RuntimeAuthority(
        kind="agent_run",
        credential_id=uuid4(),
        run_user=principal.runtime_authority.run_user,
        powers=None,
        acting_user_id=principal.user_id,
        organization_id=None,
        workflow_id=None,
        started_by_user_id=None,
        root_execution_id=None,
    )
    return principal


class _ScopeDb:
    def __init__(self, workflow: object, solution: object) -> None:
        self.workflow = workflow
        self.solution = solution

    async def get(self, model, _identity, **_kwargs):
        return self.workflow if model.__name__ == "Workflow" else self.solution


async def test_requirements_http_contract_is_retained():
    from src.routers.sdk_modules import fetch_requirements

    with patch(
        "src.routers.sdk_modules.get_requirements",
        new_callable=AsyncMock,
        return_value={"content": "example==1\n", "hash": "digest"},
    ):
        response = await fetch_requirements(_runtime_user(workflow_id=None), MagicMock())
    assert response.status_code == 200
    assert response.body == b'{"content":"example==1\\n","hash":"digest"}'

    with (
        patch(
            "src.routers.sdk_modules.get_requirements",
            new_callable=AsyncMock,
            return_value=None,
        ),
        pytest.raises(HTTPException) as exc,
    ):
        await fetch_requirements(_runtime_user(workflow_id=None), MagicMock())
    assert exc.value.status_code == 404


async def test_runtime_workflow_scope_uses_its_durable_workflow_not_query_scope():
    from shared.sdk_modules import module_scope_for_principal

    workflow_id = uuid4()
    db = _ScopeDb(
        SimpleNamespace(id=workflow_id, solution_id=UUID(SOLUTION_A)),
        SimpleNamespace(status="active", organization_id=None, allow_outbound_access=False),
    )

    scope = await module_scope_for_principal(
        db, _runtime_user(workflow_id=workflow_id), solution_id=SOLUTION_B, global_repo_access=True,
    )

    assert scope == ModuleSourceScope(solution_id=SOLUTION_A, global_repo_access=False)


async def test_agent_runtime_cannot_load_workflow_source():
    from shared.sdk_modules import module_scope_for_principal

    with pytest.raises(ModuleSourceError, match="workflow source scope") as exc:
        await module_scope_for_principal(MagicMock(), _agent_run_user())
    assert exc.value.status_code == 403


async def test_inline_runtime_loads_workspace_source_only_with_global_repository_permission():
    from shared.sdk_modules import module_scope_for_principal

    scope = await module_scope_for_principal(
        MagicMock(), _runtime_user(workflow_id=None), solution_id=SOLUTION_A, global_repo_access=False,
    )

    assert scope == ModuleSourceScope(solution_id=None, global_repo_access=True)


async def test_inline_runtime_cannot_spoof_workspace_source_permission():
    from shared.sdk_modules import module_scope_for_principal

    with pytest.raises(ModuleSourceError, match="Workspace source access") as exc:
        await module_scope_for_principal(
            MagicMock(), _runtime_user(workflow_id=None, full=False),
            solution_id=SOLUTION_A, global_repo_access=True,
        )
    assert exc.value.status_code == 403


async def test_router_maps_typed_scope_failure_to_403():
    from src.routers.sdk_modules import fetch_module

    with pytest.raises(HTTPException) as exc:
        await fetch_module("modules/blocked.py", _agent_run_user(), MagicMock())
    assert exc.value.status_code == 403


# --- Shared service: scope rules ---


def test_system_module_path_cannot_cross_signed_solution_scope():
    from shared.sdk_modules import validate_engine_storage_path

    scope = ModuleSourceScope(
        solution_id=SOLUTION_A,
        global_repo_access=False,
    )
    validate_engine_storage_path(
        f"_solutions/{SOLUTION_A}/modules/ok.py",
        scope,
    )

    with pytest.raises(Exception) as cross_solution:
        validate_engine_storage_path(
            f"_solutions/{SOLUTION_B}/modules/no.py",
            scope,
        )
    assert cross_solution.value.status_code == 403

    with pytest.raises(Exception) as workspace:
        validate_engine_storage_path("modules/no.py", scope)
    assert workspace.value.status_code == 403


# --- Shared service: resolver ---


async def test_resolve_module_returns_solution_module_before_repo_fallback():
    from shared.sdk_modules import resolve_module_name

    async def fake_get_module(path: str):
        if path == f"_solutions/{SOLUTION_A}/modules/helpers.py":
            return {"content": "VALUE = 'solution'", "path": path, "hash": "abc"}
        if path == "modules/helpers.py":
            return {"content": "VALUE = 'repo'", "path": path, "hash": "def"}
        return None

    with (
        patch(f"{SERVICE}.get_module_resolution_cache", new_callable=AsyncMock, return_value=None),
        patch(f"{SERVICE}.set_module_resolution_cache", new_callable=AsyncMock),
        patch(f"{SERVICE}.get_module", side_effect=fake_get_module) as mock_get,
    ):
        result = await resolve_module_name(
            "modules.helpers",
            scope=ModuleSourceScope(solution_id=SOLUTION_A, global_repo_access=True),
        )

    assert result == {
        "kind": "module",
        "path": "modules/helpers.py",
        "storage_path": f"_solutions/{SOLUTION_A}/modules/helpers.py",
        "content": "VALUE = 'solution'",
        "hash": "abc",
    }
    assert mock_get.await_args_list[0].args == (
        f"_solutions/{SOLUTION_A}/modules/helpers.py",
    )


async def test_resolve_module_does_not_fallback_to_repo_when_global_access_off():
    from shared.sdk_modules import resolve_module_name

    with (
        patch(f"{SERVICE}.get_module_resolution_cache", new_callable=AsyncMock, return_value=None),
        patch(f"{SERVICE}.set_module_resolution_cache", new_callable=AsyncMock),
        patch(f"{SERVICE}.get_module", new_callable=AsyncMock, return_value=None) as mock_get,
        patch(f"{SERVICE}._prefix_exists", new_callable=AsyncMock, return_value=False),
    ):
        result = await resolve_module_name(
            "modules.helpers",
            scope=ModuleSourceScope(solution_id=SOLUTION_A, global_repo_access=False),
        )

    assert result == {"kind": "not_found", "path": "modules/helpers"}
    assert [call.args[0] for call in mock_get.await_args_list] == [
        f"_solutions/{SOLUTION_A}/modules/helpers.py",
        f"_solutions/{SOLUTION_A}/modules/helpers/__init__.py",
    ]


async def test_resolve_module_uses_workspace_without_solution_context():
    from shared.sdk_modules import resolve_module_name

    workspace_module = {
        "content": "VALUE = 'workspace'",
        "path": "modules/helpers.py",
        "hash": "workspace-hash",
    }

    async def fake_get_module(path: str):
        return workspace_module if path == "modules/helpers.py" else None

    with (
        patch(f"{SERVICE}.get_module_resolution_cache", new_callable=AsyncMock, return_value=None),
        patch(f"{SERVICE}.set_module_resolution_cache", new_callable=AsyncMock),
        patch(f"{SERVICE}.get_module", side_effect=fake_get_module) as mock_get,
    ):
        result = await resolve_module_name("modules.helpers", scope=ModuleSourceScope(solution_id=None, global_repo_access=False))

    assert result == {
        "kind": "module",
        "path": "modules/helpers.py",
        "storage_path": "modules/helpers.py",
        **workspace_module,
    }
    mock_get.assert_awaited_once_with("modules/helpers.py")


async def test_resolve_module_falls_back_to_workspace_when_global_access_on():
    from shared.sdk_modules import resolve_module_name

    async def fake_get_module(path: str):
        if path == "modules/helpers.py":
            return {
                "content": "VALUE = 'workspace'",
                "path": path,
                "hash": "workspace-hash",
            }
        return None

    with (
        patch(f"{SERVICE}.get_module_resolution_cache", new_callable=AsyncMock, return_value=None),
        patch(f"{SERVICE}.set_module_resolution_cache", new_callable=AsyncMock),
        patch(f"{SERVICE}.get_module", side_effect=fake_get_module) as mock_get,
        patch(f"{SERVICE}._prefix_exists", new_callable=AsyncMock, return_value=False),
    ):
        result = await resolve_module_name(
            "modules.helpers",
            scope=ModuleSourceScope(solution_id=SOLUTION_A, global_repo_access=True),
        )

    assert result == {
        "kind": "module",
        "path": "modules/helpers.py",
        "storage_path": "modules/helpers.py",
        "content": "VALUE = 'workspace'",
        "hash": "workspace-hash",
    }
    assert [call.args[0] for call in mock_get.await_args_list] == [
        f"_solutions/{SOLUTION_A}/modules/helpers.py",
        f"_solutions/{SOLUTION_A}/modules/helpers/__init__.py",
        "modules/helpers.py",
    ]


async def test_resolve_module_uses_bounded_prefix_check_for_namespace():
    from shared.sdk_modules import resolve_module_name

    with (
        patch(f"{SERVICE}.get_module_resolution_cache", new_callable=AsyncMock, return_value=None),
        patch(f"{SERVICE}.set_module_resolution_cache", new_callable=AsyncMock),
        patch(f"{SERVICE}.get_module", new_callable=AsyncMock, return_value=None),
        patch(f"{SERVICE}._prefix_exists", new_callable=AsyncMock, return_value=True) as mock_prefix,
    ):
        result = await resolve_module_name("modules", scope=ModuleSourceScope(solution_id=None, global_repo_access=False))

    assert result == {"kind": "namespace", "path": "modules"}
    mock_prefix.assert_awaited_once_with("modules/")


async def test_resolve_module_uses_cached_not_found_before_storage():
    from shared.sdk_modules import resolve_module_name

    store: dict[str, dict] = {}

    async def fake_get_cache(key: str):
        return store.get(key)

    async def fake_set_cache(key: str, value: dict, *, generation: str):
        store[key] = value

    with (
        patch(f"{SERVICE}.get_module_resolution_cache", side_effect=fake_get_cache),
        patch(f"{SERVICE}.set_module_resolution_cache", side_effect=fake_set_cache),
        patch(f"{SERVICE}.get_module", new_callable=AsyncMock, return_value=None) as mock_get,
        patch(f"{SERVICE}._prefix_exists", new_callable=AsyncMock, return_value=False) as mock_prefix,
    ):
        first = await resolve_module_name("modules.missing", scope=ModuleSourceScope(solution_id=None, global_repo_access=False))
        second = await resolve_module_name("modules.missing", scope=ModuleSourceScope(solution_id=None, global_repo_access=False))

    assert first == {"kind": "not_found", "path": "modules/missing"}
    assert second == first
    assert mock_get.await_count == 2
    mock_prefix.assert_awaited_once_with("modules/missing/")


async def test_concurrent_cold_misses_share_one_storage_probe():
    from shared.sdk_modules import resolve_module_name

    store: dict[str, dict] = {}

    async def fake_get_cache(key: str):
        return store.get(key)

    async def fake_set_cache(key: str, value: dict, *, generation: str):
        store[key] = value

    async def fake_get_module(_path: str):
        await asyncio.sleep(0)
        return None

    with (
        patch(f"{SERVICE}.get_module_resolution_cache", side_effect=fake_get_cache),
        patch(f"{SERVICE}.set_module_resolution_cache", side_effect=fake_set_cache),
        patch(f"{SERVICE}.get_module", side_effect=fake_get_module) as mock_get,
        patch(f"{SERVICE}._prefix_exists", new_callable=AsyncMock, return_value=False) as mock_prefix,
    ):
        first, second = await asyncio.gather(
            resolve_module_name("modules.concurrent_missing", scope=ModuleSourceScope(solution_id=None, global_repo_access=False)),
            resolve_module_name("modules.concurrent_missing", scope=ModuleSourceScope(solution_id=None, global_repo_access=False)),
        )

    assert first == second == {
        "kind": "not_found",
        "path": "modules/concurrent_missing",
    }
    assert mock_get.await_count == 2
    mock_prefix.assert_awaited_once_with("modules/concurrent_missing/")


async def test_solution_namespace_precedes_global_concrete_module():
    from shared.sdk_modules import resolve_module_name

    async def fake_get_module(path: str):
        if path == "modules.py":
            return {"content": "GLOBAL = True", "path": path, "hash": "global"}
        return None

    async def fake_prefix_exists(prefix: str):
        return prefix == f"_solutions/{SOLUTION_A}/modules/"

    with (
        patch(f"{SERVICE}.get_module_resolution_cache", new_callable=AsyncMock, return_value=None),
        patch(f"{SERVICE}.set_module_resolution_cache", new_callable=AsyncMock),
        patch(f"{SERVICE}.get_module", side_effect=fake_get_module) as mock_get,
        patch(f"{SERVICE}._prefix_exists", side_effect=fake_prefix_exists),
    ):
        result = await resolve_module_name(
            "modules",
            scope=ModuleSourceScope(solution_id=SOLUTION_A, global_repo_access=True),
        )

    assert result == {"kind": "namespace", "path": "modules"}
    assert all(call.args[0] != "modules.py" for call in mock_get.await_args_list)


async def test_resolve_module_rehydrates_cached_module_metadata_from_module_cache():
    from shared.sdk_modules import resolve_module_name

    cached_metadata = {
        "kind": "module",
        "path": "modules/helpers.py",
        "storage_path": "modules/helpers.py",
        "hash": "old",
    }

    with (
        patch(f"{SERVICE}.get_module_resolution_cache", new_callable=AsyncMock, return_value=cached_metadata),
        patch(f"{SERVICE}.set_module_resolution_cache", new_callable=AsyncMock) as mock_set,
        patch(
            f"{SERVICE}.get_module",
            new_callable=AsyncMock,
            return_value={"content": "VALUE = 1", "path": "modules/helpers.py", "hash": "fresh"},
        ) as mock_get,
        patch(f"{SERVICE}._prefix_exists", new_callable=AsyncMock) as mock_prefix,
    ):
        result = await resolve_module_name("modules.helpers", scope=ModuleSourceScope(solution_id=None, global_repo_access=False))

    assert result == {
        "kind": "module",
        "path": "modules/helpers.py",
        "storage_path": "modules/helpers.py",
        "content": "VALUE = 1",
        "hash": "fresh",
    }
    mock_get.assert_awaited_once_with("modules/helpers.py")
    mock_set.assert_not_called()
    mock_prefix.assert_not_called()
