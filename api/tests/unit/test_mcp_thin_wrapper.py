"""Guardrail tests for MCP parity tools (Task 6).

Assert that each handler added under Task 6 — ``roles.*``, ``configs.*``,
and the new ``update_*`` / ``delete_*`` / ``grant_*`` / ``revoke_*`` /
``add_*`` / ``update_*`` tools in existing modules — does **not** touch
the ORM, repositories, or hold an ``AsyncSession``.

The plan's Task 6 architectural constraint (plan lines 360-367) is
precisely "thin wrappers that call the REST endpoints internally".
These checks fail loudly when a future contributor adds direct DB
access to a parity tool, which would re-introduce the drift the plan is
trying to prevent.

Approach: parse each new tool module's source with :mod:`ast`, walk it,
and reject any import from ``src.repositories.*``, ``src.models.orm.*``,
or ``sqlalchemy.ext.asyncio.AsyncSession`` that is scoped to a Task 6
handler.

Existing tool handlers (``list_integrations``, ``list_organizations``,
etc.) intentionally still use ORM — this test only inspects the Task 6
additions. Adding new parity tools: extend ``PARITY_HANDLERS`` below.
"""

from __future__ import annotations

import ast
import inspect
import pathlib
import sys
from typing import Iterable

import pytest

# Task 6 tool modules (file paths and the set of handler names added).
# New-only files list all their handlers; extended files list just the new ones.
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

from src.services.mcp_server.tools import (  # noqa: E402
    claims as claims_mod,
    configs as configs_mod,
    integrations as integrations_mod,
    organizations as organizations_mod,
    roles as roles_mod,
    workflow as workflow_mod,
)


PARITY_HANDLERS: dict[str, set[str]] = {
    "roles": {"list_roles", "create_role", "update_role", "delete_role"},
    "configs": {
        "list_configs",
        "create_config",
        "update_config",
        "delete_config",
    },
    "claims": {
        "list_claims",
        "get_claim",
        "create_claim",
        "update_claim",
        "delete_claim",
    },
    "organizations": {"update_organization", "delete_organization"},
    "integrations": {
        "create_integration",
        "update_integration",
        "add_integration_mapping",
        "update_integration_mapping",
    },
    "workflow": {
        "update_workflow",
        "delete_workflow",
        "grant_workflow_role",
        "revoke_workflow_role",
    },
}


MODULES = {
    "roles": roles_mod,
    "claims": claims_mod,
    "configs": configs_mod,
    "organizations": organizations_mod,
    "integrations": integrations_mod,
    "workflow": workflow_mod,
}


FORBIDDEN_IMPORT_PREFIXES = (
    "src.repositories",
    "src.models.orm",
)

FORBIDDEN_IMPORT_NAMES = {
    "AsyncSession",
}


def _handler_source(module_path: pathlib.Path, handler_name: str) -> ast.AST:
    """Parse the module and return the ``FunctionDef`` / ``AsyncFunctionDef``.

    Helper modules like ``_http_bridge`` and ``_ref_error_payload`` are
    out of scope for this check; we only inspect named handlers.
    """
    tree = ast.parse(module_path.read_text(encoding="utf-8"), filename=str(module_path))
    for node in ast.walk(tree):
        if (
            isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name == handler_name
        ):
            return node
    raise AssertionError(f"Handler {handler_name} not found in {module_path}")


def _walk_imports(node: ast.AST) -> Iterable[str]:
    """Yield every module name referenced by ``import`` / ``from`` nodes."""
    for inner in ast.walk(node):
        if isinstance(inner, ast.Import):
            for alias in inner.names:
                yield alias.name
        elif isinstance(inner, ast.ImportFrom):
            module = inner.module or ""
            yield module
            for alias in inner.names:
                # Expose the symbol too, so we catch ``from foo import AsyncSession``.
                yield alias.name


@pytest.mark.parametrize(
    "module_name,handler_name",
    [
        (mod, handler)
        for mod, handlers in PARITY_HANDLERS.items()
        for handler in handlers
    ],
)
def test_parity_handler_has_no_orm_imports(
    module_name: str, handler_name: str
) -> None:
    """Each Task 6 handler body must not import ORM / repositories / AsyncSession."""
    module = MODULES[module_name]
    module_path = pathlib.Path(inspect.getfile(module))
    node = _handler_source(module_path, handler_name)

    offenders: list[str] = []
    for imported in _walk_imports(node):
        if imported in FORBIDDEN_IMPORT_NAMES:
            offenders.append(imported)
            continue
        if any(imported.startswith(pfx) for pfx in FORBIDDEN_IMPORT_PREFIXES):
            offenders.append(imported)

    assert not offenders, (
        f"{module_name}.{handler_name} imports forbidden names: {offenders}. "
        "Task 6 parity tools must be thin REST wrappers — no direct ORM, "
        "repositories, or AsyncSession. Route the call through "
        "src.services.mcp_server.tools._http_bridge instead."
    )


def test_parity_handlers_use_http_bridge() -> None:
    """Every Task 6 handler must reference the HTTP bridge helpers.

    Catches the reverse drift: a handler that *removed* its REST call and
    quietly reimplemented the logic in-process would slip past the
    ORM-import check if it used sessions it already had in scope.
    """
    for module_name, handler_set in PARITY_HANDLERS.items():
        module = MODULES[module_name]
        module_path = pathlib.Path(inspect.getfile(module))
        source = module_path.read_text(encoding="utf-8")

        # The bridge is imported once at module scope; each handler
        # references ``rest_client`` or ``call_rest`` at least once.
        for handler in handler_set:
            node = _handler_source(module_path, handler)
            bodies = [ast.unparse(stmt) for stmt in ast.walk(node)]
            joined = "\n".join(bodies)
            assert (
                "call_rest" in joined or "rest_client" in joined
            ), (
                f"{module_name}.{handler} does not use call_rest / rest_client; "
                "Task 6 parity tools must go through the in-process REST bridge."
            )

        # Sanity: the module imports the bridge at module scope.
        assert (
            "from src.services.mcp_server.tools._http_bridge" in source
        ), f"{module_name} does not import the HTTP bridge helpers"
