"""
Workflow MCP Tools

Tools for executing, listing, validating, creating workflows, plus the
lifecycle thin wrappers added by Task 6 of the CLI mutation surface + MCP
parity plan (``update_workflow``, ``delete_workflow``, ``grant_workflow_role``,
``revoke_workflow_role``).

The Task 6 wrappers go through the in-process REST bridge — they must not
touch the ORM, repositories, or a long-lived ``AsyncSession``. Existing
tools in this module (``execute_workflow``, ``list_workflows``,
``validate_workflow``, ``get_workflow``, ``register_workflow``) predate this
plan and are explicitly left untouched.
"""

import logging
from typing import Any

from fastmcp.tools import ToolResult

from src.services.mcp_server.tool_result import error_result, success_result
from src.services.mcp_server.tools._http_bridge import call_rest, rest_client
from src.services.mcp_server.tools.db import get_tool_db

# MCPContext is imported where needed to avoid circular imports

logger = logging.getLogger(__name__)


def _ref_error_payload(exc: Exception) -> dict[str, Any]:
    from bifrost.refs import AmbiguousRefError, RefNotFoundError

    if isinstance(exc, AmbiguousRefError):
        return {"kind": exc.kind, "value": exc.value, "candidates": exc.candidates}
    if isinstance(exc, RefNotFoundError):
        return {"kind": exc.kind, "value": exc.value}
    return {"detail": str(exc)}


async def execute_workflow(
    context: Any, workflow_id: str, params: dict[str, Any] | None = None
) -> ToolResult:
    """Execute a workflow by ID or name and return results."""
    from uuid import UUID

    from src.repositories.workflows import WorkflowRepository
    from src.services.execution.service import execute_tool

    if not workflow_id:
        return error_result("workflow_id is required")

    params = params or {}
    logger.info(f"MCP execute_workflow: {workflow_id} with params: {params}")

    try:
        async with get_tool_db(context) as db:
            ctx_org_id = UUID(str(context.org_id)) if context.org_id else None
            ctx_user_id = UUID(str(context.user_id)) if context.user_id else None
            repo = WorkflowRepository(
                db,
                org_id=ctx_org_id,
                user_id=ctx_user_id,
                is_superuser=context.is_platform_admin,
            )
            workflow = await repo.resolve(workflow_id)

            if not workflow:
                return error_result(f"Workflow '{workflow_id}' not found. Use list_workflows to see available workflows.")

            result = await execute_tool(
                workflow_id=str(workflow.id),
                workflow_name=workflow.name,
                parameters=params,
                user_id=str(context.user_id),
                user_email=context.user_email,
                user_name=context.user_name or "MCP User",
                org_id=str(context.org_id) if context.org_id else None,
                is_platform_admin=context.is_platform_admin,
            )

            success = result.status.value == "Success"
            data = {
                "success": success,
                "execution_id": result.execution_id,
                "workflow_id": str(workflow.id),
                "workflow_name": workflow.name,
                "status": result.status.value,
                "duration_ms": result.duration_ms,
                "result": result.result,
                "error": result.error,
                "error_type": result.error_type,
            }

            if success:
                display_text = f"Workflow '{workflow.name}' completed successfully ({result.duration_ms}ms)"
                return success_result(display_text, data)
            else:
                display_text = f"Workflow '{workflow.name}' failed: {result.error}"
                return error_result(display_text, data)

    except Exception as e:
        logger.exception(f"Error executing workflow via MCP: {e}")
        return error_result(f"Error executing workflow: {str(e)}")


async def list_workflows(
    context: Any, query: str | None = None, category: str | None = None
) -> ToolResult:
    """List all registered workflows."""
    from uuid import UUID

    from src.repositories.workflows import WorkflowRepository

    logger.info(f"MCP list_workflows called with query={query}, category={category}")

    try:
        async with get_tool_db(context) as db:
            ctx_org_id = UUID(str(context.org_id)) if context.org_id else None
            ctx_user_id = UUID(str(context.user_id)) if context.user_id else None
            repo = WorkflowRepository(
                db,
                org_id=ctx_org_id,
                user_id=ctx_user_id,
                is_superuser=context.is_platform_admin,
            )
            workflows = await repo.search(query=query, category=category, limit=100)
            total_count = await repo.count_active()

            workflow_list = [
                {
                    "id": str(w.id),
                    "name": w.name,
                    "description": w.description,
                    "type": w.type,
                    "category": w.category,
                    "endpoint_enabled": w.endpoint_enabled,
                    "path": w.path,
                }
                for w in workflows
            ]

            data = {
                "workflows": workflow_list,
                "count": len(workflows),
                "total_count": total_count,
            }

            if not workflows:
                return success_result("No workflows found", data)

            # Build display text showing first 10 workflows
            display_lines = [f"Found {len(workflows)} workflow(s):"]
            for w in workflows[:10]:
                desc = f" - {w.description}" if w.description else ""
                display_lines.append(f"  - {w.name} ({w.type}){desc}")
            if len(workflows) > 10:
                display_lines.append(f"  ... and {len(workflows) - 10} more")

            return success_result("\n".join(display_lines), data)

    except Exception as e:
        logger.exception(f"Error listing workflows via MCP: {e}")
        return error_result(f"Error listing workflows: {str(e)}")


async def validate_workflow(context: Any, file_path: str) -> ToolResult:
    """Validate a workflow Python file for syntax and decorator issues."""
    import ast

    from src.services.file_storage import FileStorageService

    logger.info(f"MCP validate_workflow called with file_path={file_path}")

    try:
        async with get_tool_db(context) as db:
            service = FileStorageService(db)
            content_bytes, _ = await service.read_file(file_path)
            content = content_bytes.decode("utf-8")

            errors: list[str | dict[str, Any]] = []
            warnings: list[str] = []

            # Check Python syntax
            try:
                tree = ast.parse(content)
            except SyntaxError as e:
                data = {
                    "valid": False,
                    "errors": [{"type": "syntax", "line": e.lineno, "message": e.msg}],
                    "warnings": [],
                    "workflow_functions": [],
                }
                return error_result(f"Syntax error at line {e.lineno}: {e.msg}", data)

            # Check for @workflow decorator
            has_workflow_decorator = False
            workflow_funcs: list[str] = []

            for node in ast.walk(tree):
                if isinstance(node, ast.FunctionDef) or isinstance(node, ast.AsyncFunctionDef):
                    for decorator in node.decorator_list:
                        decorator_name = ""
                        if isinstance(decorator, ast.Name):
                            decorator_name = decorator.id
                        elif isinstance(decorator, ast.Call):
                            if isinstance(decorator.func, ast.Name):
                                decorator_name = decorator.func.id
                            elif isinstance(decorator.func, ast.Attribute):
                                decorator_name = decorator.func.attr

                        if decorator_name == "workflow":
                            has_workflow_decorator = True
                            workflow_funcs.append(node.name)

            if not has_workflow_decorator:
                errors.append("No @workflow decorator found. Add @workflow to your main function.")

            # Check for bifrost import
            has_bifrost_import = "from bifrost" in content or "import bifrost" in content
            if not has_bifrost_import:
                warnings.append("No bifrost import found. You may need `from bifrost import workflow`.")

            is_valid = len(errors) == 0
            data = {
                "valid": is_valid,
                "errors": errors,
                "warnings": warnings,
                "workflow_functions": workflow_funcs,
            }

            if is_valid:
                funcs_str = ", ".join(workflow_funcs) if workflow_funcs else "none"
                warning_str = f" ({len(warnings)} warning(s))" if warnings else ""
                display_text = f"Workflow '{file_path}' is valid{warning_str}. Functions: {funcs_str}"
                return success_result(display_text, data)
            else:
                error_msgs = "; ".join(str(e) for e in errors)
                return error_result(f"Workflow '{file_path}' has errors: {error_msgs}", data)

    except FileNotFoundError:
        return error_result(f"File not found: {file_path}")
    except Exception as e:
        logger.exception(f"Error validating workflow via MCP: {e}")
        return error_result(f"Error validating workflow: {str(e)}")



async def get_workflow(
    context: Any,
    workflow_id: str | None = None,
    workflow_name: str | None = None,
) -> ToolResult:
    """Get detailed workflow metadata."""
    from uuid import UUID

    from src.repositories.workflows import WorkflowRepository

    logger.info(f"MCP get_workflow called with id={workflow_id}, name={workflow_name}")

    if not workflow_id and not workflow_name:
        return error_result("Either workflow_id or workflow_name is required")

    try:
        async with get_tool_db(context) as db:
            ctx_org_id = UUID(str(context.org_id)) if context.org_id else None
            ctx_user_id = UUID(str(context.user_id)) if context.user_id else None
            repo = WorkflowRepository(
                db,
                org_id=ctx_org_id,
                user_id=ctx_user_id,
                is_superuser=context.is_platform_admin,
            )

            if workflow_id:
                try:
                    workflow = await repo.get(id=UUID(workflow_id))
                except ValueError:
                    return error_result(f"Invalid workflow_id format: {workflow_id}")
            else:
                workflow = await repo.get_by_name(workflow_name)  # type: ignore

            if not workflow:
                return error_result(f"Workflow not found: {workflow_id or workflow_name}")

            data = {
                "id": str(workflow.id),
                "name": workflow.name,
                "description": workflow.description,
                "type": workflow.type,
                "category": workflow.category,
                "is_active": workflow.is_active,
                "path": workflow.path,
                "endpoint_enabled": workflow.endpoint_enabled,
                "tool_description": workflow.tool_description if workflow.type == "tool" else None,
                "parameters": workflow.parameters_schema,
            }

            desc = f" - {workflow.description}" if workflow.description else ""
            display_text = f"Workflow: {workflow.name} ({workflow.type}){desc}"
            return success_result(display_text, data)

    except Exception as e:
        logger.exception(f"Error getting workflow via MCP: {e}")
        return error_result(f"Error getting workflow: {str(e)}")


async def register_workflow(context: Any, path: str, function_name: str, organization_id: str = "") -> ToolResult:
    """Register a decorated Python function as a workflow.

    Takes a file path and function name, validates the function has a
    @workflow/@tool/@data_provider decorator, and registers it in the system.
    """
    import ast
    from uuid import UUID, uuid4

    from sqlalchemy import select

    from src.models.orm.workflows import Workflow as WorkflowORM
    from src.services.file_storage import FileStorageService
    from src.services.file_storage.indexers.workflow import WorkflowIndexer

    if not path:
        return error_result("path is required")
    if not function_name:
        return error_result("function_name is required")
    if not path.endswith(".py"):
        return error_result("path must be a .py file")

    try:
        async with get_tool_db(context) as db:
            service = FileStorageService(db)

            # Read file
            try:
                content, _ = await service.read_file(path)
            except FileNotFoundError:
                return error_result(f"File not found: {path}")

            # AST parse and find decorated function
            content_str = content.decode("utf-8", errors="replace")
            try:
                tree = ast.parse(content_str, filename=path)
            except SyntaxError as e:
                return error_result(f"Syntax error in {path}: {e}")

            found = False
            decorator_type = None
            for node in ast.walk(tree):
                if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                if node.name != function_name:
                    continue
                for dec in node.decorator_list:
                    dec_name = None
                    if isinstance(dec, ast.Name):
                        dec_name = dec.id
                    elif isinstance(dec, ast.Call) and isinstance(dec.func, ast.Name):
                        dec_name = dec.func.id
                    if dec_name in ("workflow", "tool", "data_provider"):
                        found = True
                        decorator_type = dec_name
                        break
                if found:
                    break

            if not found:
                return error_result(
                    f"No @workflow/@tool/@data_provider decorated function '{function_name}' found in {path}"
                )

            # Check already registered
            existing = await db.execute(
                select(WorkflowORM).where(
                    WorkflowORM.path == path,
                    WorkflowORM.function_name == function_name,
                    WorkflowORM.is_active.is_(True),
                )
            )
            if existing.scalar_one_or_none():
                return error_result(f"Workflow '{function_name}' in {path} is already registered")

            # Create record
            wf_type = "data_provider" if decorator_type == "data_provider" else (
                "tool" if decorator_type == "tool" else "workflow"
            )
            org_uuid = UUID(organization_id) if organization_id else None
            workflow_id = uuid4()
            new_wf = WorkflowORM(
                id=workflow_id,
                name=function_name,
                function_name=function_name,
                path=path,
                type=wf_type,
                is_active=True,
                organization_id=org_uuid,
            )
            db.add(new_wf)
            await db.flush()

            # Enrich with decorator metadata
            indexer = WorkflowIndexer(db)
            await indexer.index_python_file(path, content)

            # Re-fetch enriched record
            result = await db.execute(
                select(WorkflowORM).where(WorkflowORM.id == workflow_id)
            )
            workflow = result.scalar_one()

            return success_result(
                f"Registered {wf_type} '{workflow.name}' from {path}::{function_name}",
                {
                    "id": str(workflow.id),
                    "name": workflow.name,
                    "function_name": workflow.function_name,
                    "path": workflow.path,
                    "type": workflow.type,
                },
            )
    except Exception as e:
        logger.error(f"register_workflow failed: {e}", exc_info=True)
        return error_result(str(e))


# ---------------------------------------------------------------------------
# Lifecycle thin wrappers (Task 6)
# ---------------------------------------------------------------------------


async def update_workflow(
    context: Any,
    workflow_ref: str,
    organization_id: str | None = None,
    access_level: str | None = None,
    clear_roles: bool | None = None,
    role_ids: list[str] | None = None,
    name: str | None = None,
    description: str | None = None,
    category: str | None = None,
    timeout_seconds: int | None = None,
    tags: list[str] | None = None,
    endpoint_enabled: bool | None = None,
    public_endpoint: bool | None = None,
) -> ToolResult:
    """Update a workflow — ``PATCH /api/workflows/{uuid}``.

    ``workflow_ref`` is a UUID, workflow name, or ``path::func``.
    ``role_ids`` bulk-replaces the workflow's role assignments when supplied
    (an empty list clears them); pair with ``clear_roles=True`` only when no
    list is provided. ``name`` is the MCP tool name; ``function_name`` is not
    changed by this tool. Fields marked as UI/code-managed in
    :data:`bifrost.dto_flags.DTO_EXCLUDES` (``display_name``,
    ``tool_description``, ``time_saved``, ``value``, ``cache_ttl_seconds``,
    ``allowed_methods``, ``execution_mode``, ``disable_global_key``) are not
    surfaced here.
    """
    if not workflow_ref:
        return error_result("workflow_ref is required")

    from bifrost.dto_flags import DTO_EXCLUDES, assemble_body
    from bifrost.refs import RefResolver
    from src.models.contracts.workflows import WorkflowUpdateRequest

    exclude = DTO_EXCLUDES.get("WorkflowUpdateRequest", set())

    async with rest_client(context) as http:
        resolver = RefResolver(http)
        try:
            workflow_uuid = await resolver.resolve("workflow", workflow_ref)
        except Exception as exc:
            return error_result(
                f"could not resolve workflow {workflow_ref!r}",
                _ref_error_payload(exc),
            )

        fields: dict[str, Any] = {
            "organization_id": organization_id,
            "access_level": access_level,
            "clear_roles": clear_roles,
            "role_ids": role_ids,
            "name": name,
            "description": description,
            "category": category,
            "timeout_seconds": timeout_seconds,
            "tags": tags,
            "endpoint_enabled": endpoint_enabled,
            "public_endpoint": public_endpoint,
        }
        try:
            body = await assemble_body(
                WorkflowUpdateRequest,
                {k: v for k, v in fields.items() if k not in exclude},
                resolver=resolver,
            )
        except Exception as exc:
            return error_result(f"invalid input: {exc}", _ref_error_payload(exc))

    status_code, resp = await call_rest(
        context, "PATCH", f"/api/workflows/{workflow_uuid}", json_body=body
    )
    if status_code != 200:
        return error_result(
            f"update_workflow failed: HTTP {status_code}", {"body": resp}
        )
    return success_result(
        f"Updated workflow {workflow_uuid}",
        resp if isinstance(resp, dict) else {"body": resp},
    )


async def delete_workflow(
    context: Any,
    workflow_ref: str,
    force_deactivation: bool = False,
) -> ToolResult:
    """Delete a workflow — ``DELETE /api/workflows/{uuid}``.

    On first call, the endpoint returns 409 with deactivation details if
    the workflow has history or dependencies. Call again with
    ``force_deactivation=True`` to commit the deletion.
    """
    if not workflow_ref:
        return error_result("workflow_ref is required")

    from bifrost.refs import RefResolver

    async with rest_client(context) as http:
        resolver = RefResolver(http)
        try:
            workflow_uuid = await resolver.resolve("workflow", workflow_ref)
        except Exception as exc:
            return error_result(
                f"could not resolve workflow {workflow_ref!r}",
                _ref_error_payload(exc),
            )

    body: dict[str, Any] | None = None
    if force_deactivation:
        body = {"force_deactivation": True}

    status_code, resp = await call_rest(
        context, "DELETE", f"/api/workflows/{workflow_uuid}", json_body=body
    )
    if status_code == 409:
        return error_result(
            "workflow has dependencies or history; retry with force_deactivation=true",
            resp if isinstance(resp, dict) else {"body": resp},
        )
    if status_code not in (200, 204):
        return error_result(
            f"delete_workflow failed: HTTP {status_code}", {"body": resp}
        )
    return success_result(
        f"Deleted workflow {workflow_uuid}", {"deleted": workflow_uuid}
    )


async def grant_workflow_role(
    context: Any,
    workflow_ref: str,
    role_ref: str,
) -> ToolResult:
    """Grant a role on a workflow — ``POST /api/workflows/{uuid}/roles``.

    ``workflow_ref`` and ``role_ref`` are UUIDs or names. The REST endpoint
    is a batch assign that skips already-assigned roles, so this wrapper is
    idempotent.
    """
    if not workflow_ref:
        return error_result("workflow_ref is required")
    if not role_ref:
        return error_result("role_ref is required")

    from bifrost.refs import RefResolver

    async with rest_client(context) as http:
        resolver = RefResolver(http)
        try:
            workflow_uuid = await resolver.resolve("workflow", workflow_ref)
        except Exception as exc:
            return error_result(
                f"could not resolve workflow {workflow_ref!r}",
                _ref_error_payload(exc),
            )
        try:
            role_uuid = await resolver.resolve("role", role_ref)
        except Exception as exc:
            return error_result(
                f"could not resolve role {role_ref!r}",
                _ref_error_payload(exc),
            )

    status_code, resp = await call_rest(
        context,
        "POST",
        f"/api/workflows/{workflow_uuid}/roles",
        json_body={"role_ids": [role_uuid]},
    )
    if status_code not in (200, 201, 204):
        return error_result(
            f"grant_workflow_role failed: HTTP {status_code}", {"body": resp}
        )
    return success_result(
        f"Granted role {role_uuid} on workflow {workflow_uuid}",
        {"workflow_id": workflow_uuid, "role_id": role_uuid},
    )


async def revoke_workflow_role(
    context: Any,
    workflow_ref: str,
    role_ref: str,
) -> ToolResult:
    """Revoke a role on a workflow — ``DELETE /api/workflows/{uuid}/roles/{role_id}``."""
    if not workflow_ref:
        return error_result("workflow_ref is required")
    if not role_ref:
        return error_result("role_ref is required")

    from bifrost.refs import RefResolver

    async with rest_client(context) as http:
        resolver = RefResolver(http)
        try:
            workflow_uuid = await resolver.resolve("workflow", workflow_ref)
        except Exception as exc:
            return error_result(
                f"could not resolve workflow {workflow_ref!r}",
                _ref_error_payload(exc),
            )
        try:
            role_uuid = await resolver.resolve("role", role_ref)
        except Exception as exc:
            return error_result(
                f"could not resolve role {role_ref!r}",
                _ref_error_payload(exc),
            )

    status_code, resp = await call_rest(
        context,
        "DELETE",
        f"/api/workflows/{workflow_uuid}/roles/{role_uuid}",
    )
    if status_code not in (200, 204):
        return error_result(
            f"revoke_workflow_role failed: HTTP {status_code}", {"body": resp}
        )
    return success_result(
        f"Revoked role {role_uuid} on workflow {workflow_uuid}",
        {"workflow_id": workflow_uuid, "role_id": role_uuid},
    )


# Tool metadata for registration
TOOLS = [
    ("execute_workflow", "Execute Workflow", "Execute a Bifrost workflow by ID or name and return the results. Use list_workflows to get workflow IDs."),
    ("list_workflows", "List Workflows", "List workflows registered in Bifrost."),
    ("validate_workflow", "Validate Workflow", "Validate a workflow Python file for syntax and decorator issues."),
    ("get_workflow", "Get Workflow", "Get detailed metadata for a specific workflow by ID or name."),
    ("register_workflow", "Register Workflow", "Register a decorated Python function as a workflow. Takes a file path, function name, and optional organization_id."),
    ("update_workflow", "Update Workflow", "Update an existing workflow by UUID or name (organization, access level, description, etc.)."),
    ("delete_workflow", "Delete Workflow", "Delete a workflow; returns 409 with deactivation details if it has history."),
    ("grant_workflow_role", "Grant Workflow Role", "Grant a role access to a workflow."),
    ("revoke_workflow_role", "Revoke Workflow Role", "Revoke a role's access to a workflow."),
]


def register_tools(mcp: Any, get_context_fn: Any) -> None:
    """Register all workflow tools with FastMCP."""
    from src.services.mcp_server.generators.fastmcp_generator import register_tool_with_context

    tool_funcs = {
        "execute_workflow": execute_workflow,
        "list_workflows": list_workflows,
        "validate_workflow": validate_workflow,
        "get_workflow": get_workflow,
        "register_workflow": register_workflow,
        "update_workflow": update_workflow,
        "delete_workflow": delete_workflow,
        "grant_workflow_role": grant_workflow_role,
        "revoke_workflow_role": revoke_workflow_role,
    }

    for tool_id, name, description in TOOLS:
        register_tool_with_context(mcp, tool_funcs[tool_id], tool_id, description, get_context_fn)
