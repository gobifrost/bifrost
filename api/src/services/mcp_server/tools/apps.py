"""
App Builder MCP Tools - Application Level

Tools for listing, creating, getting, updating, publishing apps,
plus schema documentation.

Applications use code-based files (TSX/TypeScript) stored in file_index table.

Note: `get_app_schema` provides a concise platform overview and component index.
"""

import logging
from datetime import datetime, timezone
from typing import Any

from fastmcp.tools import ToolResult

from src.core.pubsub import publish_app_draft_update, publish_app_published
from src.services.mcp_server.tool_result import error_result, success_result
from src.services.mcp_server.tools._http_bridge import call_rest
from src.services.mcp_server.tools.db import get_tool_db

logger = logging.getLogger(__name__)


async def list_apps(context: Any) -> ToolResult:
    """List all applications with file summaries."""
    from sqlalchemy import select

    from src.models.orm.applications import Application
    from src.services.app_storage import AppStorageService

    logger.info("MCP list_apps called")

    try:
        async with get_tool_db(context) as db:
            query = select(Application)

            if not context.is_platform_admin and context.org_id:
                query = query.where(
                    (Application.organization_id == context.org_id)
                    | (Application.organization_id.is_(None))
                )

            result = await db.execute(query.order_by(Application.name))
            apps = result.scalars().all()

            apps_data = []
            for app in apps:
                app_storage = AppStorageService()
                preview_files = await app_storage.list_files(str(app.id), "preview")
                count = len(preview_files)

                apps_data.append({
                    "id": str(app.id),
                    "name": app.name,
                    "slug": app.slug,
                    "description": app.description,
                    "status": "published" if app.is_published else "draft",
                    "is_published": app.is_published,
                    "has_unpublished_changes": app.has_unpublished_changes,
                    "file_count": count,
                    "url": f"/apps/{app.slug}",
                })

            display_text = f"Found {len(apps_data)} application(s)"
            return success_result(display_text, {"apps": apps_data, "count": len(apps_data)})

    except Exception as e:
        logger.exception(f"Error listing apps via MCP: {e}")
        return error_result(f"Error listing apps: {str(e)}")


async def create_app(
    context: Any,
    name: str,
    description: str | None = None,
    slug: str | None = None,
    scope: str = "organization",
    organization_id: str | None = None,
) -> ToolResult:
    """
    Create a new application with scaffold files.

    Args:
        name: Application name (required)
        description: Application description
        slug: URL slug (auto-generated from name if not provided)
        scope: 'global' (visible to all orgs) or 'organization' (default)
        organization_id: Override context.org_id when scope='organization'

    Returns:
        ToolResult with app details
    """
    import re
    from uuid import UUID, uuid4

    from sqlalchemy import select

    from src.models.orm.applications import Application
    from src.services.file_storage import FileStorageService

    logger.info(f"MCP create_app called with name={name}, scope={scope}")

    if not name:
        return error_result("name is required")

    if scope not in ("global", "organization"):
        return error_result("scope must be 'global' or 'organization'")

    effective_org_id: UUID | None = None
    if scope == "global":
        effective_org_id = None
    else:
        if organization_id:
            try:
                effective_org_id = UUID(organization_id)
            except ValueError:
                return error_result(f"organization_id '{organization_id}' is not a valid UUID")
        elif context.org_id:
            effective_org_id = UUID(str(context.org_id)) if isinstance(context.org_id, str) else context.org_id
        else:
            return error_result("organization_id is required when scope='organization' and no context org_id is set")

    if not slug:
        slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")

    try:
        async with get_tool_db(context) as db:
            existing = await db.execute(
                select(Application).where(Application.slug == slug)
            )
            if existing.scalar_one_or_none():
                return error_result(f"Application with slug '{slug}' already exists")

            app = Application(
                id=uuid4(),
                name=name,
                slug=slug,
                description=description,
                organization_id=effective_org_id,
                created_by=str(context.user_id),
                repo_path=f"apps/{slug}",
            )
            db.add(app)
            await db.flush()

            # Write scaffold files via FileStorageService — but only if no
            # files already exist under apps/{slug}/. Authors who created
            # the source dir locally first should not lose their work to
            # the default Welcome scaffold.
            from src.models.orm.file_index import FileIndex

            prefix = f"apps/{slug}/"
            existing_files = await db.execute(
                select(FileIndex.path).where(FileIndex.path.startswith(prefix)).limit(1)
            )
            scaffolded = 0
            if existing_files.first() is None:
                file_storage = FileStorageService(db)

                layout_source = '''import { Outlet } from "bifrost";

export default function RootLayout() {
  return (
    <div className="min-h-screen bg-background">
      <Outlet />
    </div>
  );
}
'''
                index_source = '''export default function HomePage() {
  return (
    <div className="p-8">
      <h1 className="text-3xl font-bold mb-4">Welcome</h1>
      <p className="text-muted-foreground">
        Start building your app by editing this page or adding new files.
      </p>
    </div>
  );
}
'''
                await file_storage.write_file(
                    path=f"apps/{slug}/_layout.tsx",
                    content=layout_source.encode("utf-8"),
                    updated_by="system",
                )
                await file_storage.write_file(
                    path=f"apps/{slug}/pages/index.tsx",
                    content=index_source.encode("utf-8"),
                    updated_by="system",
                )
                scaffolded = 2

            await db.commit()

            display_text = f"Created application: {app.name}"
            return success_result(display_text, {
                "success": True,
                "id": str(app.id),
                "name": app.name,
                "slug": app.slug,
                "file_count": scaffolded,
                "url": f"/apps/{app.slug}",
            })

    except Exception as e:
        logger.exception(f"Error creating app via MCP: {e}")
        return error_result(f"Error creating app: {str(e)}")


async def get_app(
    context: Any,
    app_id: str | None = None,
    app_slug: str | None = None,
) -> ToolResult:
    """
    Get application metadata and file list.

    Returns app info and a summary of files.
    """
    from uuid import UUID

    from sqlalchemy import select

    from src.models.orm.applications import Application
    from src.services.app_storage import AppStorageService

    logger.info(f"MCP get_app called with id={app_id}, slug={app_slug}")

    if not app_id and not app_slug:
        return error_result("Either app_id or app_slug is required")

    try:
        async with get_tool_db(context) as db:
            query = select(Application)

            if app_id:
                try:
                    query = query.where(Application.id == UUID(app_id))
                except ValueError:
                    return error_result(f"Invalid app_id format: {app_id}")
            else:
                query = query.where(Application.slug == app_slug)

            if not context.is_platform_admin and context.org_id:
                query = query.where(
                    (Application.organization_id == context.org_id)
                    | (Application.organization_id.is_(None))
                )

            result = await db.execute(query)
            app = result.scalar_one_or_none()

            if not app:
                return error_result(f"Application not found: {app_id or app_slug}")

            # List files from S3 preview
            app_storage = AppStorageService()
            preview_files = await app_storage.list_files(str(app.id), "preview")

            app_data = {
                "id": str(app.id),
                "name": app.name,
                "slug": app.slug,
                "description": app.description,
                "is_published": app.is_published,
                "has_unpublished_changes": app.has_unpublished_changes,
                "url": f"/apps/{app.slug}",
                "files": [
                    {"path": p}
                    for p in sorted(preview_files)
                ],
            }

            display_text = f"Application: {app.name}"
            return success_result(display_text, app_data)

    except Exception as e:
        logger.exception(f"Error getting app via MCP: {e}")
        return error_result(f"Error getting app: {str(e)}")


async def update_app(
    context: Any,
    app_id: str,
    name: str | None = None,
    description: str | None = None,
) -> ToolResult:
    """
    Update application metadata.

    Args:
        app_id: Application UUID (required)
        name: New application name
        description: New description

    Returns:
        ToolResult with updated fields
    """
    from uuid import UUID

    from sqlalchemy import select

    from src.models.orm.applications import Application

    logger.info(f"MCP update_app called with id={app_id}")

    try:
        app_uuid = UUID(app_id)
    except ValueError:
        return error_result(f"Invalid app_id format: {app_id}")

    try:
        async with get_tool_db(context) as db:
            query = select(Application).where(Application.id == app_uuid)

            if not context.is_platform_admin and context.org_id:
                query = query.where(
                    (Application.organization_id == context.org_id)
                    | (Application.organization_id.is_(None))
                )

            result = await db.execute(query)
            app = result.scalar_one_or_none()

            if not app:
                return error_result(f"Application not found: {app_id}")

            updates_made = []

            if name is not None:
                app.name = name
                updates_made.append("name")

            if description is not None:
                app.description = description
                updates_made.append("description")

            if not updates_made:
                return error_result("No updates specified")

            await db.commit()

            await publish_app_draft_update(
                app_id=app_id,
                user_id=str(context.user_id),
                user_name=context.user_name or context.user_email or "Unknown",
                entity_type="app",
                entity_id=app_id,
            )

            display_text = f"Updated application: {app.name} ({', '.join(updates_made)})"
            return success_result(display_text, {
                "success": True,
                "id": str(app.id),
                "name": app.name,
                "updates": updates_made,
            })

    except Exception as e:
        logger.exception(f"Error updating app via MCP: {e}")
        return error_result(f"Error updating app: {str(e)}")


async def publish_app(context: Any, app_id: str) -> ToolResult:
    """Publish all draft files to live.

    Builds a published_snapshot from current file_index entries and
    sets it on the application.
    """
    from uuid import UUID

    from sqlalchemy import select

    from src.models.orm.applications import Application
    from src.services.app_storage import AppStorageService

    logger.info(f"MCP publish_app called with id={app_id}")

    try:
        app_uuid = UUID(app_id)
    except ValueError:
        return error_result(f"Invalid app_id format: {app_id}")

    try:
        async with get_tool_db(context) as db:
            query = select(Application).where(Application.id == app_uuid)

            if not context.is_platform_admin and context.org_id:
                query = query.where(Application.organization_id == context.org_id)

            result = await db.execute(query)
            app = result.scalar_one_or_none()

            if not app:
                return error_result(f"Application not found: {app_id}")

            # Publish via AppStorageService: copy preview → live in S3
            app_storage = AppStorageService()
            published_count = await app_storage.publish(str(app.id))

            if published_count == 0:
                return error_result("No files found to publish")

            # Update app metadata
            preview_files = await app_storage.list_files(str(app.id), "preview")
            app.published_snapshot = {f: "" for f in preview_files}
            app.published_at = datetime.now(timezone.utc)

            await db.commit()

            await publish_app_published(
                app_id=app_id,
                user_id=str(context.user_id),
                user_name=context.user_name or context.user_email or "Unknown",
                new_version_id=app_id,
            )

            display_text = f"Published application: {app.name} ({published_count} files)"
            return success_result(display_text, {
                "success": True,
                "id": str(app.id),
                "name": app.name,
                "is_published": True,
                "files_published": published_count,
            })

    except Exception as e:
        logger.exception(f"Error publishing app via MCP: {e}")
        return error_result(f"Error publishing app: {str(e)}")


async def replace_app(
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
        return error_result(
            f"replace_app failed: HTTP {status_code}", {"body": resp}
        )
    return success_result(
        f"Repointed application {app_id} to {repo_path}",
        resp if isinstance(resp, dict) else {"body": resp},
    )


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

**App Level**: `list_apps`, `get_app`, `update_app`, `publish_app`
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
- Use `get_app_dependencies` / `update_app_dependencies` tools
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

**CRITICAL: Always use workflow UUIDs, not names.** Get IDs via `list_workflows` first.

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


async def validate_app(context: Any, app_id: str) -> ToolResult:
    """
    Validate application files for common issues.

    Compiles all files and runs static analysis checking for:
    - Compilation errors (syntax, JSX, TypeScript)
    - Missing dependencies (imported but not declared)
    - Unused dependencies (declared but not imported)
    - Unknown components
    - Invalid workflow IDs
    - Missing required files (_layout.tsx)

    Args:
        app_id: Application UUID (required)
    """
    import re
    from uuid import UUID

    from sqlalchemy import select

    from src.models.orm.applications import Application
    from src.models.orm.file_index import FileIndex
    from src.models.orm.workflows import Workflow
    from src.routers.applications import extract_external_deps

    logger.info(f"MCP validate_app called with id={app_id}")

    try:
        app_uuid = UUID(app_id)
    except ValueError:
        return error_result(f"Invalid app_id format: {app_id}")

    try:
        async with get_tool_db(context) as db:
            # Get the app
            result = await db.execute(
                select(Application).where(Application.id == app_uuid)
            )
            app = result.scalar_one_or_none()
            if not app:
                return error_result(f"Application not found: {app_id}")

            prefix = app.repo_prefix

            # Get all app files
            fi_result = await db.execute(
                select(FileIndex.path, FileIndex.content).where(
                    FileIndex.path.startswith(prefix)
                )
            )
            files = {row.path: row.content or "" for row in fi_result.all()}

            errors: list[dict] = []
            warnings: list[dict] = []

            # Check required structure
            if f"{prefix}_layout.tsx" not in files:
                errors.append({"severity": "error", "file": "_layout.tsx", "message": "Missing required _layout.tsx"})
            if f"{prefix}pages/index.tsx" not in files:
                warnings.append({"severity": "warning", "file": "pages/index.tsx", "message": "Missing pages/index.tsx"})

            # Get declared dependencies from DB
            declared_deps = app.dependencies or {}
            referenced_deps: set[str] = set()

            # Collect TSX/TS files for compilation
            compilable_files = []
            for full_path, content in files.items():
                rel_path = full_path[len(prefix):]
                if not (rel_path.endswith(".tsx") or rel_path.endswith(".ts")):
                    continue
                compilable_files.append({"path": rel_path, "source": content, "full_path": full_path})

            # Compile all files via the server-side compiler
            if compilable_files:
                from src.services.app_compiler import AppCompilerService
                compiler = AppCompilerService()
                compile_inputs = [{"path": f["path"], "source": f["source"]} for f in compilable_files]
                compile_results = await compiler.compile_batch(compile_inputs)

                for comp_file, comp_result in zip(compilable_files, compile_results):
                    rel_path = comp_file["path"]
                    content = comp_file["source"]

                    if not comp_result.success:
                        errors.append({"severity": "error", "file": rel_path, "message": f"Compilation failed: {comp_result.error}"})

                    # Check for missing default export in pages and components
                    if comp_result.success and comp_result.default_export is None:
                        if rel_path.startswith("pages/") or rel_path.startswith("components/"):
                            errors.append({"severity": "error", "file": rel_path, "message": "Missing default export. Pages and components must have a default export (e.g., export default function MyComponent() { ... })"})

                    # Check _layout.tsx uses <Outlet /> not {children}
                    if rel_path == "_layout.tsx":
                        if "{children}" in content and "Outlet" not in content:
                            errors.append({"severity": "error", "file": rel_path, "message": "Layout uses {children} but should use <Outlet /> for page routing. Replace {children} with <Outlet />."})

                    # Handle scoped packages: @scope/pkg → @scope/pkg
                    referenced_deps |= extract_external_deps(content)

                    # Check workflow IDs
                    wf_refs = re.findall(r'(?:useWorkflowQuery|useWorkflowMutation)\s*\(\s*["\']([^"\']+)["\']', content)
                    for wf_ref in wf_refs:
                        try:
                            wf_uuid = UUID(wf_ref)
                            wf_result = await db.execute(
                                select(Workflow.id).where(Workflow.id == wf_uuid, Workflow.is_active == True)  # noqa: E712
                            )
                            if not wf_result.scalar_one_or_none():
                                errors.append({"severity": "error", "file": rel_path, "message": f"Workflow '{wf_ref}' not found"})
                        except ValueError:
                            errors.append({"severity": "error", "file": rel_path, "message": f"'{wf_ref}' is not a valid UUID"})

            # Check for missing/unused dependencies. Host-provided modules
            # (DEFAULT_EXTERNALS — react, lucide-react, sonner, etc.) are
            # resolved by the host import map and never need to appear in
            # `app.dependencies`.
            from src.services.app_bundler import DEFAULT_EXTERNALS

            host_provided = set(DEFAULT_EXTERNALS)
            user_referenced = referenced_deps - host_provided
            for dep in user_referenced:
                if dep not in declared_deps:
                    errors.append({"severity": "error", "file": "dependencies", "message": f"Missing dependency: '{dep}' is imported but not declared in app dependencies"})
            for dep in declared_deps:
                if dep not in user_referenced:
                    warnings.append({"severity": "warning", "file": "dependencies", "message": f"Unused dependency: '{dep}' is declared but not imported by any file"})

            # Build result
            lines = []
            if errors:
                lines.append(f"Found {len(errors)} error(s):")
                for e in errors:
                    line_info = f" (line {e['line']})" if e.get('line') else ""
                    lines.append(f"  ✗ [{e['file']}{line_info}] {e['message']}")
            if warnings:
                lines.append(f"\nFound {len(warnings)} warning(s):")
                for w in warnings:
                    lines.append(f"  ⚠ [{w['file']}] {w['message']}")
            if not errors and not warnings:
                lines.append("✓ No issues found")

            display_text = "\n".join(lines)
            return success_result(display_text, {
                "valid": len(errors) == 0,
                "errors": errors,
                "warnings": warnings,
                "app_name": app.name,
            })

    except Exception as e:
        logger.exception(f"Error validating app: {e}")
        return error_result(f"Error validating app: {str(e)}")


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

    from src.models.orm.file_index import FileIndex
    from src.services.app_storage import AppStorageService
    from src.services.file_storage import FileStorageService

    logger.info(f"MCP push_files called with {len(files)} file(s)")

    try:
        async with get_tool_db(context) as db:
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
                prefix = delete_missing_prefix
                if not prefix.endswith("/"):
                    prefix += "/"
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

            # Load all apps to match pushed files against their repo_path
            from src.models.orm.applications import Application
            all_apps_result = await db.execute(select(Application))
            all_apps = all_apps_result.scalars().all()

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


async def get_app_dependencies(
    context: Any,
    app_id: str | None = None,
    app_slug: str | None = None,
) -> ToolResult:
    """
    Get npm dependencies declared for an app.

    Args:
        app_id: Application UUID
        app_slug: Application slug (alternative to app_id)

    Returns:
        ToolResult with dependencies dict {package_name: version}
    """
    from uuid import UUID

    from sqlalchemy import select

    from src.models.orm.applications import Application

    if not app_id and not app_slug:
        return error_result("Either app_id or app_slug is required")

    try:
        async with get_tool_db(context) as db:
            if app_id:
                try:
                    app_uuid = UUID(app_id)
                except ValueError:
                    return error_result(f"Invalid app_id format: {app_id}")
                query = select(Application).where(Application.id == app_uuid)
            else:
                query = select(Application).where(Application.slug == app_slug)

            if not context.is_platform_admin and context.org_id:
                query = query.where(
                    (Application.organization_id == context.org_id)
                    | (Application.organization_id.is_(None))
                )

            result = await db.execute(query)
            app = result.scalar_one_or_none()
            if not app:
                return error_result(f"Application not found: {app_id or app_slug}")

            deps = app.dependencies or {}

            if not deps:
                return success_result(
                    f"No dependencies declared for {app.name}",
                    {"dependencies": {}, "app_id": str(app.id), "app_name": app.name},
                )

            dep_list = ", ".join(f"{k}@{v}" for k, v in deps.items())
            return success_result(
                f"{app.name} dependencies: {dep_list}",
                {"dependencies": deps, "app_id": str(app.id), "app_name": app.name},
            )

    except Exception as e:
        logger.exception(f"Error getting app dependencies: {e}")
        return error_result(f"Error getting dependencies: {str(e)}")


async def update_app_dependencies(
    context: Any,
    app_id: str,
    dependencies: dict[str, str],
) -> ToolResult:
    """
    Update npm dependencies for an app.

    Args:
        app_id: Application UUID (required)
        dependencies: Dict of {package_name: version}. Pass empty dict to remove all.
            Package names: lowercase, hyphens, optional @scope/ prefix.
            Versions: semver with optional ^ or ~ (e.g., "2.12", "^1.5.3").
            Max 20 packages.

    Returns:
        ToolResult with updated dependencies
    """
    import re
    from uuid import UUID

    from sqlalchemy import select

    from src.models.orm.applications import Application
    from src.services.app_storage import AppStorageService

    MAX_DEPS = 20
    PKG_NAME_RE = re.compile(r"^(@[a-z0-9-]+/)?[a-z0-9][a-z0-9._-]*$")
    VERSION_RE = re.compile(r"^\^?~?\d+(\.\d+){0,2}$")

    try:
        app_uuid = UUID(app_id)
    except ValueError:
        return error_result(f"Invalid app_id format: {app_id}")

    if len(dependencies) > MAX_DEPS:
        return error_result(f"Too many dependencies (max {MAX_DEPS})")

    for name, version in dependencies.items():
        if not PKG_NAME_RE.match(name):
            return error_result(f"Invalid package name: {name}")
        if not VERSION_RE.match(version):
            return error_result(f"Invalid version for {name}: {version}")

    try:
        async with get_tool_db(context) as db:
            query = select(Application).where(Application.id == app_uuid)
            if not context.is_platform_admin and context.org_id:
                query = query.where(
                    (Application.organization_id == context.org_id)
                    | (Application.organization_id.is_(None))
                )

            result = await db.execute(query)
            app = result.scalar_one_or_none()
            if not app:
                return error_result(f"Application not found: {app_id}")

            app.dependencies = dependencies if dependencies else None
            await db.commit()

            # Invalidate render cache
            app_storage = AppStorageService()
            await app_storage.invalidate_render_cache(str(app.id))

            if dependencies:
                dep_list = ", ".join(f"{k}@{v}" for k, v in dependencies.items())
                display_text = f"Updated {app.name} dependencies: {dep_list}"
            else:
                display_text = f"Removed all dependencies from {app.name}"

            return success_result(display_text, {
                "dependencies": dependencies,
                "app_id": str(app.id),
                "app_name": app.name,
            })

    except Exception as e:
        logger.exception(f"Error updating app dependencies: {e}")
        return error_result(f"Error updating dependencies: {str(e)}")


# Tool metadata for registration
TOOLS = [
    ("list_apps", "List Applications", "List all App Builder applications with file counts and URLs."),
    ("create_app", "Create Application", "Create a new App Builder application with scaffold files."),
    ("get_app", "Get Application", "Get application metadata and file list."),
    ("update_app", "Update Application", "Update application metadata (name, description)."),
    ("publish_app", "Publish Application", "Publish all draft files to live."),
    ("replace_app", "Replace Application Source Path", "Repoint an application's repo_path after source files have been moved/renamed."),
    ("validate_app", "Validate Application", "Build and validate an app: compiles all files, checks for missing/unused dependencies, unknown components, and bad workflow IDs."),
    ("push_files", "Push Files", "Push multiple files to _repo/ in a single batch. Useful for creating or updating entire apps or workflow sets."),
("get_app_dependencies", "Get App Dependencies", "Get npm dependencies declared for an app."),
    ("update_app_dependencies", "Update App Dependencies", "Update npm dependencies for an app. Pass a dict of {package: version}."),
]


def register_tools(mcp: Any, get_context_fn: Any) -> None:
    """Register all apps tools with FastMCP."""
    from src.services.mcp_server.generators.fastmcp_generator import register_tool_with_context

    tool_funcs = {
        "list_apps": list_apps,
        "create_app": create_app,
        "get_app": get_app,
        "update_app": update_app,
        "publish_app": publish_app,
        "replace_app": replace_app,
        "validate_app": validate_app,
        "push_files": push_files,
"get_app_dependencies": get_app_dependencies,
        "update_app_dependencies": update_app_dependencies,
    }

    for tool_id, name, description in TOOLS:
        register_tool_with_context(mcp, tool_funcs[tool_id], tool_id, description, get_context_fn)
