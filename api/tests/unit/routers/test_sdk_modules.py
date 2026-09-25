import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from shared.sdk_modules import ModuleSourceScope
from src.core.constants import SYSTEM_USER_UUID
from src.core.principal import UserPrincipal

SERVICE = "shared.sdk_modules"

SOLUTION_A = "12345678-1234-5678-1234-567812345678"
SOLUTION_B = "87654321-4321-8765-4321-876543218765"


def _system_user() -> UserPrincipal:
    return UserPrincipal(
        user_id=SYSTEM_USER_UUID,
        email="engine@bifrost.internal",
        organization_id=None,
        is_superuser=True,
    )


def _admin_user() -> UserPrincipal:
    return UserPrincipal(
        user_id="11111111-1111-1111-1111-111111111111",
        email="admin@gobifrost.com",
        organization_id=None,
        is_superuser=True,
    )


def _request_with_bearer(token: str = "signed-token") -> MagicMock:
    request = MagicMock()
    request.headers = {"authorization": f"Bearer {token}"}
    return request


async def test_requirements_http_contract_is_retained():
    from src.routers.sdk_modules import fetch_requirements

    with patch(
        "src.routers.sdk_modules.get_requirements",
        new_callable=AsyncMock,
        return_value={"content": "example==1\n", "hash": "digest"},
    ):
        response = await fetch_requirements(_admin_user())
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
        await fetch_requirements(_admin_user())
    assert exc.value.status_code == 404


# --- Router: engine proof ---


async def test_system_caller_without_bearer_token_is_rejected():
    from src.routers.sdk_modules import resolve_module

    request = MagicMock()
    request.headers = {"authorization": ""}

    with pytest.raises(HTTPException) as exc:
        await resolve_module(
            request=request,
            user=_system_user(),
            name="modules.missing",
        )
    assert exc.value.status_code == 403
    assert exc.value.detail == "Execution-scoped engine token required"


async def test_system_caller_without_execution_id_claim_is_rejected():
    from src.routers.sdk_modules import fetch_module

    request = _request_with_bearer()

    with (
        patch(
            "src.routers.sdk_modules.decode_token",
            return_value={"engine_solution_id": SOLUTION_A},
        ),
        pytest.raises(HTTPException) as exc,
    ):
        await fetch_module(
            path="modules/ok.py",
            request=request,
            user=_system_user(),
        )
    assert exc.value.status_code == 403
    assert exc.value.detail == "Execution-scoped engine token required"


async def test_system_caller_with_undecodable_token_is_rejected():
    from src.routers.sdk_modules import resolve_module

    with (
        patch("src.routers.sdk_modules.decode_token", return_value=None),
        pytest.raises(HTTPException) as exc,
    ):
        await resolve_module(
            request=_request_with_bearer(),
            user=_system_user(),
            name="modules.missing",
        )
    assert exc.value.status_code == 403


async def test_system_resolver_uses_signed_scope_not_query_scope():
    from src.routers.sdk_modules import resolve_module

    request = _request_with_bearer()

    with (
        patch(
            "src.routers.sdk_modules.decode_token",
            return_value={
                "engine_execution_id": "execution-1",
                "engine_solution_id": SOLUTION_A,
                "engine_global_repo_access": False,
            },
        ),
        patch(
            "src.routers.sdk_modules.resolve_module_name",
            new_callable=AsyncMock,
            return_value={"kind": "not_found", "path": "modules/missing"},
        ) as resolver,
    ):
        await resolve_module(
            request=request,
            user=_system_user(),
            name="modules.missing",
            solution_id=SOLUTION_B,
            global_repo_access=True,
        )

    resolver.assert_awaited_once_with(
        "modules.missing",
        scope=ModuleSourceScope(solution_id=SOLUTION_A, global_repo_access=False),
    )


async def test_human_admin_keeps_query_scope():
    from src.routers.sdk_modules import resolve_module

    request = MagicMock()
    request.headers = {}

    with (
        patch(
            "src.routers.sdk_modules.resolve_module_name",
            new_callable=AsyncMock,
            return_value={"kind": "not_found", "path": "modules/missing"},
        ) as resolver,
    ):
        await resolve_module(
            request=request,
            user=_admin_user(),
            name="modules.missing",
            solution_id=SOLUTION_B,
            global_repo_access=True,
        )

    resolver.assert_awaited_once_with(
        "modules.missing",
        scope=ModuleSourceScope(solution_id=SOLUTION_B, global_repo_access=True),
    )


async def test_resolver_maps_invalid_name_to_400():
    from src.routers.sdk_modules import resolve_module

    with pytest.raises(HTTPException) as exc:
        await resolve_module(
            request=MagicMock(),
            user=_admin_user(),
            name="not a module!",
        )
    assert exc.value.status_code == 400


async def test_resolver_maps_invalid_solution_id_to_400():
    from src.routers.sdk_modules import resolve_module

    with pytest.raises(HTTPException) as exc:
        await resolve_module(
            request=MagicMock(),
            user=_admin_user(),
            name="modules.ok",
            solution_id="not-a-uuid",
        )
    assert exc.value.status_code == 400
    assert exc.value.detail == "Invalid solution_id"


# --- Router: direct fetch scope rules ---


def _engine_claims(solution_id: str | None, global_repo_access: bool = False) -> dict:
    return {
        "engine_execution_id": "execution-1",
        "engine_solution_id": solution_id,
        "engine_global_repo_access": global_repo_access,
    }


async def test_fetch_module_allows_own_solution_path():
    from src.routers.sdk_modules import fetch_module

    path = f"_solutions/{SOLUTION_A}/modules/ok.py"
    with (
        patch(
            "src.routers.sdk_modules.decode_token",
            return_value=_engine_claims(SOLUTION_A),
        ),
        patch(
            f"{SERVICE}.get_module",
            new_callable=AsyncMock,
            return_value={"content": "X = 1", "path": path, "hash": "h"},
        ),
    ):
        response = await fetch_module(
            path=path,
            request=_request_with_bearer(),
            user=_system_user(),
        )
    assert response.status_code == 200


async def test_fetch_module_forbids_sibling_solution_path():
    from src.routers.sdk_modules import fetch_module

    with (
        patch(
            "src.routers.sdk_modules.decode_token",
            return_value=_engine_claims(SOLUTION_A),
        ),
        pytest.raises(HTTPException) as exc,
    ):
        await fetch_module(
            path=f"_solutions/{SOLUTION_B}/modules/no.py",
            request=_request_with_bearer(),
            user=_system_user(),
        )
    assert exc.value.status_code == 403
    assert exc.value.detail == "Module path is outside execution scope"


async def test_fetch_module_forbids_bare_repo_path_when_sealed():
    from src.routers.sdk_modules import fetch_module

    with (
        patch(
            "src.routers.sdk_modules.decode_token",
            return_value=_engine_claims(SOLUTION_A, global_repo_access=False),
        ),
        pytest.raises(HTTPException) as exc,
    ):
        await fetch_module(
            path="modules/no.py",
            request=_request_with_bearer(),
            user=_system_user(),
        )
    assert exc.value.status_code == 403
    assert exc.value.detail == "Workspace module access is disabled for this Solution"


async def test_fetch_module_allows_bare_repo_path_with_global_access():
    from src.routers.sdk_modules import fetch_module

    with (
        patch(
            "src.routers.sdk_modules.decode_token",
            return_value=_engine_claims(SOLUTION_A, global_repo_access=True),
        ),
        patch(
            f"{SERVICE}.get_module",
            new_callable=AsyncMock,
            return_value={"content": "X = 1", "path": "modules/ok.py", "hash": "h"},
        ),
    ):
        response = await fetch_module(
            path="modules/ok.py",
            request=_request_with_bearer(),
            user=_system_user(),
        )
    assert response.status_code == 200


async def test_fetch_module_maps_traversal_to_400():
    from src.routers.sdk_modules import fetch_module

    with pytest.raises(HTTPException) as exc:
        await fetch_module(
            path="../escape.py",
            request=MagicMock(),
            user=_admin_user(),
        )
    assert exc.value.status_code == 400


async def test_fetch_module_maps_miss_to_404():
    from src.routers.sdk_modules import fetch_module

    with (
        patch(f"{SERVICE}.get_module", new_callable=AsyncMock, return_value=None),
        pytest.raises(HTTPException) as exc,
    ):
        await fetch_module(
            path="modules/missing.py",
            request=MagicMock(),
            user=_admin_user(),
        )
    assert exc.value.status_code == 404


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

    async def fake_set_cache(key: str, value: dict):
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

    async def fake_set_cache(key: str, value: dict):
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
