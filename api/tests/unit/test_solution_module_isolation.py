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


@pytest.fixture(autouse=True)
def _restore_virtual_import_hook():
    """Importing worker installs the production hook; do not leak it to later tests."""
    from src.services.execution import virtual_import

    meta_path_before = list(sys.meta_path)
    finder_before = virtual_import._finder
    yield
    sys.meta_path[:] = meta_path_before
    virtual_import._finder = finder_before


def _fake_solution_module(
    name: str,
    rel_path: str,
    content_hash: str,
    storage_path: str,
):
    """A module object as VirtualModuleLoader ACTUALLY creates it: __file__ is the
    BARE relative path (e.g. 'modules/foo.py'), NOT a _solutions/{id}/-rooted one.
    (An earlier version of this fixture used a fictional _solutions/-rooted
    __file__, which only ever exercised a dead force-evict block — see the removed
    block in simple_worker. This matches reality so the test verifies the REAL
    hash-check eviction.)"""
    from src.services.execution.virtual_import import VirtualModuleLoader

    m = types.ModuleType(name)
    m.__file__ = rel_path
    m.__content_hash__ = content_hash  # type: ignore[attr-defined]  # dynamic attr the loader sets
    m.__storage_path__ = storage_path  # type: ignore[attr-defined]
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
    import src.services.execution.simple_worker as sw

    sid_b = str(uuid.uuid4())

    sid_a = str(uuid.uuid4())
    sys.modules["modules.foo"] = _fake_solution_module(
        "modules.foo",
        "modules/foo.py",
        "same-hash",
        f"_solutions/{sid_a}/modules/foo.py",
    )
    monkeypatch.setattr(
        mcs,
        "get_module_sync",
        lambda _p: {
            "hash": "same-hash",
            "storage_path": f"_solutions/{sid_b}/modules/foo.py",
        },
    )

    # Now Solution B is the active execution.
    mcs.set_solution_context(sid_b, global_repo_access=False)
    try:
        sw._clear_workspace_modules()
    finally:
        mcs._solution_ctx.value = None

    # A's modules.foo must be gone so B re-imports from its own root.
    assert "modules.foo" not in sys.modules, (
        "a different solution's cached module bled into this execution"
    )


def test_same_solution_keeps_its_module(_clean_sys_modules, monkeypatch):
    import src.core.module_cache_sync as mcs
    import src.services.execution.simple_worker as sw

    sid = str(uuid.uuid4())
    storage_path = f"_solutions/{sid}/modules/foo.py"
    sys.modules["modules.foo"] = _fake_solution_module(
        "modules.foo",
        "modules/foo.py",
        "hashA",
        storage_path,
    )
    monkeypatch.setattr(
        mcs,
        "get_module_sync",
        lambda _p: {"hash": "hashA", "storage_path": storage_path},
    )

    mcs.set_solution_context(sid, global_repo_access=False)
    try:
        sw._clear_workspace_modules()
    finally:
        mcs._solution_ctx.value = None

    # Known module + unchanged content → kept.
    assert "modules.foo" in sys.modules


async def test_execute_async_sets_solution_context_before_clearing_modules(monkeypatch):
    """Codex #9: the persistent-worker path must activate the execution's
    Solution context BEFORE evicting workspace modules, or the cross-solution
    eviction runs blind and a prior install's same-name module survives. Assert
    set_solution_context runs before _clear_workspace_modules, with the context's
    own solution_id."""
    import src.services.execution.simple_worker as sw
    import src.core.module_cache_sync as mcs

    sid = str(uuid.uuid4())
    calls: list[tuple[str, object]] = []

    context = {"solution_id": sid, "solution_global_repo_access": False}

    def _fake_set_ctx(solution_id, global_repo_access=False):
        calls.append(("set_context", solution_id))

    def _fake_clear():
        calls.append(("clear_modules", None))

    def _fake_clear_ctx():
        calls.append(("clear_context", None))

    async def _fake_run(_eid, _ctx):
        calls.append(("run", None))
        return {"status": "Success", "result": {}, "metrics": {}}

    monkeypatch.setattr(mcs, "set_solution_context", _fake_set_ctx)
    monkeypatch.setattr(mcs, "clear_solution_context", _fake_clear_ctx)
    monkeypatch.setattr(sw, "_clear_workspace_modules", _fake_clear)
    monkeypatch.setattr(sw, "_get_pss_bytes", lambda: 0)
    # _run_execution is imported inside the function from worker; patch there.
    import src.services.execution.worker as worker_mod
    monkeypatch.setattr(worker_mod, "_run_execution", _fake_run)

    await sw._execute_async("exec-1", "worker-1", context)

    order = [name for name, _ in calls]
    assert order.index("set_context") < order.index("clear_modules"), (
        f"context must be set before clearing modules; got {order}"
    )
    assert order.index("clear_modules") < order.index("clear_context")
    assert order.index("clear_context") < order.index("run"), (
        f"eviction-only context must be clear before credential bootstrap; got {order}"
    )
    # The context activated is THIS execution's install.
    assert ("set_context", sid) in calls
