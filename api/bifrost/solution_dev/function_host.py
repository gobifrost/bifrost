"""Discover and run a Solution workspace's local @workflow functions in-process.

This is the "local function host" behind `bifrost solution start`: it imports the
workspace's decorated functions (any folder layout) and runs them directly,
mirroring `bifrost run`'s offline execution — nothing is registered to the API.
"""
from __future__ import annotations

import importlib.util
import inspect
import sys
from dataclasses import dataclass, field
from pathlib import Path
from types import ModuleType
from typing import Any, Callable

import yaml

# Folders that never hold solution source — skip for speed and to avoid
# importing build output / deps. (Discovery is intentionally layout-agnostic:
# a @workflow anywhere is resolvable by its path::fn, exactly as the platform
# resolves it — we don't restrict source to particular dirs.)
_SKIP_DIRS = {"node_modules", "dist", ".venv", "venv", "__pycache__", ".git", ".bifrost"}


class LocalWorkflowError(Exception):
    """Base for local resolution errors the dev proxy surfaces to the app."""


class LocalWorkflowResolutionError(LocalWorkflowError):
    """A workflow name matched multiple local manifest entries."""


class LocalWorkflowImportError(LocalWorkflowError):
    """The ref's target file exists locally but failed to import."""


@dataclass(frozen=True)
class LocalWorkflowIndex:
    by_ref: dict[str, str] = field(default_factory=dict)       # ref → local path::fn
    ambiguous: dict[str, list[str]] = field(default_factory=dict)  # name → candidate refs
    failed: dict[str, str] = field(default_factory=dict)       # ref → import error


def _load_workflow_manifest_entries(workspace: Path) -> list[dict[str, Any]]:
    manifest = workspace / ".bifrost" / "workflows.yaml"
    if not manifest.is_file():
        return []
    data = yaml.safe_load(manifest.read_text()) or {}
    workflows = data.get("workflows") or {}
    if not isinstance(workflows, dict):
        return []
    entries: list[dict[str, Any]] = []
    for workflow_id, body in workflows.items():
        if not isinstance(body, dict):
            continue
        entry = dict(body)
        entry.setdefault("id", str(workflow_id))
        entries.append(entry)
    return entries


def build_local_workflow_index(
    entries: list[dict[str, Any]],
    local_refs: set[str],
    failures: dict[str, str],
) -> LocalWorkflowIndex:
    """Index every manifest entry's ref shapes onto the discovered local functions.

    Honors the manifest ``path`` key ONLY — the same key deploy's
    ``_collect_workflows`` honors — so ``solution start`` and ``deploy``
    agree about which entries exist.
    """
    by_ref = {ref: ref for ref in local_refs}
    failed: dict[str, str] = {}
    name_targets: dict[str, set[str]] = {}

    for entry in entries:
        path = entry.get("path")
        function_name = entry.get("function_name")
        if not path or not function_name:
            continue
        local_ref = f"{path}::{function_name}"
        uuid_alias = str(entry["id"])
        name = entry.get("name")
        if name:
            name_targets.setdefault(str(name), set()).add(local_ref)

        if local_ref in local_refs:
            by_ref[uuid_alias] = local_ref
        elif path in failures:
            failed[uuid_alias] = failures[path]
        # else: entry points at a file that doesn't exist locally → clean miss.

    ambiguous: dict[str, list[str]] = {}
    for name, targets in name_targets.items():
        live = sorted(t for t in targets if t in local_refs)
        broken = sorted(t for t in targets if t.split("::", 1)[0] in failures)
        if len(live) > 1:
            ambiguous[name] = live
        elif len(live) == 1:
            by_ref[name] = live[0]
        elif broken:
            failed[name] = failures[broken[0].split("::", 1)[0]]
    return LocalWorkflowIndex(by_ref=by_ref, ambiguous=ambiguous, failed=failed)


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
        self._index = LocalWorkflowIndex()

    def reload(self) -> None:
        self._fns, self._failures = discover_functions(self._workspace)
        try:
            entries = _load_workflow_manifest_entries(self._workspace)
        except yaml.YAMLError as exc:
            # A mid-edit save must degrade to "no aliases + loud failure line",
            # never an exception — an escaped error kills the watcher thread.
            self._failures[".bifrost/workflows.yaml"] = f"invalid YAML: {exc}"
            entries = []
        self._index = build_local_workflow_index(entries, set(self._fns), self._failures)

    def refs(self) -> list[str]:
        return sorted(self._fns)

    def failures(self) -> dict[str, str]:
        return dict(self._failures)

    def resolve(self, ref: str) -> str | None:
        """Local ``path::fn`` for any ref shape; None on a clean miss.

        Raises LocalWorkflowResolutionError (ambiguous name) or
        LocalWorkflowImportError (target file failed to import) — both must
        surface to the developer, never fall through to the platform.
        """
        if ref in self._index.ambiguous:
            candidates = ", ".join(self._index.ambiguous[ref])
            raise LocalWorkflowResolutionError(
                f"workflow name '{ref}' is ambiguous in .bifrost/workflows.yaml "
                f"(matches: {candidates}); use a path::function ref"
            )
        err = self._index.failed.get(ref)
        if err is None and "::" in ref:
            err = self._failures.get(ref.split("::", 1)[0])
        if err is not None:
            raise LocalWorkflowImportError(
                f"local workflow '{ref}' failed to import: {err} "
                f"(fix the file — it hot-reloads on save)"
            )
        return self._index.by_ref.get(ref)

    async def run(self, ref: str, params: dict[str, Any]) -> Any:
        fn = self._fns[ref]  # KeyError → caller maps to 404
        result = fn(**params)
        if inspect.isawaitable(result):
            result = await result
        return result
