"""Per-execution Solution import root.

When a workflow carries a solution_id, module resolution must be rooted at
``_solutions/{solution_id}/`` — both for the entry workflow's own code and for
its ``from modules.x import y`` imports — and must fall back to the ad-hoc
``_repo/`` root ONLY when the install's global_repo_access flag is on
(success-criteria §3.5, criteria 3 & 4).

These tests pin the *path-resolution* contract in module_cache_sync, which is
the single chokepoint both the worker's entry-code load and the
VirtualModuleFinder go through. The live end-to-end proof (deploy a solution,
run it, assert imports resolve / global blocked) is exercised in the deploy
e2e once deploy exists.
"""
from __future__ import annotations

import uuid

import pytest

from src.core import module_cache_sync as mcs


@pytest.fixture(autouse=True)
def _clear_ctx():
    """Each test starts and ends with no active solution context."""
    mcs.clear_solution_context()
    yield
    mcs.clear_solution_context()


def test_no_context_resolves_to_repo_paths_only() -> None:
    """With no solution active, the candidate storage paths are exactly the
    bare path (i.e. unchanged _repo/ behavior — criterion 1)."""
    assert mcs.get_solution_context() is None
    assert mcs._candidate_storage_paths("modules/x.py") == ["modules/x.py"]


def test_solution_context_prefixes_first_no_fallback_when_global_off() -> None:
    sid = uuid.uuid4()
    mcs.set_solution_context(sid, global_repo_access=False)
    ctx = mcs.get_solution_context()
    assert ctx is not None and ctx.solution_id == str(sid)
    # global access OFF → only the solution-rooted path is a candidate.
    assert mcs._candidate_storage_paths("modules/x.py") == [f"_solutions/{sid}/modules/x.py"]


def test_solution_context_falls_back_to_repo_when_global_on() -> None:
    sid = uuid.uuid4()
    mcs.set_solution_context(sid, global_repo_access=True)
    # global access ON → solution-rooted FIRST, then bare (_repo) path.
    assert mcs._candidate_storage_paths("shared/util.py") == [
        f"_solutions/{sid}/shared/util.py",
        "shared/util.py",
    ]


def test_clear_restores_repo_only() -> None:
    sid = uuid.uuid4()
    mcs.set_solution_context(sid, global_repo_access=True)
    mcs.clear_solution_context()
    assert mcs.get_solution_context() is None
    assert mcs._candidate_storage_paths("a.py") == ["a.py"]
