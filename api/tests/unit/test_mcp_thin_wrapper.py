"""Guardrail tests for MCP parity tools (Task 6 + Task 11).

Task 11 adds solution-scope forwarding to the file policy MCP tools —
``bifrost_file_policy_list``, ``bifrost_file_policy_get``, ``bifrost_file_policy_set``,
``bifrost_file_policy_delete`` — each accepts an optional ``solution`` param that
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

Adding new parity tools: extend ``PARITY_HANDLERS`` below.
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
    agents as agents_mod,
    code_editor as code_editor_mod,
    claims as claims_mod,
    configs as configs_mod,
    events as events_mod,
    execution as execution_mod,
    files as files_mod,
    forms as forms_mod,
    gateway as gateway_mod,
    integrations as integrations_mod,
    organizations as organizations_mod,
    apps as apps_mod,
    platform_jobs as platform_jobs_mod,
    policy_rules as policy_rules_mod,
    roles as roles_mod,
    tables as tables_mod,
    workflow as workflow_mod,
)


PARITY_HANDLERS: dict[str, set[str]] = {
    "code_editor": {"bifrost_file_search"},
    "agents": {
        "bifrost_agent_list",
        "bifrost_agent_get",
        "bifrost_agent_create",
        "bifrost_agent_update",
        "bifrost_agent_delete",
    },
    "roles": {"bifrost_role_list", "bifrost_role_create", "bifrost_role_update", "bifrost_role_delete"},
    "configs": {
        "bifrost_config_list",
        "bifrost_config_get",
        "bifrost_config_create",
        "bifrost_config_update",
        "bifrost_config_delete",
    },
    "claims": {
        "bifrost_claim_list",
        "bifrost_claim_get",
        "bifrost_claim_create",
        "bifrost_claim_update",
        "bifrost_claim_delete",
    },
    "organizations": {
        "bifrost_organization_list",
        "bifrost_organization_get",
        "bifrost_organization_create",
        "bifrost_organization_update",
        "bifrost_organization_delete",
    },
    "integrations": {
        "bifrost_integration_list",
        "bifrost_integration_get",
        "bifrost_integration_create",
        "bifrost_integration_update",
        "bifrost_integration_mapping_create",
        "bifrost_integration_mapping_update",
    },
    "workflow": {
        "bifrost_workflow_list",
        "bifrost_workflow_get",
        "bifrost_workflow_validate",
        "bifrost_workflow_register",
        "bifrost_workflow_execute",
        "bifrost_workflow_update",
        "bifrost_workflow_delete",
        "bifrost_workflow_role_grant",
        "bifrost_workflow_role_revoke",
    },
    "files": {
        "bifrost_file_policy_list",
        "bifrost_file_policy_get",
        "bifrost_file_policy_set",
        "bifrost_file_policy_delete",
    },
    "apps": {
        "bifrost_app_list",
        "bifrost_app_get",
        "bifrost_app_create",
        "bifrost_app_update",
        "bifrost_app_delete",
        "bifrost_app_publish",
        "bifrost_app_replace",
        "bifrost_app_validate",
        "bifrost_app_dependencies_get",
        "bifrost_app_dependencies_update",
    },
    "platform_jobs": {"bifrost_platform_job_get"},
    "execution": {"bifrost_execution_list", "bifrost_execution_get"},
    "policy_rules": {
        "bifrost_policy_rule_list",
        "bifrost_policy_rule_get",
        "bifrost_policy_rule_create",
        "bifrost_policy_rule_update",
        "bifrost_policy_rule_delete",
        "bifrost_policy_rule_usage_list",
    },
    "gateway": {
        "bifrost_get_required_instructions",
        "bifrost_search_capabilities",
        "bifrost_execute_tool",
        "bifrost_get_execution",
        "bifrost_search_memory",
        "bifrost_save_memory",
        "bifrost_remove_memory",
    },
    "forms": {
        "bifrost_form_list",
        "bifrost_form_get",
        "bifrost_form_create",
        "bifrost_form_update",
        "bifrost_form_delete",
    },
    "tables": {
        "bifrost_table_list",
        "bifrost_table_get",
        "bifrost_table_create",
        "bifrost_table_update",
        "bifrost_table_delete",
    },
    "events": {
        "bifrost_event_source_list",
        "bifrost_event_source_get",
        "bifrost_event_source_create",
        "bifrost_event_source_update",
        "bifrost_event_source_delete",
        "bifrost_event_subscription_list",
        "bifrost_event_subscription_get",
        "bifrost_event_subscription_create",
        "bifrost_event_subscription_update",
        "bifrost_event_subscription_delete",
        "bifrost_event_webhook_adapter_list",
    },
}


MODULES = {
    "agents": agents_mod,
    "code_editor": code_editor_mod,
    "roles": roles_mod,
    "claims": claims_mod,
    "configs": configs_mod,
    "organizations": organizations_mod,
    "integrations": integrations_mod,
    "workflow": workflow_mod,
    "files": files_mod,
    "policy_rules": policy_rules_mod,
    "apps": apps_mod,
    "platform_jobs": platform_jobs_mod,
    "execution": execution_mod,
    "gateway": gateway_mod,
    "forms": forms_mod,
    "tables": tables_mod,
    "events": events_mod,
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
async def test_get_app_publish_status_requires_action_is_an_error() -> None:
    """An action-required publish must not be presented as a completed success."""
    ctx = _make_mcp_context()
    response = {
        "status": "requires_action",
        "progress": {"phase": "Confirm deletes"},
        "result": {
            "requires_action": "confirm_deletes",
            "unsafe_detail": "must not reach MCP consumers",
        },
    }

    with patch.object(platform_jobs_mod, "call_rest", AsyncMock(return_value=(200, response))):
        result = await platform_jobs_mod.bifrost_platform_job_get(ctx, "publish-job-id")

    assert result.structured_content is not None
    assert result.structured_content["error"] == (
        "Platform job requires_action: Confirm deletes"
    )
    assert result.structured_content["status"] == "requires_action"
    assert result.structured_content["result"] == {"requires_action": "confirm_deletes"}
    assert "unsafe_detail" not in result.structured_content


@pytest.mark.asyncio
async def test_bifrost_integration_list_is_a_thin_rest_wrapper() -> None:
    """bifrost_integration_list reads GET /api/integrations, not the ORM.

    Regression coverage for the R1b batch-3 rename: the old ``list_integrations``
    queried ``Integration``/``IntegrationMapping`` directly and widened the
    result for non-admin org members; the thin wrapper inherits REST's
    platform-admin-only gate instead.
    """
    from src.services.mcp_server.tools import integrations as integrations_mod

    ctx = _make_mcp_context()
    response = {
        "items": [
            {
                "id": "11111111-2222-3333-4444-555555555555",
                "name": "Microsoft Graph",
                "has_oauth_config": True,
                "entity_id_name": "Tenant ID",
            }
        ],
        "total": 1,
    }

    with patch.object(
        integrations_mod, "call_rest", AsyncMock(return_value=(200, response))
    ) as call_rest_mock:
        result = await integrations_mod.bifrost_integration_list(ctx)

    call_rest_mock.assert_awaited_once_with(ctx, "GET", "/api/integrations")
    data = result.structured_content
    assert data["count"] == 1
    assert data["integrations"][0]["name"] == "Microsoft Graph"
    assert data["integrations"][0]["has_oauth"] is True
    assert data["integrations"][0]["entity_id_name"] == "Tenant ID"


@pytest.mark.asyncio
async def test_create_integration_forwards_description() -> None:
    """create_integration includes description in DTO assembly and REST payload."""
    from src.services.mcp_server.tools.integrations import bifrost_integration_create as create_integration

    ctx = _make_mcp_context()
    assembled_body = {
        "name": "mcp-unit-integration",
        "description": "Shown on admin integration cards",
    }

    with (
        patch(
            "src.services.mcp_server.tools.integrations._assemble_integration_body",
            AsyncMock(return_value=assembled_body),
        ) as assemble_mock,
        patch(
            "src.services.mcp_server.tools.integrations.call_rest",
            AsyncMock(return_value=(201, {"id": "integration-id", **assembled_body})),
        ) as call_rest_mock,
    ):
        await create_integration(
            ctx,
            name="mcp-unit-integration",
            description="Shown on admin integration cards",
        )

    fields = assemble_mock.await_args.kwargs
    assert fields["model_name"] == "IntegrationCreate"
    assert assemble_mock.await_args.args[1]["description"] == (
        "Shown on admin integration cards"
    )
    assert call_rest_mock.await_args.kwargs["json_body"] == assembled_body


@pytest.mark.asyncio
async def test_update_integration_forwards_description_when_provided() -> None:
    """update_integration forwards a provided description through the REST bridge."""
    from src.services.mcp_server.tools.integrations import bifrost_integration_update as update_integration

    class _RestClient:
        async def __aenter__(self):
            return MagicMock()

        async def __aexit__(self, exc_type, exc, tb):
            return False

    ctx = _make_mcp_context()
    integration_id = "11111111-2222-3333-4444-555555555555"
    assembled_body = {"description": "Updated card description"}

    with (
        patch(
            "src.services.mcp_server.tools.integrations.rest_client",
            MagicMock(return_value=_RestClient()),
        ),
        patch(
            "src.services.mcp_server.tools.integrations._assemble_integration_body",
            AsyncMock(return_value=assembled_body),
        ) as assemble_mock,
        patch(
            "src.services.mcp_server.tools.integrations.call_rest",
            AsyncMock(return_value=(200, {"id": integration_id, **assembled_body})),
        ) as call_rest_mock,
    ):
        await update_integration(
            ctx,
            integration_ref=integration_id,
            description="Updated card description",
        )

    fields = assemble_mock.await_args.args[1]
    assert assemble_mock.await_args.kwargs["model_name"] == "IntegrationUpdate"
    assert fields["description"] == "Updated card description"
    assert call_rest_mock.await_args.args[2] == f"/api/integrations/{integration_id}"
    assert call_rest_mock.await_args.kwargs["json_body"] == assembled_body


@pytest.mark.asyncio
async def test_file_policy_solution_scope_forwarded_list() -> None:
    """bifrost_file_policy_list forwards ?solution= to the REST endpoint."""
    from src.services.mcp_server.tools.files import bifrost_file_policy_list

    mock_call_rest, captures = _call_rest_capturing_params()
    ctx = _make_mcp_context()
    install_id = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"

    with patch("src.services.mcp_server.tools.files.call_rest", mock_call_rest):
        await bifrost_file_policy_list(ctx, location="solutions", solution=install_id)

    assert len(captures) == 1
    assert captures[0]["params"].get("solution") == install_id


@pytest.mark.asyncio
async def test_file_policy_solution_scope_forwarded_get() -> None:
    """bifrost_file_policy_get forwards ?solution= to the REST endpoint."""
    from src.services.mcp_server.tools.files import bifrost_file_policy_get

    mock_call_rest, captures = _call_rest_capturing_params()
    ctx = _make_mcp_context()

    async def _fake(context, method, path, *, json_body=None, params=None):
        captures.append({"params": params})
        return (200, {"id": "x", "path": "", "location": "solutions", "policies": []})

    install_id = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    with patch("src.services.mcp_server.tools.files.call_rest", AsyncMock(side_effect=_fake)):
        await bifrost_file_policy_get(ctx, path="data/", location="solutions", solution=install_id)

    assert captures[0]["params"].get("solution") == install_id


@pytest.mark.asyncio
async def test_file_policy_solution_scope_omitted_when_none() -> None:
    """When solution is None the ?solution= key is absent from the REST call."""
    from src.services.mcp_server.tools.files import bifrost_file_policy_list

    mock_call_rest, captures = _call_rest_capturing_params()
    ctx = _make_mcp_context()

    with patch("src.services.mcp_server.tools.files.call_rest", mock_call_rest):
        await bifrost_file_policy_list(ctx, location="workspace", solution=None)

    assert len(captures) == 1
    assert "solution" not in captures[0]["params"]


def test_file_policy_tools_accept_solution_param() -> None:
    """All four file policy tools declare an optional ``solution`` keyword argument."""
    import inspect as _inspect
    from src.services.mcp_server.tools.files import (
        bifrost_file_policy_delete,
        bifrost_file_policy_get,
        bifrost_file_policy_list,
        bifrost_file_policy_set,
    )

    for fn in (bifrost_file_policy_list, bifrost_file_policy_get, bifrost_file_policy_set, bifrost_file_policy_delete):
        sig = _inspect.signature(fn)
        assert "solution" in sig.parameters, (
            f"{fn.__name__} does not accept a 'solution' parameter (Task 11)"
        )
