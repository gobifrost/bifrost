"""Pure import-order coverage for the disposable ASGI controller seam."""

from __future__ import annotations

import asyncio
import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest


HERE = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location("cloudflare_live_asgi_seam", HERE / "asgi_seam.py")
assert SPEC is not None and SPEC.loader is not None
seam = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(seam)


def test_guard_rejects_missing_or_malformed_scope_without_importing_control() -> None:
    imports = []

    def importer(name):
        imports.append(name)
        raise AssertionError("must not import")

    with pytest.raises(seam.ConformanceAsgiError, match="invalid or missing"):
        seam.disable_capacity_loops(environ={}, importer=importer)
    with pytest.raises(seam.ConformanceAsgiError, match="invalid or missing"):
        seam.disable_capacity_loops(environ={"BIFROST_CONFORMANCE_SCOPE": "bad scope"}, importer=importer)
    assert imports == []


def test_guard_replaces_only_capacity_loop_factory_before_main_import() -> None:
    async def original_loop(*_args):
        raise AssertionError("original loop must not run")

    control = SimpleNamespace(start_kubernetes_loops=original_loop, unrelated="preserved")
    imported = []

    def importer(name):
        imported.append(name)
        assert name == "src.services.kubernetes_control"
        return control

    assert seam.disable_capacity_loops(
        environ={"BIFROST_CONFORMANCE_SCOPE": "suite.scope"}, importer=importer,
    ) == "suite.scope"
    assert imported == ["src.services.kubernetes_control"]
    assert control.unrelated == "preserved"
    assert control.start_kubernetes_loops is seam.no_capacity_loops
    assert asyncio.run(control.start_kubernetes_loops(object(), object())) == []
