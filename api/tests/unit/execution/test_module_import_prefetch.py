"""Bounded static-import batching for the virtual module finder."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import httpx
import pytest

from src.core import module_cache_sync as mcs
from src.core.module_cache_sync import ModuleResolution
from src.services.execution import virtual_import


def _module(
    *,
    path: str,
    content: str,
    storage_path: str | None = None,
) -> ModuleResolution:
    return ModuleResolution(
        kind="module",
        path=path,
        storage_path=storage_path or path,
        content=content,
        hash="digest",
    )


def test_static_import_discovery_ignores_conditional_and_dynamic_imports() -> None:
    names = virtual_import._module_body_import_names(
        """
import helpers.client
from workspace import widgets
if enabled:
    import conditional
try:
    import optional
except ImportError:
    pass
def load_later():
    import deferred
importlib.import_module('dynamic')
""",
        fullname="workflows.entry",
        is_package=False,
    )

    assert names == [
        "helpers",
        "helpers.client",
        "workspace",
    ]


def test_static_import_discovery_does_not_probe_from_import_attributes() -> None:
    names = virtual_import._module_body_import_names(
        """
from service.models import User, Team
from helpers import make_client
""",
        fullname="workflows.entry",
        is_package=False,
    )

    assert names == ["service", "service.models", "helpers"]


def test_static_import_discovery_does_not_probe_relative_from_import_attributes() -> None:
    names = virtual_import._module_body_import_names(
        "from .models import User, Team\n",
        fullname="modules.feature",
        is_package=True,
    )

    assert names == ["modules.feature.models"]


def test_static_import_discovery_resolves_relative_imports_from_package() -> None:
    names = virtual_import._module_body_import_names(
        """
from . import sibling
from ..common import helpers
""",
        fullname="modules.feature",
        is_package=True,
    )

    assert names == ["modules.feature.sibling", "modules.common"]


def test_virtual_finder_prefetches_workspace_graph_but_not_installed_dependency() -> None:
    finder = virtual_import.VirtualModuleFinder()
    entry = _module(
        path="workflows/entry.py",
        content="""
import sdk_helpers
from workspace import client
import httpx
if False:
    import deferred
""",
    )
    installed = MagicMock(origin="/venv/site-packages/httpx/__init__.py")
    with (
        patch.object(virtual_import, "resolve_module_sync", return_value=entry),
        patch.object(
            virtual_import.PathFinder,
            "find_spec",
            side_effect=lambda name, path=None: installed if name == "httpx" else None,
        ),
        patch.object(virtual_import, "prefetch_module_resolutions") as prefetch,
    ):
        assert finder._find_spec_impl("workflows.entry") is not None

    prefetch.assert_called_once_with(
        ["sdk_helpers", "workspace"]
    )


def test_prefetch_warms_actual_virtual_import_graph_with_one_engine_request() -> None:
    mcs.clear_solution_context()
    client = MagicMock()
    client.engine_request_sync.return_value = httpx.Response(
        200,
        json={
            "modules": {
                "workspace": {
                    "kind": "namespace",
                    "path": "workspace",
                },
                "workspace.client": {
                    "kind": "module",
                    "path": "workspace/client.py",
                    "storage_path": "workspace/client.py",
                    "content": "VALUE = 1\n",
                    "hash": "client",
                },
                "sdk_helpers": {
                    "kind": "module",
                    "path": "sdk_helpers.py",
                    "storage_path": "sdk_helpers.py",
                    "content": "HELPER = 1\n",
                    "hash": "helper",
                },
            },
            "errors": {},
        },
    )
    try:
        with patch.object(mcs, "_get_engine_client", return_value=client):
            mcs.prefetch_module_resolutions(
                ["workspace", "workspace.client", "sdk_helpers"]
            )
            assert mcs.resolve_module_sync("workspace.client").content == "VALUE = 1\n"
            assert mcs.resolve_module_sync("sdk_helpers").content == "HELPER = 1\n"
    finally:
        mcs.clear_solution_context()

    client.engine_request_sync.assert_called_once_with(
        "POST",
        "/api/sdk/modules-resolve-batch",
        params={},
        json={
            "names": ["workspace", "workspace.client", "sdk_helpers"],
            "max_sources": 32,
            "max_source_bytes": 1_048_576,
        },
    )


def test_prefetch_enforces_client_source_budget_and_defers_excluded_name() -> None:
    mcs.clear_solution_context()
    client = MagicMock()
    client.engine_request_sync.return_value = httpx.Response(
        200,
        json={
            "modules": {
                "first": {
                    "kind": "module",
                    "path": "first.py",
                    "storage_path": "first.py",
                    "content": "abc",
                    "hash": "first",
                },
                "second": {
                    "kind": "module",
                    "path": "second.py",
                    "storage_path": "second.py",
                    "content": "def",
                    "hash": "second",
                },
            },
            "errors": {},
        },
    )
    mcs._solution_ctx.prefetch_budget = mcs.ModulePrefetchBudget(
        remaining_sources=1,
        remaining_bytes=3,
    )
    try:
        with patch.object(mcs, "_get_engine_client", return_value=client):
            mcs.prefetch_module_resolutions(["first", "second"])
            assert mcs.resolve_module_sync("first").content == "abc"
            client.engine_request_sync.reset_mock()
            client.engine_request_sync.return_value = httpx.Response(
                200,
                json={
                    "kind": "module",
                    "path": "second.py",
                    "storage_path": "second.py",
                    "content": "def",
                    "hash": "second",
                },
            )
            assert mcs.resolve_module_sync("second").content == "def"
    finally:
        mcs.clear_solution_context()

    client.engine_request_sync.assert_called_once_with(
        "GET",
        "/api/sdk/modules-resolve",
        params={"name": "second"},
    )


def test_prefetch_error_is_deferred_to_the_existing_single_name_path() -> None:
    mcs.clear_solution_context()
    client = MagicMock()
    client.engine_request_sync.side_effect = [
        httpx.Response(
            200,
            json={
                "modules": {},
                "errors": {
                    "unavailable": {
                        "status_code": 503,
                        "detail": "storage unavailable",
                    }
                },
            },
        ),
        httpx.Response(503, json={"detail": "storage unavailable"}),
    ]
    try:
        with patch.object(mcs, "_get_engine_client", return_value=client):
            mcs.prefetch_module_resolutions(["unavailable"])
            with pytest.raises(mcs.ModuleResolutionError, match="returned 503"):
                mcs.resolve_module_sync("unavailable")
    finally:
        mcs.clear_solution_context()

    assert client.engine_request_sync.call_args_list[1].args == (
        "GET",
        "/api/sdk/modules-resolve",
    )


def test_prefetch_preserves_solution_scope_on_the_signed_engine_route() -> None:
    client = MagicMock()
    client.engine_request_sync.return_value = httpx.Response(
        200,
        json={
            "modules": {"missing": {"kind": "not_found", "path": "missing"}},
            "errors": {},
        },
    )
    mcs.set_solution_context("solution-a", global_repo_access=False)
    try:
        with patch.object(mcs, "_get_engine_client", return_value=client):
            mcs.prefetch_module_resolutions(["missing"])
    finally:
        mcs.clear_solution_context()

    assert client.engine_request_sync.call_args.kwargs["params"] == {
        "solution_id": "solution-a",
        "global_repo_access": False,
    }


def test_solution_context_reset_drops_prefetch_source_budget() -> None:
    mcs.set_solution_context("solution-a", global_repo_access=False)
    first_budget = mcs._get_prefetch_budget()
    first_budget.remaining_sources = 0
    first_budget.remaining_bytes = 0

    mcs.set_solution_context("solution-b", global_repo_access=True)
    second_budget = mcs._get_prefetch_budget()
    assert second_budget.remaining_sources == mcs.MODULE_PREFETCH_MAX_SOURCES
    assert second_budget.remaining_bytes == mcs.MODULE_PREFETCH_MAX_SOURCE_BYTES

    mcs.clear_solution_context()
