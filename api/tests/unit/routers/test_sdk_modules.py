import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from src.core.constants import SYSTEM_USER_UUID
from src.core.principal import UserPrincipal


def _system_user() -> UserPrincipal:
    return UserPrincipal(
        user_id=SYSTEM_USER_UUID,
        email="engine@bifrost.internal",
        organization_id=None,
        is_superuser=True,
    )


async def test_system_resolver_uses_signed_scope_not_query_scope():
    from src.routers.sdk_modules import resolve_module

    signed_solution = "12345678-1234-5678-1234-567812345678"
    requested_solution = "87654321-4321-8765-4321-876543218765"
    request = MagicMock()
    request.headers = {"authorization": "Bearer signed-token"}

    with (
        patch(
            "src.routers.sdk_modules.decode_token",
            return_value={
                "engine_execution_id": "execution-1",
                "engine_solution_id": signed_solution,
                "engine_global_repo_access": False,
            },
        ),
        patch(
            "src.routers.sdk_modules._resolve_module_name",
            new_callable=AsyncMock,
            return_value={"kind": "not_found", "path": "modules/missing"},
        ) as resolver,
    ):
        await resolve_module(
            request=request,
            user=_system_user(),
            name="modules.missing",
            solution_id=requested_solution,
            global_repo_access=True,
        )

    resolver.assert_awaited_once_with(
        "modules.missing",
        solution_id=signed_solution,
        global_repo_access=False,
    )


def test_system_module_path_cannot_cross_signed_solution_scope():
    from src.routers.sdk_modules import (
        _EngineModuleScope,
        _validate_engine_storage_path,
    )

    scope = _EngineModuleScope(
        solution_id="12345678-1234-5678-1234-567812345678",
        global_repo_access=False,
    )
    _validate_engine_storage_path(
        "_solutions/12345678-1234-5678-1234-567812345678/modules/ok.py",
        scope,
    )

    with pytest.raises(HTTPException) as cross_solution:
        _validate_engine_storage_path(
            "_solutions/87654321-4321-8765-4321-876543218765/modules/no.py",
            scope,
        )
    assert cross_solution.value.status_code == 403

    with pytest.raises(HTTPException) as workspace:
        _validate_engine_storage_path("modules/no.py", scope)
    assert workspace.value.status_code == 403


async def test_resolve_module_returns_solution_module_before_repo_fallback():
    from src.routers.sdk_modules import _resolve_module_name

    solution_id = "12345678-1234-5678-1234-567812345678"

    async def fake_get_module(path: str):
        if path == f"_solutions/{solution_id}/modules/helpers.py":
            return {"content": "VALUE = 'solution'", "path": path, "hash": "abc"}
        if path == "modules/helpers.py":
            return {"content": "VALUE = 'repo'", "path": path, "hash": "def"}
        return None

    with (
        patch("src.routers.sdk_modules.get_module_resolution_cache", new_callable=AsyncMock, return_value=None),
        patch("src.routers.sdk_modules.set_module_resolution_cache", new_callable=AsyncMock),
        patch("src.routers.sdk_modules.get_module", side_effect=fake_get_module) as mock_get,
    ):
        result = await _resolve_module_name(
            "modules.helpers",
            solution_id=solution_id,
            global_repo_access=True,
        )

    assert result == {
        "kind": "module",
        "path": "modules/helpers.py",
        "storage_path": f"_solutions/{solution_id}/modules/helpers.py",
        "content": "VALUE = 'solution'",
        "hash": "abc",
    }
    assert mock_get.await_args_list[0].args == (
        f"_solutions/{solution_id}/modules/helpers.py",
    )


async def test_resolve_module_does_not_fallback_to_repo_when_global_access_off():
    from src.routers.sdk_modules import _resolve_module_name

    solution_id = "12345678-1234-5678-1234-567812345678"

    with (
        patch("src.routers.sdk_modules.get_module_resolution_cache", new_callable=AsyncMock, return_value=None),
        patch("src.routers.sdk_modules.set_module_resolution_cache", new_callable=AsyncMock),
        patch("src.routers.sdk_modules.get_module", new_callable=AsyncMock, return_value=None) as mock_get,
        patch("src.routers.sdk_modules._prefix_exists", new_callable=AsyncMock, return_value=False),
    ):
        result = await _resolve_module_name(
            "modules.helpers",
            solution_id=solution_id,
            global_repo_access=False,
        )

    assert result == {"kind": "not_found", "path": "modules/helpers"}
    assert [call.args[0] for call in mock_get.await_args_list] == [
        f"_solutions/{solution_id}/modules/helpers.py",
        f"_solutions/{solution_id}/modules/helpers/__init__.py",
    ]


async def test_resolve_module_uses_workspace_without_solution_context():
    from src.routers.sdk_modules import _resolve_module_name

    workspace_module = {
        "content": "VALUE = 'workspace'",
        "path": "modules/helpers.py",
        "hash": "workspace-hash",
    }

    async def fake_get_module(path: str):
        return workspace_module if path == "modules/helpers.py" else None

    with (
        patch("src.routers.sdk_modules.get_module_resolution_cache", new_callable=AsyncMock, return_value=None),
        patch("src.routers.sdk_modules.set_module_resolution_cache", new_callable=AsyncMock),
        patch("src.routers.sdk_modules.get_module", side_effect=fake_get_module) as mock_get,
    ):
        result = await _resolve_module_name("modules.helpers")

    assert result == {
        "kind": "module",
        "path": "modules/helpers.py",
        "storage_path": "modules/helpers.py",
        **workspace_module,
    }
    mock_get.assert_awaited_once_with("modules/helpers.py")


async def test_resolve_module_falls_back_to_workspace_when_global_access_on():
    from src.routers.sdk_modules import _resolve_module_name

    solution_id = "12345678-1234-5678-1234-567812345678"

    async def fake_get_module(path: str):
        if path == "modules/helpers.py":
            return {
                "content": "VALUE = 'workspace'",
                "path": path,
                "hash": "workspace-hash",
            }
        return None

    with (
        patch("src.routers.sdk_modules.get_module_resolution_cache", new_callable=AsyncMock, return_value=None),
        patch("src.routers.sdk_modules.set_module_resolution_cache", new_callable=AsyncMock),
        patch("src.routers.sdk_modules.get_module", side_effect=fake_get_module) as mock_get,
        patch("src.routers.sdk_modules._prefix_exists", new_callable=AsyncMock, return_value=False),
    ):
        result = await _resolve_module_name(
            "modules.helpers",
            solution_id=solution_id,
            global_repo_access=True,
        )

    assert result == {
        "kind": "module",
        "path": "modules/helpers.py",
        "storage_path": "modules/helpers.py",
        "content": "VALUE = 'workspace'",
        "hash": "workspace-hash",
    }
    assert [call.args[0] for call in mock_get.await_args_list] == [
        f"_solutions/{solution_id}/modules/helpers.py",
        f"_solutions/{solution_id}/modules/helpers/__init__.py",
        "modules/helpers.py",
    ]


async def test_resolve_module_uses_bounded_prefix_check_for_namespace():
    from src.routers.sdk_modules import _resolve_module_name

    with (
        patch("src.routers.sdk_modules.get_module_resolution_cache", new_callable=AsyncMock, return_value=None),
        patch("src.routers.sdk_modules.set_module_resolution_cache", new_callable=AsyncMock),
        patch("src.routers.sdk_modules.get_module", new_callable=AsyncMock, return_value=None),
        patch("src.routers.sdk_modules._prefix_exists", new_callable=AsyncMock, return_value=True) as mock_prefix,
    ):
        result = await _resolve_module_name("modules")

    assert result == {"kind": "namespace", "path": "modules"}
    mock_prefix.assert_awaited_once_with("modules/")


async def test_resolve_module_uses_cached_not_found_before_storage():
    from src.routers.sdk_modules import _resolve_module_name

    store: dict[str, dict] = {}

    async def fake_get_cache(key: str):
        return store.get(key)

    async def fake_set_cache(key: str, value: dict):
        store[key] = value

    with (
        patch("src.routers.sdk_modules.get_module_resolution_cache", side_effect=fake_get_cache),
        patch("src.routers.sdk_modules.set_module_resolution_cache", side_effect=fake_set_cache),
        patch("src.routers.sdk_modules.get_module", new_callable=AsyncMock, return_value=None) as mock_get,
        patch("src.routers.sdk_modules._prefix_exists", new_callable=AsyncMock, return_value=False) as mock_prefix,
    ):
        first = await _resolve_module_name("modules.missing")
        second = await _resolve_module_name("modules.missing")

    assert first == {"kind": "not_found", "path": "modules/missing"}
    assert second == first
    assert mock_get.await_count == 2
    mock_prefix.assert_awaited_once_with("modules/missing/")


async def test_concurrent_cold_misses_share_one_storage_probe():
    from src.routers.sdk_modules import _resolve_module_name

    store: dict[str, dict] = {}

    async def fake_get_cache(key: str):
        return store.get(key)

    async def fake_set_cache(key: str, value: dict):
        store[key] = value

    async def fake_get_module(_path: str):
        await asyncio.sleep(0)
        return None

    with (
        patch("src.routers.sdk_modules.get_module_resolution_cache", side_effect=fake_get_cache),
        patch("src.routers.sdk_modules.set_module_resolution_cache", side_effect=fake_set_cache),
        patch("src.routers.sdk_modules.get_module", side_effect=fake_get_module) as mock_get,
        patch("src.routers.sdk_modules._prefix_exists", new_callable=AsyncMock, return_value=False) as mock_prefix,
    ):
        first, second = await asyncio.gather(
            _resolve_module_name("modules.concurrent_missing"),
            _resolve_module_name("modules.concurrent_missing"),
        )

    assert first == second == {
        "kind": "not_found",
        "path": "modules/concurrent_missing",
    }
    assert mock_get.await_count == 2
    mock_prefix.assert_awaited_once_with("modules/concurrent_missing/")


async def test_solution_namespace_precedes_global_concrete_module():
    from src.routers.sdk_modules import _resolve_module_name

    solution_id = "12345678-1234-5678-1234-567812345678"

    async def fake_get_module(path: str):
        if path == "modules.py":
            return {"content": "GLOBAL = True", "path": path, "hash": "global"}
        return None

    async def fake_prefix_exists(prefix: str):
        return prefix == f"_solutions/{solution_id}/modules/"

    with (
        patch("src.routers.sdk_modules.get_module_resolution_cache", new_callable=AsyncMock, return_value=None),
        patch("src.routers.sdk_modules.set_module_resolution_cache", new_callable=AsyncMock),
        patch("src.routers.sdk_modules.get_module", side_effect=fake_get_module) as mock_get,
        patch("src.routers.sdk_modules._prefix_exists", side_effect=fake_prefix_exists),
    ):
        result = await _resolve_module_name(
            "modules",
            solution_id=solution_id,
            global_repo_access=True,
        )

    assert result == {"kind": "namespace", "path": "modules"}
    assert all(call.args[0] != "modules.py" for call in mock_get.await_args_list)


async def test_resolve_module_rehydrates_cached_module_metadata_from_module_cache():
    from src.routers.sdk_modules import _resolve_module_name

    cached_metadata = {
        "kind": "module",
        "path": "modules/helpers.py",
        "storage_path": "modules/helpers.py",
        "hash": "old",
    }

    with (
        patch("src.routers.sdk_modules.get_module_resolution_cache", new_callable=AsyncMock, return_value=cached_metadata),
        patch("src.routers.sdk_modules.set_module_resolution_cache", new_callable=AsyncMock) as mock_set,
        patch(
            "src.routers.sdk_modules.get_module",
            new_callable=AsyncMock,
            return_value={"content": "VALUE = 1", "path": "modules/helpers.py", "hash": "fresh"},
        ) as mock_get,
        patch("src.routers.sdk_modules._prefix_exists", new_callable=AsyncMock) as mock_prefix,
    ):
        result = await _resolve_module_name("modules.helpers")

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
