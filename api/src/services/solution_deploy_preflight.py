"""Preflight + name extraction so the decorator name stays the execution identity.

The execution engine matches a workflow by ``@workflow(name=...)`` (the decorated
name). Manifest import and solution deploy persist *that* name into ``Workflow.name``
rather than the manifest dict slug — ``extract_workflow_name_from_source`` recovers
the decorated name from source and import always uses it, so a manifest slug that
differs from the decorated/function name is resolved correctly and is NOT an error.

``preflight_workflows`` therefore does not compare slug vs decorator. It flags only
the genuinely execution-breaking case: the ``function_name`` the bundle entry points
at does not exist in the carried source at all (the "Executable not found" class) —
import would then write a name nothing can resolve.
"""
from __future__ import annotations

import ast


def _is_workflow_dec(node: ast.expr) -> bool:
    return (isinstance(node, ast.Name) and node.id == "workflow") or (
        isinstance(node, ast.Attribute) and node.attr == "workflow"
    )


def extract_workflow_name_from_source(source: str, function_name: str) -> str | None:
    """Return the ``@workflow(name=...)`` name for ``function_name``.

    Falls back to ``function_name`` when the decorator carries no ``name=`` (bare
    ``@workflow`` or ``@workflow()``). Returns ``None`` only if the source cannot
    be parsed.
    """
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return None
    for node in ast.walk(tree):
        if (
            isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name == function_name
        ):
            for dec in node.decorator_list:
                if isinstance(dec, ast.Call) and _is_workflow_dec(dec.func):
                    for kw in dec.keywords:
                        if kw.arg == "name" and isinstance(kw.value, ast.Constant):
                            return str(kw.value.value)
                    return function_name  # @workflow() with no name → function name
                if _is_workflow_dec(dec):  # bare @workflow
                    return function_name
    return function_name


def _source_defines_function(source: str, function_name: str) -> bool:
    """True if ``function_name`` is defined as a (sync/async) function in source."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        # Unparseable source can't be statically verified — don't block on it
        # (the engine, not preflight, owns runtime import failures).
        return True
    return any(
        isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name == function_name
        for node in ast.walk(tree)
    )


def preflight_workflows(workflows: list[dict]) -> list[str]:
    """Return execution-breaking errors for a deploy bundle (empty list = OK).

    Each entry is expected to carry ``function_name``, ``path`` and the ``.py``
    ``source`` text. The only failure flagged is a ``function_name`` that the
    carried source does not define at all — import would then persist a
    ``Workflow.name`` the execution engine can never resolve. A manifest slug
    (``name``) that differs from the decorated/function name is NOT an error:
    import always writes the decorated name, so the divergence is resolved.

    Entries without source are skipped (nothing to verify against).
    """
    errors: list[str] = []
    for wf in workflows:
        src = wf.get("source")
        if not src:
            continue
        function_name = wf.get("function_name", "")
        if not function_name:
            continue
        if not _source_defines_function(src, function_name):
            errors.append(
                f"Workflow `{wf.get('name')}` points to {wf.get('path')}::"
                f"{function_name}, but no function named `{function_name}` exists "
                f"in that source. Fix the manifest's function_name or add the function."
            )
    return errors
