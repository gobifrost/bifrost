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


def _fake_solution_module(name: str, rel_path: str, content_hash: str):
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

    sid_b = str(uuid.uuid4())

    # Solution A imported modules.foo (bare __file__, as the loader produces).
    sys.modules["modules.foo"] = _fake_solution_module("modules.foo", "modules/foo.py", "hashA")

    # The module index is _repo/-keyed and doesn't know solution module paths, so
    # name_to_path can't map modules.foo → the eviction's "can't map a name to a
    # file path → clear to be safe" branch fires. THIS is the real isolation
    # mechanism (not the removed _solutions/-prefix block). Stub an empty index.
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
    sys.modules["modules.foo"] = _fake_solution_module("modules.foo", "modules/foo.py", "hashA")
    # Index maps the name to its path AND the cached hash MATCHES → the
    # content-change sweep KEEPS it (unchanged content isn't re-exec'd). This is
    # the perf path: a module the index knows and whose content is unchanged
    # survives, so the next import is a no-op.
    monkeypatch.setattr(mcs, "get_module_index_sync", lambda: ["modules/foo.py"])
    monkeypatch.setattr(mcs, "get_module_sync", lambda _p: {"hash": "hashA"})

    mcs.set_solution_context(sid, global_repo_access=False)
    try:
        _clear_workspace_modules()
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

    async def _fake_read_context(_eid):
        return {"solution_id": sid, "solution_global_repo_access": False}

    def _fake_set_ctx(solution_id, global_repo_access=False):
        calls.append(("set_context", solution_id))

    def _fake_clear():
        calls.append(("clear_modules", None))

    async def _fake_run(_eid, _ctx):
        calls.append(("run", None))
        return {"status": "Success", "result": {}, "metrics": {}}

    monkeypatch.setattr(sw, "_read_context_from_redis", _fake_read_context)
    monkeypatch.setattr(mcs, "set_solution_context", _fake_set_ctx)
    monkeypatch.setattr(sw, "_clear_workspace_modules", _fake_clear)
    monkeypatch.setattr(sw, "_get_pss_bytes", lambda: 0)
    # _run_execution is imported inside the function from worker; patch there.
    import src.services.execution.worker as worker_mod
    monkeypatch.setattr(worker_mod, "_run_execution", _fake_run)

    await sw._execute_async("exec-1", "worker-1")

    order = [name for name, _ in calls]
    assert order.index("set_context") < order.index("clear_modules"), (
        f"context must be set before clearing modules; got {order}"
    )
    assert order.index("clear_modules") < order.index("run")
    # The context activated is THIS execution's install.
    assert ("set_context", sid) in calls
