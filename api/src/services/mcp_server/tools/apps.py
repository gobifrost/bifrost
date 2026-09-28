"""
App Builder MCP Tools — thin wrappers around the REST API.

``bifrost_app_list``, ``bifrost_app_get``, ``bifrost_app_create``,
``bifrost_app_update``, ``bifrost_app_delete``, ``bifrost_app_publish``,
``bifrost_app_replace``, ``bifrost_app_validate``,
``bifrost_app_dependencies_get``, ``bifrost_app_dependencies_update``.

These tools are thin wrappers: they resolve human refs, assemble request
bodies from the shared Application DTOs, then call the corresponding REST
endpoint through the in-process HTTP bridge (``_http_bridge``). No ORM, no
repositories, no ``AsyncSession`` — all side effects (audit logs, cache
invalidation, Solution-managed guards, write-scope checks) happen behind the
REST handler, matching the CLI's path exactly.

Applications are listed/read for any authenticated user (cascade + role
scoping via ``ApplicationRepository`` — the same rows the app launcher
shows). All mutations (create/update/delete/dependencies) require scope
bypass (platform admin or provider-org member) — enforced by REST's
``get_application_for_write_or_404`` / the create handler's explicit check,
not narrowed further here.

``push_files`` has no catalog REST operation (it is a batch ``_repo/``
write spanning many files, not one Application resource) and keeps its
existing name and ORM-backed implementation.
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
    """Resolve an app UUID/slug/name ref to its UUID via :class:`RefResolver`."""
    from bifrost.refs import RefResolver

    async with rest_client(context) as http:
        return await RefResolver(http).resolve("app", app_ref)  # type: ignore[arg-type]


async def bifrost_app_list(context: Any) -> ToolResult:
    """List Applications visible to the caller — thin wrapper over ``GET /api/applications``."""
    status_code, body = await call_rest(context, "GET", "/api/applications")
    if status_code != 200:
        return _rest_error("List Applications", status_code, body)
    apps = body.get("applications") if isinstance(body, dict) else None
    apps = apps if isinstance(apps, list) else []
    apps_data = [
        {
            "id": a.get("id"),
            "name": a.get("name"),
            "slug": a.get("slug"),
            "description": a.get("description"),
            "status": "published" if a.get("is_published") else "draft",
            "is_published": a.get("is_published"),
            "has_unpublished_changes": a.get("has_unpublished_changes"),
            "url": f"/apps/{a.get('slug')}",
        }
        for a in apps
    ]
    return success_result(
        f"Found {len(apps_data)} application(s)",
        {"apps": apps_data, "count": len(apps_data)},
    )


async def bifrost_app_get(
    context: Any,
    app_id: str | None = None,
    app_slug: str | None = None,
) -> ToolResult:
    """Get one Application by UUID, slug, or name.

    The public per-record endpoint (``GET /api/applications/{slug}``) is
    keyed by slug. For a slug ref this is a single round-trip; for a UUID
    or name ref, resolves to a UUID via :class:`RefResolver` then locates
    the record in the ``GET /api/applications`` list payload — mirroring
    ``bifrost apps get``, since the per-record endpoint does not accept
    UUIDs.
    """
    if not app_id and not app_slug:
        return error_result("Either app_id or app_slug is required")

    ref = app_slug or app_id or ""

    # Try the slug endpoint first — works directly for slug refs.
    status_code, body = await call_rest(context, "GET", f"/api/applications/{ref}")
    if status_code == 200 and isinstance(body, dict):
        return success_result(f"Application: {body.get('name', ref)}", body)
    if status_code not in (403, 404):
        return _rest_error("Get Application", status_code, body)

    # Fall through: resolve via UUID/name then locate in the list payload.
    try:
        app_uuid = await _resolve_app_ref(context, ref)
    except Exception as exc:
        return error_result(f"could not resolve app {ref!r}", _ref_error_payload(exc))

    list_status, list_body = await call_rest(context, "GET", "/api/applications")
    if list_status != 200:
        return _rest_error("Get Application", list_status, list_body)
    items = list_body.get("applications") if isinstance(list_body, dict) else None
    items = items if isinstance(items, list) else []
    for item in items:
        if str(item.get("id")) == app_uuid:
            return success_result(f"Application: {item.get('name', app_uuid)}", item)
    return error_result(f"application {ref!r} resolved to {app_uuid} but is not in the accessible list")


async def bifrost_app_create(
    context: Any,
    name: str,
    description: str | None = None,
    slug: str | None = None,
    scope: str = "organization",
    organization_id: str | None = None,
    access_level: str | None = None,
    role_ids: list[str] | None = None,
) -> ToolResult:
    """Create an Application — thin wrapper over ``POST /api/applications`` (platform
    admin only).

    ``slug`` is auto-generated from ``name`` if omitted (lowercased,
    non-alphanumeric runs collapsed to ``-``). ``scope='global'`` omits
    ``organization_id`` from the payload as an explicit null; ``scope=
    'organization'`` uses ``organization_id`` if given, else the caller's
    default org.
    """
    import re

    if not name:
        return error_result("name is required")
    if scope not in ("global", "organization"):
        return error_result("scope must be 'global' or 'organization'")

    if not slug:
        slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")

    body: dict[str, Any] = {"name": name, "slug": slug}
    if description is not None:
        body["description"] = description
    if access_level is not None:
        body["access_level"] = access_level

    try:
        async with rest_client(context) as http:
            from bifrost.refs import RefResolver

            resolver = RefResolver(http)
            if scope == "global":
                body["organization_id"] = None
            elif organization_id:
                body["organization_id"] = await resolver.resolve("org", organization_id)
            if role_ids:
                body["role_ids"] = [await resolver.resolve("role", r) for r in role_ids]
    except Exception as exc:
        return error_result(f"invalid input: {exc}", _ref_error_payload(exc))

    status_code, resp = await call_rest(context, "POST", "/api/applications", json_body=body)
    if status_code not in (200, 201):
        return _rest_error("Create Application", status_code, resp)
    payload = resp if isinstance(resp, dict) else {"body": resp}
    return success_result(
        f"Created application: {payload.get('name', name)}",
        {
            "success": True,
            "id": payload.get("id"),
            "name": payload.get("name"),
            "slug": payload.get("slug"),
            "url": f"/apps/{payload.get('slug')}",
        },
    )


async def bifrost_app_update(
    context: Any,
    app_ref: str,
    name: str | None = None,
    description: str | None = None,
    slug: str | None = None,
    icon: str | None = None,
    scope: str | None = None,
    access_level: str | None = None,
    role_ids: list[str] | None = None,
) -> ToolResult:
    """Update Application metadata — thin wrapper over ``PATCH /api/applications/{uuid}``
    (platform admin only; refused for a Solution-managed app).

    ``app_ref`` is a UUID, slug, or name. Only fields explicitly passed are
    sent. ``scope`` is ``'global'`` or an organization UUID/name (platform
    admin only, per ``ApplicationUpdate.scope``).
    """
    if not app_ref:
        return error_result("app_ref is required")

    body: dict[str, Any] = {}
    if name is not None:
        body["name"] = name
    if description is not None:
        body["description"] = description
    if slug is not None:
        body["slug"] = slug
    if icon is not None:
        body["icon"] = icon
    if access_level is not None:
        body["access_level"] = access_level

    try:
        async with rest_client(context) as http:
            from bifrost.refs import RefResolver

            resolver = RefResolver(http)
            try:
                app_uuid = await resolver.resolve("app", app_ref)
            except Exception as exc:
                return error_result(
                    f"could not resolve app {app_ref!r}", _ref_error_payload(exc)
                )
            if scope is not None:
                body["scope"] = "global" if scope == "global" else await resolver.resolve("org", scope)
            if role_ids is not None:
                body["role_ids"] = [await resolver.resolve("role", r) for r in role_ids]
    except Exception as exc:
        return error_result(f"invalid input: {exc}", _ref_error_payload(exc))

    if not body:
        return error_result("No updates provided")

    status_code, resp = await call_rest(
        context, "PATCH", f"/api/applications/{app_uuid}", json_body=body
    )
    if status_code != 200:
        return _rest_error("Update Application", status_code, resp)
    payload = resp if isinstance(resp, dict) else {"body": resp}
    return success_result(f"Updated application: {payload.get('name', app_uuid)}", payload)


async def bifrost_app_delete(context: Any, app_ref: str) -> ToolResult:
    """Delete an Application — thin wrapper over ``DELETE /api/applications/{uuid}``
    (platform admin only; refused for a Solution-managed app)."""
    if not app_ref:
        return error_result("app_ref is required")
    try:
        app_uuid = await _resolve_app_ref(context, app_ref)
    except Exception as exc:
        return error_result(f"could not resolve app {app_ref!r}", _ref_error_payload(exc))

    status_code, resp = await call_rest(context, "DELETE", f"/api/applications/{app_uuid}")
    if status_code not in (200, 204):
        return _rest_error("Delete Application", status_code, resp)
    return success_result(f"Deleted application {app_uuid}", {"deleted": app_uuid})


async def bifrost_app_publish(context: Any, app_id: str) -> ToolResult:
    """Queue publishing through the canonical REST build-and-promote path —
    thin wrapper over ``POST /api/applications/{app_id}/publish``."""
    logger.info("MCP bifrost_app_publish id=%s", app_id)
    status_code, body = await call_rest(
        context,
        "POST",
        f"/api/applications/{app_id}/publish",
        json_body={},
    )
    if status_code != 202 or not isinstance(body, dict):
        return _rest_error("Publish Application", status_code, body)
    job_id = body.get("job_id")
    return success_result(f"Application publish queued: {job_id}", body)


async def bifrost_app_replace(
    context: Any,
    app_id: str,
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
    if not app_id:
        return error_result("app_id is required")
    if not repo_path:
        return error_result("repo_path is required")

    body: dict[str, Any] = {"repo_path": repo_path, "force": force}
    status_code, resp = await call_rest(
        context, "POST", f"/api/applications/{app_id}/replace", json_body=body
    )
    if status_code != 200:
        return _rest_error("Replace Application Source", status_code, resp)
    return success_result(
        f"Repointed application {app_id} to {repo_path}",
        resp if isinstance(resp, dict) else {"body": resp},
    )


async def bifrost_app_validate(context: Any, app_id: str) -> ToolResult:
    """Validate application files — thin wrapper over
    ``POST /api/applications/{app_id}/validate``.

    Compiles all files and checks for missing/unused dependencies, unknown
    components, invalid workflow IDs, and missing required files.
    """
    if not app_id:
        return error_result("app_id is required")

    status_code, resp = await call_rest(
        context, "POST", f"/api/applications/{app_id}/validate"
    )
    if status_code != 200:
        return _rest_error("Validate Application", status_code, resp)
    payload = resp if isinstance(resp, dict) else {"body": resp}

    errors = payload.get("errors") or []
    warnings = payload.get("warnings") or []
    lines = []
    if errors:
        lines.append(f"Found {len(errors)} error(s):")
        for e in errors:
            line_info = f" (line {e['line']})" if e.get("line") else ""
            lines.append(f"  ✗ [{e.get('file')}{line_info}] {e.get('message')}")
    if warnings:
        lines.append(f"\nFound {len(warnings)} warning(s):")
        for w in warnings:
            lines.append(f"  ⚠ [{w.get('file')}] {w.get('message')}")
    if not errors and not warnings:
        lines.append("✓ No issues found")

    return success_result("\n".join(lines), payload)


async def bifrost_app_dependencies_get(
    context: Any,
    app_id: str | None = None,
    app_slug: str | None = None,
) -> ToolResult:
    """Get npm dependencies declared for an app — thin wrapper over
    ``GET /api/applications/{app_id}/dependencies``."""
    if not app_id and not app_slug:
        return error_result("Either app_id or app_slug is required")

    try:
        app_uuid = await _resolve_app_ref(context, app_id or app_slug)  # type: ignore[arg-type]
    except Exception as exc:
        return error_result(
            f"could not resolve app {(app_id or app_slug)!r}", _ref_error_payload(exc)
        )

    status_code, resp = await call_rest(
        context, "GET", f"/api/applications/{app_uuid}/dependencies"
    )
    if status_code != 200:
        return _rest_error("Get App Dependencies", status_code, resp)
    deps = resp if isinstance(resp, dict) else {}
    if not deps:
        return success_result("No dependencies declared", {"dependencies": {}, "app_id": app_uuid})
    dep_list = ", ".join(f"{k}@{v}" for k, v in deps.items())
    return success_result(f"Dependencies: {dep_list}", {"dependencies": deps, "app_id": app_uuid})


async def bifrost_app_dependencies_update(
    context: Any,
    app_id: str,
    dependencies: dict[str, str],
) -> ToolResult:
    """Replace npm dependencies for an app — thin wrapper over
    ``PUT /api/applications/{app_id}/dependencies`` (platform admin only;
    refused for a Solution-managed app).

    ``dependencies`` is a dict of ``{package_name: version}``. Pass an empty
    dict to remove all.
    """
    if not app_id:
        return error_result("app_id is required")

    status_code, resp = await call_rest(
        context, "PUT", f"/api/applications/{app_id}/dependencies", json_body=dependencies
    )
    if status_code != 200:
        return _rest_error("Update App Dependencies", status_code, resp)
    deps = resp if isinstance(resp, dict) else {}
    if deps:
        dep_list = ", ".join(f"{k}@{v}" for k, v in deps.items())
        display_text = f"Updated dependencies: {dep_list}"
    else:
        display_text = "Removed all dependencies"
    return success_result(display_text, {"dependencies": deps, "app_id": app_id})


async def get_app_schema(context: Any) -> ToolResult:  # noqa: ARG001
    """Get application schema documentation for code-based apps."""
    from src.models.contracts.applications import (
        ApplicationCreate,
        ApplicationUpdate,
    )
    from src.services.mcp_server.schema_utils import models_to_markdown

    app_models = models_to_markdown([
        (ApplicationCreate, "ApplicationCreate (for creating apps)"),
        (ApplicationUpdate, "ApplicationUpdate (for updating apps)"),
    ], "Application Models")

    # Documentation for code-based apps
    overview = r"""# App Builder Schema Documentation

Applications in Bifrost use TypeScript/TSX files.

## Tool Hierarchy

**App Level**: `bifrost_app_list`, `bifrost_app_get`, `bifrost_app_update`, `bifrost_app_publish`
**File Level**: `code_list_files`, `code_get_file`, `code_create_file`, `code_update_file`, `code_delete_file`

## File Structure & Paths

- `_layout.tsx` — Root layout (required, must use `<Outlet />` not `{children}`)
- `_providers.tsx` — Optional providers wrapper
- `pages/*.tsx` — Routes (e.g., `pages/index.tsx` = `/`, `pages/clients/[id].tsx` = `/clients/:id`)
- `components/*.tsx` — Reusable UI components
- `modules/*.ts` — Utility modules

## Imports

App files use standard ES import syntax. The server-side compiler transforms imports automatically:

**Bifrost imports** — platform components, hooks, icons, utilities:
```tsx
import { Button, Card, useWorkflowQuery, useState } from "bifrost";
```

**External npm imports** — packages declared in app dependencies:
```tsx
import dayjs from "dayjs";
import { LineChart, Line } from "recharts";
```

Everything from `"bifrost"` is also available in scope without importing (for backwards compatibility), but **using explicit imports is the recommended pattern**.

### Available from "bifrost"

- **React**: `useState`, `useEffect`, `useMemo`, `useCallback`, `useRef`, etc.
- **Bifrost hooks**: `useWorkflowQuery`, `useWorkflowMutation`, `useUser`, `useNavigate`, `useLocation`, `useParams`
- **Routing**: `Outlet`, `Link`, `NavLink`, `Navigate`
- **UI**: Button, Card, Table, Select, Badge, Input, Skeleton, Pagination, Calendar, DateRangePicker, MultiCombobox, TagsInput, Combobox, Slider, Dialog, Alert, Tabs, etc.
- **Icons**: All lucide-react icons (e.g., `Loader2`, `RefreshCw`, `Check`, `X`)
- **Utilities**: `cn` (className merging), `format` (date-fns)

## External Dependencies (npm packages)

Apps can use npm packages loaded at runtime from esm.sh CDN.

### Managing dependencies:
- Use `bifrost_app_dependencies_get` / `bifrost_app_dependencies_update` tools
- Or use the REST API: `GET/PUT /api/applications/{app_id}/dependencies`
- Dependencies are stored in `.bifrost/apps.yaml` and synced via git

### Using in code:
```tsx
import { LineChart, Line, XAxis, YAxis } from "recharts";
import dayjs from "dayjs";
```

### Rules:
- Max 20 dependencies per app
- Version format: semver with optional `^` or `~` prefix (e.g., `"2.12"`, `"^1.5.3"`)
- Package names: lowercase, hyphens, optional `@scope/` prefix

## Available Components

**Layout**: Card, CardHeader, CardTitle, CardDescription, CardContent, CardFooter, Separator
**Data Display**: Table, TableHeader, TableBody, TableRow, TableHead, TableCell, Badge, Skeleton
**Forms**: Input, Button, Select, Combobox, MultiCombobox, TagsInput, Slider, Calendar, DateRangePicker
**Feedback**: Alert, AlertTitle, AlertDescription, Dialog, DialogTrigger, DialogContent, DialogHeader, DialogTitle, DialogDescription, DialogFooter
**Navigation**: Tabs, TabsList, TabsTrigger, TabsContent, Pagination, Link
**Overlay**: Tooltip, TooltipTrigger, TooltipContent, Popover, PopoverTrigger, PopoverContent

## Workflow Hooks

**CRITICAL: Always use workflow UUIDs, not names.** Get IDs via `bifrost_workflow_list` first.

### useWorkflowQuery(workflowId, params?, options?)

Auto-executes on mount. For reading/loading data.

| Property | Type | Description |
|----------|------|-------------|
| `data` | `T \| null` | Result data (null until completed) |
| `isLoading` | `boolean` | True while executing |
| `isError` | `boolean` | True if failed |
| `error` | `string \| null` | Error message |
| `refetch` | `() => Promise<T>` | Re-execute |
| `logs` | `StreamingLog[]` | Real-time streaming logs |

Options: `{ enabled?: boolean }` — set `false` to defer execution.

### useWorkflowMutation(workflowId)

Manual execution via `execute()`. For user-triggered actions.

| Property | Type | Description |
|----------|------|-------------|
| `execute` | `(params?) => Promise<T>` | Run the workflow, returns result |
| `isLoading` | `boolean` | True while executing |
| `isError` | `boolean` | True if failed |
| `error` | `string \| null` | Error message |
| `data` | `T \| null` | Last result |
| `reset` | `() => void` | Reset state |
| `logs` | `StreamingLog[]` | Real-time streaming logs |

### Quick Patterns

```tsx
// Load data on mount
const { data, isLoading } = useWorkflowQuery("workflow-uuid", { limit: 10 });

// Button-triggered action
const { execute, isLoading } = useWorkflowMutation("workflow-uuid");
const result = await execute({ name: "New Item" });

// Conditional loading
const { data } = useWorkflowQuery("workflow-uuid", { id }, { enabled: !!id });
```

## Layout Tips

- `_layout.tsx`: Use `<Outlet />` (not `{children}`) with `h-full overflow-hidden` on root div
- Scrollable pages: `flex flex-col h-full overflow-hidden` on page root, `shrink-0` on headers, `flex-1 min-h-0 overflow-auto` on scrollable content

"""

    schema_doc = overview + app_models
    return success_result("App Builder schema documentation", {"schema": schema_doc})


async def push_files(
    context: Any,
    files: dict[str, str],
    delete_missing_prefix: str | None = None,
) -> ToolResult:
    """
    Push multiple files to _repo/ in a single batch.

    Useful for creating or updating multiple files at once (e.g., pushing
    an entire app or workflow set). No catalog REST operation exists for
    this batch shape (it spans many files/apps, not one Application
    resource), so this tool keeps its ORM-backed implementation and name.

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

            # Resolve the delete-sweep's target paths (if any) BEFORE any
            # write, so the write-scope check below covers them too and a
            # denial rejects the whole batch atomically — no partial writes.
            delete_prefix_paths: set[str] = set()
            if delete_missing_prefix:
                sweep_prefix = delete_missing_prefix
                if not sweep_prefix.endswith("/"):
                    sweep_prefix += "/"
                # The delete-sweep is a separate write path from the files-key
                # guard above: an empty/partial `files` dict slips past the
                # key check, but the sweep would still delete _repo files
                # under `sweep_prefix`. Refuse if the sweep would touch ANY
                # solution-managed app's files — in either direction: the
                # delete prefix is under a managed prefix (delete
                # "apps/managed/sub"), OR contains/equals one (delete
                # "apps/" which would sweep "apps/managed/...").
                if any(
                    sweep_prefix.startswith(managed) or managed.startswith(sweep_prefix)
                    for managed in managed_prefixes
                ):
                    return error_result(
                        SOLUTION_MANAGED_MESSAGE,
                        {"blocked_delete_prefix": delete_missing_prefix},
                    )
                existing_files = await db.execute(
                    select(FileIndex.path).where(FileIndex.path.startswith(sweep_prefix))
                )
                existing_paths = {row[0] for row in existing_files.all()}
                delete_prefix_paths = existing_paths - set(files.keys())

            # Write scope: writing or deleting any `_repo/` path requires a
            # platform admin — matching REST, where `_repo/` writes are
            # CurrentSuperuser (files.py's editor routes).
            if not getattr(context, "is_platform_admin", False):
                return error_result(
                    "You don't have permission to write one or more of these paths.",
                    {"denied_paths": sorted(set(files.keys()) | delete_prefix_paths)},
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
                        select(FileIndex.content_hash).where(FileIndex.path == repo_path)
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
                # The solution-managed guard and the target-path set were
                # already resolved above (delete_prefix_paths) so the
                # write-scope check could cover them before any write
                # happened.
                for path_to_delete in delete_prefix_paths:
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
                        rel_path = repo_path[len(prefix):]
                        app_file_groups.setdefault(str(app_obj.id), []).append({
                            "path": rel_path, "source": content
                        })
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
                                str(app.id), result.path,
                                result.compiled.encode("utf-8")
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
                display_text += f"\n\nErrors ({len(push_errors)}):\n" + "\n".join(f"  - {e}" for e in push_errors)

            if compile_warnings:
                display_text += f"\n\nCompilation ({len(compile_warnings)} issue(s)):\n"
                display_text += "\n".join(f"  {w}" for w in compile_warnings)

            return success_result(display_text, {
                "created": created,
                "updated": updated,
                "deleted": deleted,
                "unchanged": unchanged,
                "errors": push_errors,
                "compile_warnings": compile_warnings,
            })

    except Exception as e:
        logger.exception(f"Error pushing files: {e}")
        return error_result(f"Error pushing files: {str(e)}")


# Tool metadata for registration
TOOLS = [
    ("bifrost_app_list", "List Applications", "List all App Builder applications with file counts and URLs."),
    ("bifrost_app_create", "Create Application", "Create a new App Builder application with scaffold files."),
    ("bifrost_app_get", "Get Application", "Get application metadata and file list."),
    ("bifrost_app_update", "Update Application", "Update application metadata (name, description, slug, icon, scope, access, roles)."),
    ("bifrost_app_delete", "Delete Application", "Delete an application."),
    ("bifrost_app_publish", "Publish Application", "Queue a rebuild and publish; returns a durable publish job ID."),
    ("bifrost_app_replace", "Replace Application Source Path", "Repoint an application's repo_path after source files have been moved/renamed."),
    ("bifrost_app_validate", "Validate Application", "Build and validate an app: compiles all files, checks for missing/unused dependencies, unknown components, and bad workflow IDs."),
    ("push_files", "Push Files", "Push multiple files to _repo/ in a single batch. Useful for creating or updating entire apps or workflow sets."),
    ("bifrost_app_dependencies_get", "Get App Dependencies", "Get npm dependencies declared for an app."),
    ("bifrost_app_dependencies_update", "Update App Dependencies", "Update npm dependencies for an app. Pass a dict of {package: version}."),
]


def register_tools(mcp: Any, get_context_fn: Any) -> None:
    """Register all apps tools with FastMCP."""
    from src.services.mcp_server.generators.fastmcp_generator import register_tool_with_context

    tool_funcs = {
        "bifrost_app_list": bifrost_app_list,
        "bifrost_app_create": bifrost_app_create,
        "bifrost_app_get": bifrost_app_get,
        "bifrost_app_update": bifrost_app_update,
        "bifrost_app_delete": bifrost_app_delete,
        "bifrost_app_publish": bifrost_app_publish,
        "bifrost_app_replace": bifrost_app_replace,
        "bifrost_app_validate": bifrost_app_validate,
        "push_files": push_files,
        "bifrost_app_dependencies_get": bifrost_app_dependencies_get,
        "bifrost_app_dependencies_update": bifrost_app_dependencies_update,
    }

    for tool_id, _name, description in TOOLS:
        register_tool_with_context(mcp, tool_funcs[tool_id], tool_id, description, get_context_fn)


__all__ = [
    "TOOLS",
    "bifrost_app_create",
    "bifrost_app_delete",
    "bifrost_app_dependencies_get",
    "bifrost_app_dependencies_update",
    "bifrost_app_get",
    "bifrost_app_list",
    "bifrost_app_publish",
    "bifrost_app_replace",
    "bifrost_app_update",
    "bifrost_app_validate",
    "get_app_schema",
    "push_files",
    "register_tools",
]
