"""Discover and run a Solution workspace's local @workflow functions in-process.

This is the "local function host" behind `bifrost solution start`: it imports the
workspace's decorated functions (any folder layout) and runs them directly,
mirroring `bifrost run`'s offline execution — nothing is registered to the API.
"""
from __future__ import annotations

import importlib.util
import inspect
import logging
import sys
from pathlib import Path
from types import ModuleType
from typing import Any, Callable

logger = logging.getLogger("bifrost.solution_dev")

# Folders that never hold solution source — skip for speed and to avoid
# importing build output / deps. (Discovery is intentionally layout-agnostic:
# a @workflow anywhere is resolvable by its path::fn, exactly as the platform
# resolves it — we don't restrict source to particular dirs.)
_SKIP_DIRS = {"node_modules", "dist", ".venv", "venv", "__pycache__", ".git", ".bifrost"}


def discover_functions(workspace: Path) -> dict[str, Callable[..., Any]]:
    """Map ``path::function_name`` → callable for every decorated function.

    ``path`` is workspace-relative with POSIX separators (the same form app code
    passes to ``useWorkflow``). The workspace root is placed on ``sys.path`` so a
    function's ``from modules.x import y`` resolves against the solution root.
    """
    workspace = workspace.resolve()
    root_str = str(workspace)
    if root_str not in sys.path:
        sys.path.insert(0, root_str)

    out: dict[str, Callable[..., Any]] = {}
    for py in sorted(workspace.rglob("*.py")):
        rel_parts = py.relative_to(workspace).parts
        if any(part in _SKIP_DIRS for part in rel_parts):
            continue
        rel = py.relative_to(workspace).as_posix()
        module = _load_module(py, rel)
        if module is None:
            continue
        for name in dir(module):
            obj = getattr(module, name)
            if callable(obj) and hasattr(obj, "_executable_metadata"):
                out[f"{rel}::{name}"] = obj
    return out


def _load_module(py: Path, rel: str) -> ModuleType | None:
    # A stable, unique module name per file so re-import on reload replaces it.
    mod_name = "bifrost_devhost_" + rel.replace("/", "_").removesuffix(".py")
    try:
        spec = importlib.util.spec_from_file_location(mod_name, py)
        if spec is None or spec.loader is None:
            return None
        module = importlib.util.module_from_spec(spec)
        sys.modules[mod_name] = module
        spec.loader.exec_module(module)
        return module
    except Exception as exc:  # a broken file shouldn't kill discovery
        # Logged, not raised: one un-importable file must not blank the whole map
        # (the dev server stays useful; the user sees the error on first call).
        logger.warning("solution start: could not import %s: %s", rel, exc)
        return None


class FunctionHost:
    """Holds the discovered function map; runs one by ``path::fn`` ref.

    ``reload()`` re-discovers (used on file change). ``run()`` executes the
    callable. Sync functions are supported (run directly); async are awaited.
    The execution context (org/user) is configured by the command before serving
    via :func:`set_dev_execution_context`, so callables that read
    ``context.org_id`` / use the data-plane behave as under ``bifrost run``.
    """

    def __init__(self, workspace: Path) -> None:
        self._workspace = workspace
        self._fns: dict[str, Callable[..., Any]] = {}

    def reload(self) -> None:
        self._fns = discover_functions(self._workspace)

    def refs(self) -> list[str]:
        return sorted(self._fns)

    def has(self, ref: str) -> bool:
        return ref in self._fns

    async def run(self, ref: str, params: dict[str, Any]) -> Any:
        fn = self._fns[ref]  # KeyError → caller maps to 404
        result = fn(**params)
        if inspect.isawaitable(result):
            result = await result
        return result
