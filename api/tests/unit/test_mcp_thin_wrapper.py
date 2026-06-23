"""Guardrail tests for MCP parity tools (Task 6 + Task 11).

Task 11 adds solution-scope forwarding to the file policy MCP tools —
``list_file_policies``, ``get_file_policy``, ``set_file_policy``,
``delete_file_policy`` — each accepts an optional ``solution`` param that
is forwarded as ``?solution=<uuid>`` to the REST endpoint via ``call_rest``.
Tests for that behaviour live at the bottom of this file under
``test_file_policy_solution_scope_*``.


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
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Task 6 tool modules (file paths and the set of handler names added).
# New-only files list all their handlers; extended files list just the new ones.
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

from src.services.mcp_server.tools import (  # noqa: E402
    claims as claims_mod,
    configs as configs_mod,
    files as files_mod,
    integrations as integrations_mod,
    organizations as organizations_mod,
    policy_rules as policy_rules_mod,
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
    "files": {
        "list_file_policies",
        "get_file_policy",
        "set_file_policy",
        "delete_file_policy",
    },
    "policy_rules": {
        "list_policy_rules",
        "create_policy_rule",
        "delete_policy_rule",
    },
}


MODULES = {
    "roles": roles_mod,
    "claims": claims_mod,
    "configs": configs_mod,
    "organizations": organizations_mod,
    "integrations": integrations_mod,
    "workflow": workflow_mod,
    "files": files_mod,
    "policy_rules": policy_rules_mod,
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


# ---------------------------------------------------------------------------
# Task 11: solution scope forwarding in file policy MCP tools
# ---------------------------------------------------------------------------


def _make_mcp_context(is_admin: bool = True) -> MagicMock:
    """Return a minimal fake MCPContext sufficient for _policy_params + call_rest."""
    ctx = MagicMock()
    ctx.user_id = "00000000-0000-0000-0000-000000000001"
    ctx.user_email = "test@example.com"
    ctx.user_name = "Test User"
    ctx.is_platform_admin = is_admin
    ctx.org_id = "00000000-0000-0000-0000-000000000002"
    ctx.is_external = False
    return ctx


def _call_rest_capturing_params() -> tuple[AsyncMock, list[dict]]:
    """Return (mock, captures) where captures accumulates kwargs from each call."""
    calls: list[dict] = []

    async def _fake_call_rest(context, method, path, *, json_body=None, params=None):
        calls.append({"method": method, "path": path, "params": params, "json_body": json_body})
        return (200, {"policies": [], "count": 0})

    return AsyncMock(side_effect=_fake_call_rest), calls


@pytest.mark.asyncio
async def test_file_policy_solution_scope_forwarded_list() -> None:
    """list_file_policies forwards ?solution= to the REST endpoint."""
    from src.services.mcp_server.tools.files import list_file_policies

    mock_call_rest, captures = _call_rest_capturing_params()
    ctx = _make_mcp_context()
    install_id = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"

    with patch("src.services.mcp_server.tools.files.call_rest", mock_call_rest):
        await list_file_policies(ctx, location="solutions", solution=install_id)

    assert len(captures) == 1
    assert captures[0]["params"].get("solution") == install_id


@pytest.mark.asyncio
async def test_file_policy_solution_scope_forwarded_get() -> None:
    """get_file_policy forwards ?solution= to the REST endpoint."""
    from src.services.mcp_server.tools.files import get_file_policy

    mock_call_rest, captures = _call_rest_capturing_params()
    ctx = _make_mcp_context()

    async def _fake(context, method, path, *, json_body=None, params=None):
        captures.append({"params": params})
        return (200, {"id": "x", "path": "", "location": "solutions", "policies": []})

    install_id = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    with patch("src.services.mcp_server.tools.files.call_rest", AsyncMock(side_effect=_fake)):
        await get_file_policy(ctx, path="data/", location="solutions", solution=install_id)

    assert captures[0]["params"].get("solution") == install_id


@pytest.mark.asyncio
async def test_file_policy_solution_scope_omitted_when_none() -> None:
    """When solution is None the ?solution= key is absent from the REST call."""
    from src.services.mcp_server.tools.files import list_file_policies

    mock_call_rest, captures = _call_rest_capturing_params()
    ctx = _make_mcp_context()

    with patch("src.services.mcp_server.tools.files.call_rest", mock_call_rest):
        await list_file_policies(ctx, location="workspace", solution=None)

    assert len(captures) == 1
    assert "solution" not in captures[0]["params"]


def test_file_policy_tools_accept_solution_param() -> None:
    """All four file policy tools declare an optional ``solution`` keyword argument."""
    import inspect as _inspect
    from src.services.mcp_server.tools.files import (
        delete_file_policy,
        get_file_policy,
        list_file_policies,
        set_file_policy,
    )

    for fn in (list_file_policies, get_file_policy, set_file_policy, delete_file_policy):
        sig = _inspect.signature(fn)
        assert "solution" in sig.parameters, (
            f"{fn.__name__} does not accept a 'solution' parameter (Task 11)"
        )
