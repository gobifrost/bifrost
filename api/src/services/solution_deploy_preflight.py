"""Deploy preflight: catch a workflow bundle that can never execute.

Execution resolves a workflow by its Python ``function_name`` (both
``execution/service.py`` and ``execution/module_loader.py`` match the def name,
not the decorator display name), and the DB ``name`` is identity/display only.
So a manifest entry whose declared ``name`` differs from the decorated name or
the function name is NOT an error — it is a legitimate rename.

The one genuinely execution-breaking case is a bundle entry whose
``function_name`` is not defined in the carried source at all: import would then
persist a record the engine can never load. ``preflight_workflows`` flags only
that.
"""
from __future__ import annotations

import ast


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
    carried source does not define at all — import would then persist a workflow
    the execution engine can never load. A manifest ``name`` that differs from
    the decorated or function name is NOT an error (execution resolves by
    function_name; the name is identity/display only).

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
