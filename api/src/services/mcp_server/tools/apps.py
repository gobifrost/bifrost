"""Application MCP tools backed by the canonical REST API.

Application metadata, dependency, validation, publish, and source-path tools
reuse the same REST authorization, validation, audit, manifest, cache, and
Platform Job behavior as the web client and CLI. ``push_files`` remains the
legacy workspace batch operation until the workspace/file catalog slice moves
it to its proper domain.
"""

from __future__ import annotations

import logging
from typing import Any

from fastmcp.tools import ToolResult

from src.services.mcp_server.tool_result import error_result, success_result
from src.services.mcp_server.tools._http_bridge import call_rest, rest_client
from src.services.mcp_server.tools.db import get_tool_db

logger = logging.getLogger(__name__)


def _ref_error_payload(exc: Exception) -> dict[str, Any]:
    from bifrost.refs import AmbiguousRefError, RefNotFoundError

    if isinstance(exc, AmbiguousRefError):
        return {"kind": exc.kind, "value": exc.value, "candidates": exc.candidates}
    if isinstance(exc, RefNotFoundError):
        return {"kind": exc.kind, "value": exc.value}
    return {"detail": str(exc)}


def _rest_error(action: str, status_code: int, body: Any) -> ToolResult:
    detail = body.get("detail") if isinstance(body, dict) else None
    if isinstance(detail, dict):
        message = detail.get("message") or detail.get("detail")
    else:
        message = detail
    return error_result(
        str(message) if message else f"{action} failed: HTTP {status_code}",
        {"status_code": status_code, "body": body},
    )


async def _resolve_app_ref(context: Any, app_ref: str) -> str:
    from bifrost.refs import RefResolver

    async with rest_client(context) as http:
        return await RefResolver(http).resolve("app", app_ref)


async def _assemble_app_body(
    context: Any,
    fields: dict[str, Any],
    *,
    is_update: bool,
    scope: str | None,
) -> dict[str, Any]:
    from bifrost.dto_flags import assemble_body
    from bifrost.refs import RefResolver
    from src.models.contracts.applications import ApplicationCreate, ApplicationUpdate

    model_cls = ApplicationUpdate if is_update else ApplicationCreate
    async with rest_client(context) as http:
        resolver = RefResolver(http)
        body = await assemble_body(model_cls, fields, resolver=resolver)
        if scope is not None:
            if scope == "global":
                body["organization_id"] = None
            else:
                body["organization_id"] = await resolver.resolve("org", scope)
    return body


async def bifrost_list_apps(
    context: Any,
    scope: str | None = None,
) -> ToolResult:
    """List Applications visible to the caller through REST."""

    status_code, body = await call_rest(
        context,
        "GET",
        "/api/applications",
        params={"scope": scope} if scope is not None else None,
    )
    if status_code != 200 or not isinstance(body, dict):
        return _rest_error("List Applications", status_code, body)
    applications = body.get("applications", [])
    return success_result(
        f"Found {len(applications)} Application(s)",
        {"applications": applications, "count": len(applications)},
    )


async def bifrost_get_app(context: Any, app_ref: str) -> ToolResult:
    """Get one Application by UUID, slug, or unambiguous name."""

    if not app_ref:
        return error_result("app_ref is required")
    try:
        app_id = await _resolve_app_ref(context, app_ref)
    except Exception as exc:
        return error_result(
            f"Could not resolve Application {app_ref!r}",
            _ref_error_payload(exc),
        )
    status_code, body = await call_rest(context, "GET", "/api/applications")
    if status_code != 200 or not isinstance(body, dict):
        return _rest_error("Get Application", status_code, body)
    payload = next(
        (
            app
            for app in body.get("applications", [])
            if isinstance(app, dict) and str(app.get("id")) == app_id
        ),
        None,
    )
    if payload is None:
        return error_result(
            f"Application {app_ref!r} is not in the accessible list",
            {"app_id": app_id},
        )
    return success_result(f"Application: {payload.get('name', app_id)}", payload)


async def bifrost_create_app(
    context: Any,
    name: str,
    slug: str,
    description: str | None = None,
    access_level: str = "authenticated",
    app_model: str = "standalone_v2",
    role_ids: list[str] | None = None,
    scope: str | None = None,
) -> ToolResult:
    """Create an Application through ``POST /api/applications``.

    ``scope`` is ``global``, an organization UUID/name, or omitted for the
    caller's home organization. Loose Applications must explicitly use
    ``app_model='inline_v1'``; v2 Apps are created by Solution deployment.
    """

    try:
        body = await _assemble_app_body(
            context,
            {
                "name": name,
                "slug": slug,
                "description": description,
                "access_level": access_level,
                "app_model": app_model,
                "role_ids": role_ids,
            },
            is_update=False,
            scope=scope,
        )
    except Exception as exc:
        return error_result(
            f"Invalid Application input: {exc}", _ref_error_payload(exc)
        )
    status_code, response = await call_rest(
        context,
        "POST",
        "/api/applications",
        json_body=body,
    )
    if status_code != 201:
        return _rest_error("Create Application", status_code, response)
    payload = response if isinstance(response, dict) else {"body": response}
    return success_result(f"Created Application: {payload.get('name', name)}", payload)


async def bifrost_update_app(
    context: Any,
    app_ref: str,
    name: str | None = None,
    slug: str | None = None,
    description: str | None = None,
    access_level: str | None = None,
    role_ids: list[str] | None = None,
    scope: str | None = None,
) -> ToolResult:
    """Update Application metadata through ``PATCH /api/applications/{id}``."""

    if not app_ref:
        return error_result("app_ref is required")
    try:
        app_id = await _resolve_app_ref(context, app_ref)
        body = await _assemble_app_body(
            context,
            {
                "name": name,
                "slug": slug,
                "description": description,
                "access_level": access_level,
                "role_ids": role_ids,
            },
            is_update=True,
            scope=scope,
        )
    except Exception as exc:
        return error_result(
            f"Invalid Application input: {exc}", _ref_error_payload(exc)
        )
    if not body:
        return error_result("No updates provided")
    status_code, response = await call_rest(
        context,
        "PATCH",
        f"/api/applications/{app_id}",
        json_body=body,
    )
    if status_code != 200:
        return _rest_error("Update Application", status_code, response)
    payload = response if isinstance(response, dict) else {"body": response}
    return success_result(
        f"Updated Application: {payload.get('name', app_id)}", payload
    )


async def bifrost_delete_app(context: Any, app_ref: str) -> ToolResult:
    """Delete an Application through the canonical REST endpoint."""

    if not app_ref:
        return error_result("app_ref is required")
    try:
        app_id = await _resolve_app_ref(context, app_ref)
    except Exception as exc:
        return error_result(
            f"Could not resolve Application {app_ref!r}",
            _ref_error_payload(exc),
        )
    status_code, body = await call_rest(
        context,
        "DELETE",
        f"/api/applications/{app_id}",
    )
    if status_code != 204:
        return _rest_error("Delete Application", status_code, body)
    return success_result("Deleted Application", {"success": True, "id": app_id})


async def bifrost_publish_app(
    context: Any,
    app_ref: str,
    message: str | None = None,
) -> ToolResult:
    """Queue publishing through the canonical REST build-and-promote path."""
    if not app_ref:
        return error_result("app_ref is required")
    try:
        app_id = await _resolve_app_ref(context, app_ref)
    except Exception as exc:
        return error_result(
            f"Could not resolve Application {app_ref!r}",
            _ref_error_payload(exc),
        )
    logger.info("MCP bifrost_publish_app (HTTP bridge) id=%s", app_id)
    status_code, body = await call_rest(
        context,
        "POST",
        f"/api/applications/{app_id}/publish",
        json_body={"message": message} if message else {},
    )
    if status_code != 202 or not isinstance(body, dict):
        return _rest_error("Publish Application", status_code, body)
    job_id = body.get("job_id")
    return success_result(
        f"Application publish queued: {job_id}",
        body,
    )


async def bifrost_get_app_publish_status(
    context: Any,
    publish_job_id: str,
) -> ToolResult:
    """Read durable publish progress through the canonical REST endpoint."""
    logger.info(
        "MCP bifrost_get_app_publish_status (HTTP bridge) job=%s",
        publish_job_id,
    )
    status_code, body = await call_rest(
        context,
        "GET",
        f"/api/platform-jobs/{publish_job_id}",
    )
    if status_code != 200 or not isinstance(body, dict):
        return _rest_error("Get Application publish status", status_code, body)
    status_value = body.get("status", "unknown")
    progress = body.get("progress") or {}
    phase = progress.get("phase")
    description = f"Application publish {status_value}"
    if phase:
        description += f": {phase}"
    if status_value in ("failed", "cancelled"):
        error = body.get("error") or {}
        return error_result(
            error.get("message") or description,
            body,
        )
    return success_result(description, body)


async def bifrost_replace_app(
    context: Any,
    app_ref: str,
    repo_path: str,
    force: bool = False,
) -> ToolResult:
    """Repoint an application's source directory — thin wrapper over
    ``POST /api/applications/{app_id}/replace``.

    Updates ``repo_path`` after source files have been moved/renamed. The
    server validates that the new path is unique, non-nested with other
    apps, and has source files under it. ``force=True`` bypasses all
    three checks.
    """
    if not app_ref:
        return error_result("app_ref is required")
    if not repo_path:
        return error_result("repo_path is required")

    try:
        app_id = await _resolve_app_ref(context, app_ref)
    except Exception as exc:
        return error_result(
            f"Could not resolve Application {app_ref!r}",
            _ref_error_payload(exc),
        )

    body: dict[str, Any] = {"repo_path": repo_path, "force": force}
    status_code, resp = await call_rest(
        context, "POST", f"/api/applications/{app_id}/replace", json_body=body
    )
    if status_code != 200:
        return _rest_error("Replace Application source path", status_code, resp)
    return success_result(
        f"Repointed application {app_id} to {repo_path}",
        resp if isinstance(resp, dict) else {"body": resp},
    )


async def bifrost_validate_app(context: Any, app_ref: str) -> ToolResult:
    """Validate an Application through ``POST /api/applications/{id}/validate``."""

    if not app_ref:
        return error_result("app_ref is required")
    try:
        app_id = await _resolve_app_ref(context, app_ref)
    except Exception as exc:
        return error_result(
            f"Could not resolve Application {app_ref!r}",
            _ref_error_payload(exc),
        )
    status_code, response = await call_rest(
        context,
        "POST",
        f"/api/applications/{app_id}/validate",
    )
    if status_code != 200:
        return _rest_error("Validate Application", status_code, response)
    payload = response if isinstance(response, dict) else {"body": response}
    errors = payload.get("errors", [])
    warnings = payload.get("warnings", [])
    return success_result(
        f"Application validation: {len(errors)} error(s), {len(warnings)} warning(s)",
        payload,
    )


async def push_files(
    context: Any,
    files: dict[str, str],
    delete_missing_prefix: str | None = None,
) -> ToolResult:
    """
    Push multiple files to _repo/ in a single batch.

    Useful for creating or updating multiple files at once (e.g., pushing
    an entire app or workflow set).

    Args:
        files: Map of repo_path to content, e.g. {"apps/my-app/pages/index.tsx": "..."}
        delete_missing_prefix: If set, delete files under this prefix not in batch
    """
    import hashlib

    from sqlalchemy import select

    from src.models.orm.applications import Application
    from src.models.orm.file_index import FileIndex
    from src.services.app_storage import AppStorageService
    from src.services.file_storage import FileStorageService
    from src.services.solutions.guard import (
        SOLUTION_MANAGED_MESSAGE,
        is_solution_managed,
    )

    logger.info(f"MCP push_files called with {len(files)} file(s)")

    try:
        async with get_tool_db(context) as db:
            # Refuse before any S3 write: file_storage.write_file (_repo) and
            # app_storage.write_preview_file (preview) both write S3 without
            # dirtying the Application row, so the before_flush backstop never
            # fires for them (criterion 6). Reject the whole batch if ANY pushed
            # file lands under a solution-managed app's repo_path.
            all_apps = (await db.execute(select(Application))).scalars().all()
            managed_prefixes = [
                app_obj.repo_path.rstrip("/") + "/"
                for app_obj in all_apps
                if is_solution_managed(app_obj)
            ]
            blocked = sorted(
                repo_path
                for repo_path in files
                if any(repo_path.startswith(p) for p in managed_prefixes)
            )
            if blocked:
                return error_result(
                    SOLUTION_MANAGED_MESSAGE,
                    {"blocked_paths": blocked},
                )

            file_storage = FileStorageService(db)
            created = 0
            updated = 0
            unchanged = 0
            deleted = 0
            push_errors: list[str] = []

            for repo_path, content in files.items():
                try:
                    existing = await db.execute(
                        select(FileIndex.content_hash).where(
                            FileIndex.path == repo_path
                        )
                    )
                    existing_hash = existing.scalar_one_or_none()

                    content_bytes = content.encode("utf-8")
                    new_hash = hashlib.sha256(content_bytes).hexdigest()

                    if existing_hash == new_hash:
                        unchanged += 1
                        continue

                    was_new = existing_hash is None
                    await file_storage.write_file(
                        path=repo_path,
                        content=content_bytes,
                        updated_by=str(context.user_id),
                    )

                    if was_new:
                        created += 1
                    else:
                        updated += 1
                except Exception as e:
                    push_errors.append(f"{repo_path}: {str(e)}")

            if delete_missing_prefix:
                prefix = delete_missing_prefix
                if not prefix.endswith("/"):
                    prefix += "/"
                # The delete-sweep is a separate write path from the files-key
                # guard above: an empty/partial `files` dict slips past the key
                # check, but the sweep would still delete _repo files under
                # `prefix`. Refuse if the sweep would touch ANY solution-managed
                # app's files — in either direction: the delete prefix is under a
                # managed prefix (delete "apps/managed/sub"), OR contains/equals
                # one (delete "apps/" which would sweep "apps/managed/...").
                if any(
                    prefix.startswith(managed) or managed.startswith(prefix)
                    for managed in managed_prefixes
                ):
                    return error_result(
                        SOLUTION_MANAGED_MESSAGE,
                        {"blocked_delete_prefix": delete_missing_prefix},
                    )
                existing_files = await db.execute(
                    select(FileIndex.path).where(FileIndex.path.startswith(prefix))
                )
                existing_paths = {row[0] for row in existing_files.all()}
                push_paths = set(files.keys())
                for path_to_delete in existing_paths - push_paths:
                    try:
                        await file_storage.delete_file(path_to_delete)
                        deleted += 1
                    except Exception as e:
                        push_errors.append(f"delete {path_to_delete}: {str(e)}")

            await db.commit()

            # Compile app files that were pushed
            compile_warnings = []
            app_file_groups: dict[str, list[dict[str, str]]] = {}  # app_id -> files

            # Build prefix -> app mapping
            app_by_prefix: dict[str, Application] = {}
            for app_obj in all_apps:
                prefix = app_obj.repo_path.rstrip("/") + "/"
                app_by_prefix[prefix] = app_obj

            for repo_path, content in files.items():
                if not repo_path.endswith((".tsx", ".ts")):
                    continue
                for prefix, app_obj in app_by_prefix.items():
                    if repo_path.startswith(prefix):
                        rel_path = repo_path[len(prefix) :]
                        app_file_groups.setdefault(str(app_obj.id), []).append(
                            {"path": rel_path, "source": content}
                        )
                        break

            if app_file_groups:
                from src.services.app_compiler import AppCompilerService

                compiler = AppCompilerService()
                app_lookup = {str(a.id): a for a in all_apps}
                for app_id_str, app_files in app_file_groups.items():
                    app = app_lookup.get(app_id_str)
                    if not app:
                        continue

                    # Batch compile
                    results = await compiler.compile_batch(app_files)
                    app_storage = AppStorageService()

                    for result in results:
                        if result.success and result.compiled:
                            await app_storage.write_preview_file(
                                str(app.id),
                                result.path,
                                result.compiled.encode("utf-8"),
                            )
                        else:
                            compile_warnings.append(f"✗ {result.path}: {result.error}")

            parts = []
            if created:
                parts.append(f"{created} created")
            if updated:
                parts.append(f"{updated} updated")
            if deleted:
                parts.append(f"{deleted} deleted")
            if unchanged:
                parts.append(f"{unchanged} unchanged")

            summary = ", ".join(parts) if parts else "No changes"
            display_text = f"Push complete: {summary}"
            if push_errors:
                display_text += f"\n\nErrors ({len(push_errors)}):\n" + "\n".join(
                    f"  - {e}" for e in push_errors
                )

            if compile_warnings:
                display_text += f"\n\nCompilation ({len(compile_warnings)} issue(s)):\n"
                display_text += "\n".join(f"  {w}" for w in compile_warnings)

            return success_result(
                display_text,
                {
                    "created": created,
                    "updated": updated,
                    "deleted": deleted,
                    "unchanged": unchanged,
                    "errors": push_errors,
                    "compile_warnings": compile_warnings,
                },
            )

    except Exception as e:
        logger.exception(f"Error pushing files: {e}")
        return error_result(f"Error pushing files: {str(e)}")


async def bifrost_get_app_dependencies(
    context: Any,
    app_ref: str,
) -> ToolResult:
    """Get one Application's npm dependencies through REST."""
    if not app_ref:
        return error_result("app_ref is required")
    try:
        app_id = await _resolve_app_ref(context, app_ref)
    except Exception as exc:
        return error_result(
            f"Could not resolve Application {app_ref!r}",
            _ref_error_payload(exc),
        )
    status_code, response = await call_rest(
        context,
        "GET",
        f"/api/applications/{app_id}/dependencies",
    )
    if status_code != 200 or not isinstance(response, dict):
        return _rest_error("Get Application dependencies", status_code, response)
    return success_result(
        f"Application dependencies: {len(response)} package(s)",
        {"app_id": app_id, "dependencies": response},
    )


async def bifrost_update_app_dependencies(
    context: Any,
    app_ref: str,
    dependencies: dict[str, str],
) -> ToolResult:
    """Replace one Application's npm dependencies through REST."""
    if not app_ref:
        return error_result("app_ref is required")
    try:
        app_id = await _resolve_app_ref(context, app_ref)
    except Exception as exc:
        return error_result(
            f"Could not resolve Application {app_ref!r}",
            _ref_error_payload(exc),
        )
    status_code, response = await call_rest(
        context,
        "PUT",
        f"/api/applications/{app_id}/dependencies",
        json_body=dependencies,
    )
    if status_code != 200 or not isinstance(response, dict):
        return _rest_error("Update Application dependencies", status_code, response)
    return success_result(
        f"Updated Application dependencies: {len(response)} package(s)",
        {"app_id": app_id, "dependencies": response},
    )


# Tool metadata for registration
TOOLS = [
    (
        "bifrost_list_apps",
        "List Applications",
        "List Applications visible to the caller.",
    ),
    (
        "bifrost_get_app",
        "Get Application",
        "Get Application metadata by UUID, slug, or unambiguous name.",
    ),
    (
        "bifrost_create_app",
        "Create Application",
        "Create a loose Application through the canonical REST contract.",
    ),
    (
        "bifrost_update_app",
        "Update Application",
        "Update Application metadata and access through REST.",
    ),
    ("bifrost_delete_app", "Delete Application", "Delete an Application through REST."),
    (
        "bifrost_publish_app",
        "Publish Application",
        "Queue a durable rebuild and publish.",
    ),
    (
        "bifrost_get_app_publish_status",
        "Get Application Publish Status",
        "Get progress, result, or error for an Application publish job.",
    ),
    (
        "bifrost_replace_app",
        "Replace Application Source Path",
        "Repoint an Application's workspace source directory.",
    ),
    (
        "bifrost_validate_app",
        "Validate Application",
        "Compile and validate an Application through REST.",
    ),
    (
        "push_files",
        "Push Files",
        "Push multiple files to _repo/ in a single batch. Useful for creating or updating entire apps or workflow sets.",
    ),
    (
        "bifrost_get_app_dependencies",
        "Get Application Dependencies",
        "Get npm dependencies declared for an Application.",
    ),
    (
        "bifrost_update_app_dependencies",
        "Update Application Dependencies",
        "Replace npm dependencies for an Application.",
    ),
]


def register_tools(mcp: Any, get_context_fn: Any) -> None:
    """Register all apps tools with FastMCP."""
    from src.services.mcp_server.generators.fastmcp_generator import (
        register_tool_with_context,
    )

    tool_funcs = {
        "bifrost_list_apps": bifrost_list_apps,
        "bifrost_get_app": bifrost_get_app,
        "bifrost_create_app": bifrost_create_app,
        "bifrost_update_app": bifrost_update_app,
        "bifrost_delete_app": bifrost_delete_app,
        "bifrost_publish_app": bifrost_publish_app,
        "bifrost_get_app_publish_status": bifrost_get_app_publish_status,
        "bifrost_replace_app": bifrost_replace_app,
        "bifrost_validate_app": bifrost_validate_app,
        "push_files": push_files,
        "bifrost_get_app_dependencies": bifrost_get_app_dependencies,
        "bifrost_update_app_dependencies": bifrost_update_app_dependencies,
    }

    for tool_id, name, description in TOOLS:
        register_tool_with_context(
            mcp, tool_funcs[tool_id], tool_id, description, get_context_fn
        )
