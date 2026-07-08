"""Discover and run a Solution workspace's local @workflow functions in-process.

This is the "local function host" behind `bifrost solution start`: it imports the
workspace's decorated functions (any folder layout) and runs them directly,
mirroring `bifrost run`'s offline execution — nothing is registered to the API.
"""
from __future__ import annotations

import importlib.util
import inspect
import sys
from pathlib import Path
from types import ModuleType
from typing import Any, Callable

# Folders that never hold solution source — skip for speed and to avoid
# importing build output / deps. (Discovery is intentionally layout-agnostic:
# a @workflow anywhere is resolvable by its path::fn, exactly as the platform
# resolves it — we don't restrict source to particular dirs.)
_SKIP_DIRS = {"node_modules", "dist", ".venv", "venv", "__pycache__", ".git", ".bifrost"}


def discover_functions(
    workspace: Path,
) -> tuple[dict[str, Callable[..., Any]], dict[str, str]]:
    """Map ``path::function_name`` → callable for every decorated function.

    Also returns a map of workspace-relative paths that FAILED to import →
    one-line error. A broken file must be a loud local error at resolution
    time, not a silent drop that lets the ref fall through to the platform
    (which would run the stale deployed copy — the exact confusion a local
    debug loop exists to prevent).

    ``path`` is workspace-relative with POSIX separators (the same form app
    code passes to ``useWorkflow``). The workspace root is placed on
    ``sys.path`` so a function's ``from modules.x import y`` resolves against
    the solution root.
    """
    workspace = workspace.resolve()
    root_str = str(workspace)
    if root_str not in sys.path:
        sys.path.insert(0, root_str)

    out: dict[str, Callable[..., Any]] = {}
    failures: dict[str, str] = {}
    for py in sorted(workspace.rglob("*.py")):
        rel_parts = py.relative_to(workspace).parts
        if any(part in _SKIP_DIRS for part in rel_parts):
            continue
        rel = py.relative_to(workspace).as_posix()
        module, err = _load_module(py, rel)
        if err is not None:
            failures[rel] = err
            continue
        if module is None:
            continue
        for name in dir(module):
            obj = getattr(module, name)
            if callable(obj) and hasattr(obj, "_executable_metadata"):
                out[f"{rel}::{name}"] = obj
    return out, failures


def _load_module(py: Path, rel: str) -> tuple[ModuleType | None, str | None]:
    # A stable, unique module name per file so re-import on reload replaces it.
    mod_name = "bifrost_devhost_" + rel.replace("/", "_").removesuffix(".py")
    try:
        spec = importlib.util.spec_from_file_location(mod_name, py)
        if spec is None or spec.loader is None:
            return None, "could not build an import spec for this file"
        module = importlib.util.module_from_spec(spec)
        sys.modules[mod_name] = module
        spec.loader.exec_module(module)
        return module, None
    except Exception as exc:  # one broken file must not blank the whole map
        return None, f"{type(exc).__name__}: {exc}"


def set_dev_execution_context(
    *, user: dict, org: dict | None, solution_id: str | None = None
) -> None:
    """Configure the in-process execution context the local host runs under.

    Mirrors `bifrost run`'s context setup so locally-run functions see
    context.org_id/user_id and the data-plane runs under the chosen org.

    ``solution_id`` is the resolved install id for this workspace. When set, the
    host's functions resolve their OWN install-scoped tables/configs own-first
    (the SDK appends ``?solution=``), matching the server engine — without it a
    solution workflow run via ``solution start`` would read the ``_repo/``
    cascade instead of its own data plane (F2).
    """
    import uuid as _uuid

    from bifrost._context import set_execution_context as _set_execution_context
    from bifrost._execution_context import ExecutionContext, Organization

    organization = (
        Organization(
            id=org["id"],
            name=org.get("name", ""),
            is_active=org.get("is_active", True),
            is_provider=org.get("is_provider", False),
        )
        if org
        else None
    )
    ctx = ExecutionContext(
        user_id=user.get("id", "cli-user"),
        email=user.get("email", ""),
        name=user.get("name", "CLI User"),
        scope=org["id"] if org else "GLOBAL",
        organization=organization,
        is_platform_admin=user.get("is_superuser", False),
        is_function_key=False,
        execution_id=f"solution-start-{_uuid.uuid4()}",
        workflow_name="solution-start",
        solution_id=solution_id,
    )
    _set_execution_context(ctx)


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
        self._failures: dict[str, str] = {}

    def reload(self) -> None:
        self._fns, self._failures = discover_functions(self._workspace)

    def refs(self) -> list[str]:
        return sorted(self._fns)

    def failures(self) -> dict[str, str]:
        return dict(self._failures)

    def has(self, ref: str) -> bool:
        return ref in self._fns

    async def run(self, ref: str, params: dict[str, Any]) -> Any:
        fn = self._fns[ref]  # KeyError → caller maps to 404
        result = fn(**params)
        if inspect.isawaitable(result):
            result = await result
        return result
