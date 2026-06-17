"""Preflight + name extraction so the decorator name stays the execution identity.

The execution engine matches a workflow by ``@workflow(name=...)`` (the decorated
name). Manifest import and solution deploy must therefore persist *that* name into
``Workflow.name`` rather than the manifest dict slug. ``extract_workflow_name_from_source``
recovers the decorated name from source; ``preflight_workflows`` flags any bundle
entry whose declared name diverges from the decorated one before it is written.
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


def preflight_workflows(workflows: list[dict]) -> list[str]:
    """Return mismatch errors for a deploy bundle (empty list = OK).

    Each entry is expected to carry ``name``, ``function_name``, ``path`` and the
    ``.py`` ``source`` text. Entries without source are skipped (no source = nothing
    to compare against).
    """
    errors: list[str] = []
    for wf in workflows:
        src = wf.get("source")
        if not src:
            continue
        actual = extract_workflow_name_from_source(src, wf.get("function_name", ""))
        declared = wf.get("name")
        if actual and declared and actual != declared:
            errors.append(
                f"Workflow manifest entry `{declared}` points to {wf.get('path')}::"
                f"{wf.get('function_name')}, but the decorated name is `{actual}`. "
                f'Use @workflow(name="{declared}") or update the manifest.'
            )
    return errors
