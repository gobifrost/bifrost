"""Cross-install module isolation (criterion 3, Codex G6).

Per-execution import root namespaces module RESOLUTION, but Python caches
imported modules in ``sys.modules`` by bare name (``modules.foo``). Without
eviction, after Solution A's execution imports ``modules.foo`` from
``_solutions/A/...``, a reused worker running Solution B would get A's cached
``modules.foo`` instead of B's. ``_clear_workspace_modules`` must evict a
solution-rooted module when the active solution differs from the one that
loaded it.
"""
from __future__ import annotations

import sys
import types
import uuid

import pytest

pytestmark = pytest.mark.e2e


def _fake_solution_module(name: str, solution_id: str, content_hash: str):
    """A module object as the virtual loader would create it for a solution —
    carries a VirtualModuleLoader so _clear_workspace_modules sees it as a
    workspace module."""
    from src.services.execution.virtual_import import VirtualModuleLoader

    m = types.ModuleType(name)
    m.__file__ = f"_solutions/{solution_id}/modules/foo.py"
    m.__content_hash__ = content_hash
    # A minimal loader instance of the right type (only isinstance is checked).
    m.__loader__ = VirtualModuleLoader.__new__(VirtualModuleLoader)
    return m


@pytest.fixture
def _clean_sys_modules():
    before = dict(sys.modules)
    yield
    for k in set(sys.modules) - set(before):
        sys.modules.pop(k, None)


def test_switching_solution_evicts_other_solutions_module(_clean_sys_modules, monkeypatch):
    import src.core.module_cache_sync as mcs
    from src.services.execution.simple_worker import _clear_workspace_modules

    sid_a = str(uuid.uuid4())
    sid_b = str(uuid.uuid4())

    # Solution A imported modules.foo from its own root.
    sys.modules["modules.foo"] = _fake_solution_module("modules.foo", sid_a, "hashA")

    # The module index doesn't know about solution roots (it's _repo/-keyed),
    # so name_to_path can't map modules.foo to A's path — stub an empty index.
    monkeypatch.setattr(mcs, "get_module_index_sync", lambda: [])

    # Now Solution B is the active execution.
    mcs.set_solution_context(sid_b, global_repo_access=False)
    try:
        _clear_workspace_modules()
    finally:
        mcs._solution_ctx.value = None

    # A's modules.foo must be gone so B re-imports from its own root.
    assert "modules.foo" not in sys.modules, (
        "a different solution's cached module bled into this execution"
    )


def test_same_solution_keeps_its_module(_clean_sys_modules, monkeypatch):
    import src.core.module_cache_sync as mcs
    from src.services.execution.simple_worker import _clear_workspace_modules

    sid = str(uuid.uuid4())
    sys.modules["modules.foo"] = _fake_solution_module("modules.foo", sid, "hashA")
    # Index maps the name to its path; the cached hash MATCHES so the
    # content-change sweep keeps it. (We're isolating the cross-solution rule:
    # the same solution's module must NOT be force-evicted as "foreign".)
    monkeypatch.setattr(mcs, "get_module_index_sync", lambda: ["modules/foo.py"])
    monkeypatch.setattr(mcs, "get_module_sync", lambda _p: {"hash": "hashA"})

    mcs.set_solution_context(sid, global_repo_access=False)
    try:
        _clear_workspace_modules()
    finally:
        mcs._solution_ctx.value = None

    # Same solution + unchanged content → its own module stays.
    assert "modules.foo" in sys.modules
