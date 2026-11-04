"""Contracts for bounded speculative SDK module-resolution batches."""

from __future__ import annotations

import asyncio
from unittest.mock import patch

from shared import sdk_modules
from shared.sdk_modules import ModuleSourceError, ModuleSourceScope


async def test_batch_resolves_names_serially_under_one_scope() -> None:
    active = 0
    peak_active = 0
    scopes: list[ModuleSourceScope] = []

    async def resolve(name: str, *, scope: ModuleSourceScope) -> dict[str, object]:
        nonlocal active, peak_active
        active += 1
        peak_active = max(peak_active, active)
        scopes.append(scope)
        await asyncio.sleep(0)
        active -= 1
        return {"kind": "not_found", "path": name.replace(".", "/")}

    scope = ModuleSourceScope(solution_id="solution", global_repo_access=False)
    with patch.object(sdk_modules, "resolve_module_name", side_effect=resolve):
        modules, errors = await sdk_modules.resolve_module_names(
            ["one", "two", "three"],
            scope=scope,
            max_sources=32,
            max_source_bytes=1_048_576,
        )

    assert peak_active == 1
    assert scopes == [scope, scope, scope]
    assert modules == {
        "one": {"kind": "not_found", "path": "one"},
        "two": {"kind": "not_found", "path": "two"},
        "three": {"kind": "not_found", "path": "three"},
    }
    assert errors == {}


async def test_batch_returns_error_without_caching_or_raising_speculation() -> None:
    scope = ModuleSourceScope(solution_id=None, global_repo_access=False)

    async def resolve(name: str, *, scope: ModuleSourceScope) -> dict[str, object]:
        if name == "unavailable":
            raise ModuleSourceError(503, "storage unavailable")
        return {
            "kind": "module",
            "path": "ready.py",
            "storage_path": "ready.py",
            "content": "VALUE = 1\n",
            "hash": "ready",
        }

    with patch.object(sdk_modules, "resolve_module_name", side_effect=resolve):
        modules, errors = await sdk_modules.resolve_module_names(
            ["unavailable", "ready"],
            scope=scope,
            max_sources=32,
            max_source_bytes=1_048_576,
        )

    assert modules == {
        "ready": {
            "kind": "module",
            "path": "ready.py",
            "storage_path": "ready.py",
            "content": "VALUE = 1\n",
            "hash": "ready",
        }
    }
    assert errors == {
        "unavailable": {"status_code": 503, "detail": "storage unavailable"}
    }


async def test_batch_caps_source_result_count_and_bytes() -> None:
    scope = ModuleSourceScope(solution_id=None, global_repo_access=False)

    async def resolve(name: str, *, scope: ModuleSourceScope) -> dict[str, object]:
        return {
            "kind": "module",
            "path": f"{name}.py",
            "storage_path": f"{name}.py",
            "content": "abc" if name == "small" else "toolarge",
            "hash": name,
        }

    with patch.object(sdk_modules, "resolve_module_name", side_effect=resolve):
        modules, errors = await sdk_modules.resolve_module_names(
            ["small", "large"],
            scope=scope,
            max_sources=1,
            max_source_bytes=3,
        )

    assert set(modules) == {"small"}
    assert errors == {}


async def test_batch_stops_speculative_probes_after_storage_failure() -> None:
    scope = ModuleSourceScope(solution_id=None, global_repo_access=False)
    with patch.object(
        sdk_modules, "resolve_module_name", side_effect=RuntimeError("storage unavailable")
    ) as resolve:
        modules, errors = await sdk_modules.resolve_module_names(
            ["first", "second"], scope=scope, max_sources=32, max_source_bytes=1_048_576,
        )

    resolve.assert_awaited_once_with("first", scope=scope)
    assert modules == {}
    assert set(errors) == {"first"}
