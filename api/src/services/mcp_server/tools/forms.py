"""
Form MCP Tools

Tools for listing, creating, validating, and managing forms.
"""

import logging
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from fastmcp.tools import ToolResult

from src.services.mcp_server.tool_result import error_result, success_result
from src.services.mcp_server.tools._org_scope import apply_mcp_org_scope
from src.services.mcp_server.tools.db import get_tool_db

# MCPContext is imported where needed to avoid circular imports

logger = logging.getLogger(__name__)


async def list_forms(context: Any) -> ToolResult:
    """List all forms."""
    from src.repositories.forms import FormRepository

    logger.info("MCP list_forms called")

    try:
        async with get_tool_db(context) as db:
            # Determine org_id and user context based on context
            if context.is_platform_admin:
                # Platform admins see all forms (no org filtering)
                repo = FormRepository(
                    session=db,
                    org_id=None,
                    is_superuser=True,
                )
                forms = await repo.list_all_in_scope(active_only=True)
            elif context.org_id:
                # Org users see their org's forms + global forms
                org_id = UUID(str(context.org_id)) if isinstance(context.org_id, str) else context.org_id
                user_id = UUID(str(context.user_id)) if context.user_id else None
                repo = FormRepository(
                    session=db,
                    org_id=org_id,
                    user_id=user_id,
                    is_superuser=False,
                )
                forms = await repo.list_forms(active_only=True)
            else:
                # No org context - only global forms
                repo = FormRepository(
                    session=db,
                    org_id=None,
                    user_id=None,
                    is_superuser=False,
                )
                forms = await repo.list_forms(active_only=True)

            forms_data = [
                {
                    "id": str(form.id),
                    "name": form.name,
                    "description": form.description,
                    "workflow_id": str(form.workflow_id) if form.workflow_id else None,
                    "url": f"/forms/{form.id}",
                }
                for form in forms
            ]

            display_text = f"Found {len(forms_data)} form(s)"
            return success_result(display_text, {"forms": forms_data, "count": len(forms_data)})

    except Exception as e:
        logger.exception(f"Error listing forms via MCP: {e}")
        return error_result(f"Error listing forms: {str(e)}")


async def get_form_schema(context: Any) -> ToolResult:
    """Get form schema documentation generated from Pydantic models."""
    from src.models.contracts.forms import FormCreate, FormUpdate, FormField, FormSchema, DataProviderInputConfig
    from src.services.mcp_server.schema_utils import models_to_markdown

    schema_doc = models_to_markdown([
        (FormCreate, "FormCreate (for creating forms)"),
        (FormUpdate, "FormUpdate (for updating forms)"),
        (FormSchema, "FormSchema (fields container)"),
        (FormField, "FormField (field definition)"),
        (DataProviderInputConfig, "DataProviderInputConfig (for cascading dropdowns)"),
    ], "Form Schema Documentation")

    # Data provider usage docs (previously in get_data_provider_schema)
    data_provider_docs = """
## Using Data Providers in Forms

Reference a data provider in form field definitions to create dynamic dropdowns:

```json
{
  "name": "customer",
  "type": "select",
  "label": "Select Customer",
  "data_provider_id": "uuid-of-provider",
  "data_provider_inputs": {
    "department_id": "{{department}}"
  }
}
```

Data providers must return a list of objects with label/value pairs:

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| label | string | Yes | Text shown to user in dropdown |
| value | string | Yes | Value stored when selected |
| metadata | object | No | Optional extra data for workflows |

Data providers are stored as workflows with type='data_provider'.
Use `list_workflows` to see available data providers. For `@data_provider` decorator docs, use `get_sdk_schema`.
"""

    file_upload_docs = """
## File Upload Fields

Use `type: "file"` to accept file uploads in forms. Uploaded files are stored in S3 under `uploads/{scope}/{form_id}/{uuid}/{filename}`. The workflow receives the **location-relative** path — pass it straight to `files.read(..., location="uploads")` and the SDK adds `uploads/{scope}/`.

### What the workflow receives

| Scenario | Parameter value |
|----------|----------------|
| Single file (`multiple: false`) | `"abc123/uuid/report.pdf"` (string, relative to `uploads/`) |
| Multiple files (`multiple: true`) | `["abc123/uuid/a.pdf", "abc123/uuid/b.pdf"]` (list of strings) |

### Reading uploaded files in a workflow

```python
from bifrost import workflow, files

@workflow
async def process_upload(document: str) -> dict:
    # SDK resolves to uploads/{your_scope}/{document}
    content = await files.read(document, location="uploads")
    return {"size": len(content)}
```

### File field options

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| allowed_types | list[str] | [] (any) | MIME types or extensions, e.g. `[".pdf", "image/*"]` |
| multiple | bool | false | Allow selecting more than one file |
| max_size_mb | number | 10 | Maximum file size in megabytes |

### Example form field definition

```json
{
  "name": "attachments",
  "type": "file",
  "label": "Upload Documents",
  "required": true,
  "options": {
    "allowed_types": [".pdf", ".docx", "image/*"],
    "multiple": true,
    "max_size_mb": 25
  }
}
```
"""

    schema_doc += data_provider_docs
    schema_doc += file_upload_docs
    return success_result("Form schema documentation", {"schema": schema_doc})


async def create_form(
    context: Any,
    name: str,
    workflow_id: str,
    fields: list[dict[str, Any]],
    description: str | None = None,
    launch_workflow_id: str | None = None,
    scope: str = "organization",
    organization_id: str | None = None,
) -> ToolResult:
    """Create a new form with fields linked to a workflow.

    Args:
        context: MCP context with user permissions
        name: Form name (1-200 chars)
        workflow_id: UUID of workflow to execute on form submit
        fields: Array of field definitions
        description: Optional form description
        launch_workflow_id: Optional UUID of workflow to run before form display
        scope: 'global' (visible to all orgs) or 'organization' (default)
        organization_id: Override context.org_id when scope='organization'

    Returns:
        ToolResult with form details
    """
    from uuid import UUID as UUID_TYPE

    from sqlalchemy import select
    from sqlalchemy.orm import selectinload

    from src.models import Form as FormORM
    from src.models import FormSchema
    from src.repositories.workflows import WorkflowRepository
    from src.routers.forms import _form_schema_to_fields

    logger.info(f"MCP create_form called: name={name}, workflow_id={workflow_id}, scope={scope}")

    # Validate inputs
    if not name:
        return error_result("name is required")
    if not workflow_id:
        return error_result("workflow_id is required")
    if not fields:
        return error_result("fields array is required")
    if len(name) > 200:
        return error_result("name must be 200 characters or less")

    # Validate scope parameter
    if scope not in ("global", "organization"):
        return error_result("scope must be 'global' or 'organization'")

    # Determine effective organization_id based on scope
    effective_org_id: UUID_TYPE | None = None
    if scope == "global":
        # Global resources have no organization_id
        effective_org_id = None
    else:
        # Organization scope: use provided organization_id or fall back to context.org_id
        if organization_id:
            try:
                effective_org_id = UUID_TYPE(organization_id)
            except ValueError:
                return error_result(f"organization_id '{organization_id}' is not a valid UUID")
        elif context.org_id:
            effective_org_id = UUID_TYPE(str(context.org_id)) if isinstance(context.org_id, str) else context.org_id
        else:
            return error_result("organization_id is required when scope='organization' and no context org_id is set")

    # Validate workflow_id is a valid UUID
    try:
        UUID_TYPE(workflow_id)
    except ValueError:
        return error_result(f"workflow_id '{workflow_id}' is not a valid UUID")

    # Validate launch_workflow_id if provided
    if launch_workflow_id:
        try:
            UUID_TYPE(launch_workflow_id)
        except ValueError:
            return error_result(f"launch_workflow_id '{launch_workflow_id}' is not a valid UUID")

    try:
        async with get_tool_db(context) as db:
            # Verify workflow exists with proper scoping
            ctx_org_id = UUID_TYPE(str(context.org_id)) if context.org_id else None
            ctx_user_id = UUID_TYPE(str(context.user_id)) if context.user_id else None
            workflow_repo = WorkflowRepository(
                db,
                org_id=ctx_org_id,
                user_id=ctx_user_id,
                is_superuser=context.is_platform_admin,
                is_external=context.is_external,
            )
            workflow = await workflow_repo.get(id=UUID_TYPE(workflow_id))
            if not workflow:
                return error_result(f"Workflow '{workflow_id}' not found. Use list_workflows to see available workflows.")

            # Verify launch workflow if provided
            launch_workflow = None
            if launch_workflow_id:
                launch_workflow = await workflow_repo.get(id=UUID_TYPE(launch_workflow_id))
                if not launch_workflow:
                    return error_result(f"Launch workflow '{launch_workflow_id}' not found.")

            # Validate form schema using Pydantic model
            from pydantic import ValidationError

            try:
                FormSchema.model_validate({"fields": fields})
            except ValidationError as e:
                errors_str = "; ".join(f"{'.'.join(str(x) for x in err['loc'])}: {err['msg']}" for err in e.errors())
                return error_result(f"Invalid form schema: {errors_str}")
            except Exception as e:
                return error_result(f"Error validating form schema: {str(e)}")

            # Create form record
            now = datetime.now(timezone.utc)

            form = FormORM(
                name=name,
                description=description,
                workflow_id=workflow_id,
                launch_workflow_id=launch_workflow_id,
                access_level="role_based",
                organization_id=effective_org_id,
                is_active=True,
                created_by=context.user_email,
                created_at=now,
                updated_at=now,
            )

            db.add(form)
            await db.flush()  # Get the form ID

            # Convert form_schema to FormField records
            field_records = _form_schema_to_fields({"fields": fields}, form.id)
            for field in field_records:
                db.add(field)

            await db.flush()

            # Reload form with fields eager-loaded
            result = await db.execute(
                select(FormORM)
                .options(selectinload(FormORM.fields))
                .where(FormORM.id == form.id)
            )
            form = result.scalar_one()

            logger.info(f"Created form {form.id}: {form.name}")

            display_text = f"Created form: {form.name}"
            return success_result(display_text, {
                "success": True,
                "id": str(form.id),
                "name": form.name,
                "url": f"/forms/{form.id}",
                "workflow_id": workflow_id,
                "workflow_name": workflow.name,
                "field_count": len(fields),
                "launch_workflow_id": launch_workflow_id,
                "launch_workflow_name": launch_workflow.name if launch_workflow else None,
            })

    except Exception as e:
        logger.exception(f"Error creating form via MCP: {e}")
        return error_result(f"Error creating form: {str(e)}")


async def get_form(
    context: Any,
    form_id: str | None = None,
    form_name: str | None = None,
) -> ToolResult:
    """Get detailed information about a specific form.

    Args:
        context: MCP context with user permissions
        form_id: Form UUID (preferred)
        form_name: Form name (alternative to ID)

    Returns:
        ToolResult with form details
    """
    from uuid import UUID as UUID_TYPE

    from sqlalchemy import select
    from sqlalchemy.orm import selectinload

    from src.models import Form as FormORM
    from src.repositories.workflows import WorkflowRepository

    logger.info(f"MCP get_form called: form_id={form_id}, form_name={form_name}")

    if not form_id and not form_name:
        return error_result("Either form_id or form_name is required")

    try:
        async with get_tool_db(context) as db:
            # Build query
            query = select(FormORM).options(selectinload(FormORM.fields))

            if form_id:
                # ID-based lookup: IDs are unique, so cascade filter is safe.
                try:
                    uuid_id = UUID_TYPE(form_id)
                except ValueError:
                    return error_result(f"'{form_id}' is not a valid UUID")
                query = query.where(FormORM.id == uuid_id)
                query = apply_mcp_org_scope(query, FormORM, context)
            else:
                # Name-based lookup: use prioritized lookup (org-specific > global)
                query = query.where(FormORM.name == form_name)
                query = apply_mcp_org_scope(query, FormORM, context)
                if not context.is_platform_admin and context.org_id:
                    # Prioritize org-specific over global (nulls come last)
                    query = query.order_by(
                        FormORM.organization_id.desc().nulls_last()
                    ).limit(1)

            result = await db.execute(query)
            form = result.scalar_one_or_none()

            if not form:
                identifier = form_id or form_name
                return error_result(f"Form '{identifier}' not found. Use list_forms to see available forms.")

            # Get workflow names with proper scoping
            ctx_org_id = UUID_TYPE(str(context.org_id)) if context.org_id else None
            ctx_user_id = UUID_TYPE(str(context.user_id)) if context.user_id else None
            workflow_repo = WorkflowRepository(
                db,
                org_id=ctx_org_id,
                user_id=ctx_user_id,
                is_superuser=context.is_platform_admin,
                is_external=context.is_external,
            )
            workflow_name = None
            launch_workflow_name = None

            if form.workflow_id:
                try:
                    workflow = await workflow_repo.get(id=UUID_TYPE(form.workflow_id))
                    workflow_name = workflow.name if workflow else None
                except (ValueError, AttributeError) as e:
                    # Non-UUID portable ref or workflow lookup failed — name stays None
                    logger.debug(f"could not resolve workflow {form.workflow_id!r} for form {form.id}: {e}")

            if form.launch_workflow_id:
                try:
                    launch_workflow = await workflow_repo.get(id=UUID_TYPE(form.launch_workflow_id))
                    launch_workflow_name = launch_workflow.name if launch_workflow else None
                except (ValueError, AttributeError) as e:
                    # Non-UUID portable ref or workflow lookup failed — name stays None
                    logger.debug(f"could not resolve launch workflow {form.launch_workflow_id!r} for form {form.id}: {e}")

            # Sort fields by position
            sorted_fields = sorted(form.fields, key=lambda f: f.position) if form.fields else []

            form_data = {
                "id": str(form.id),
                "name": form.name,
                "description": form.description,
                "url": f"/forms/{form.id}",
                "is_active": form.is_active,
                "access_level": form.access_level or "role_based",
                "organization_id": str(form.organization_id) if form.organization_id else None,
                "workflow_id": form.workflow_id,
                "workflow_name": workflow_name,
                "launch_workflow_id": form.launch_workflow_id,
                "launch_workflow_name": launch_workflow_name,
                "fields": [
                    {
                        "name": field.name,
                        "type": field.type,
                        "label": field.label,
                        "required": field.required,
                        "placeholder": field.placeholder,
                        "help_text": field.help_text,
                        "default_value": field.default_value,
                        "options": field.options,
                        "data_provider_id": field.data_provider_id,
                        "data_provider_inputs": field.data_provider_inputs,
                        "visibility_expression": field.visibility_expression,
                        "validation": field.validation,
                        "allowed_types": field.allowed_types,
                        "multiple": field.multiple,
                        "max_size_mb": field.max_size_mb,
                        "content": field.content,
                        "auto_fill": field.auto_fill,
                        "position": field.position,
                    }
                    for field in sorted_fields
                ],
            }

            display_text = f"Form: {form.name}"
            return success_result(display_text, form_data)

    except Exception as e:
        logger.exception(f"Error getting form via MCP: {e}")
        return error_result(f"Error getting form: {str(e)}")


async def update_form(
    context: Any,
    form_id: str,
    name: str | None = None,
    description: str | None = None,
    workflow_id: str | None = None,
    launch_workflow_id: str | None = None,
    fields: list[dict[str, Any]] | None = None,
    is_active: bool | None = None,
) -> ToolResult:
    """Update an existing form.

    Args:
        context: MCP context with user permissions
        form_id: Form UUID (required)
        name: New form name
        description: New description
        workflow_id: New workflow UUID
        launch_workflow_id: New launch workflow UUID
        fields: New field definitions (replaces all fields)
        is_active: Enable/disable the form

    Returns:
        ToolResult with update confirmation
    """
    from uuid import UUID as UUID_TYPE

    from sqlalchemy import delete, select
    from sqlalchemy.orm import selectinload

    from src.models import Form as FormORM, FormField as FormFieldORM
    from src.models import FormSchema
    from src.repositories.workflows import WorkflowRepository
    from src.routers.forms import _form_schema_to_fields

    logger.info(f"MCP update_form called: form_id={form_id}")

    if not form_id:
        return error_result("form_id is required")

    # Validate form_id is a valid UUID
    try:
        uuid_id = UUID_TYPE(form_id)
    except ValueError:
        return error_result(f"'{form_id}' is not a valid UUID")

    try:
        async with get_tool_db(context) as db:
            # Get existing form
            result = await db.execute(
                select(FormORM)
                .options(selectinload(FormORM.fields))
                .where(FormORM.id == uuid_id)
            )
            form = result.scalar_one_or_none()

            if not form:
                return error_result(f"Form '{form_id}' not found. Use list_forms to see available forms.")

            # Solution-managed forms are read-only (criterion 6). Refuse BEFORE
            # any mutation: this tool issues a Core bulk delete of FormField rows
            # that bypasses the ORM-flush backstop, and the agent executor commits
            # even after an error_result — so a late guard would leave the field
            # delete persisted (Codex #13).
            from src.services.solutions.guard import (
                SOLUTION_MANAGED_MESSAGE,
                is_solution_managed,
            )

            if is_solution_managed(form):
                return error_result(SOLUTION_MANAGED_MESSAGE)

            # Check access for non-admins
            if not context.is_platform_admin:
                if form.organization_id:
                    if context.org_id and str(form.organization_id) != str(context.org_id):
                        return error_result("You don't have permission to update this form.")
                # Global forms can only be updated by admins
                if form.organization_id is None:
                    return error_result("Only platform admins can update global forms.")

            updates_made = []

            # Apply updates
            if name is not None:
                if len(name) > 200:
                    return error_result("name must be 200 characters or less")
                form.name = name
                updates_made.append("name")

            if description is not None:
                form.description = description
                updates_made.append("description")

            if workflow_id is not None:
                try:
                    UUID_TYPE(workflow_id)
                except ValueError:
                    return error_result(f"workflow_id '{workflow_id}' is not a valid UUID")

                ctx_org_id = UUID_TYPE(str(context.org_id)) if context.org_id else None
                ctx_user_id = UUID_TYPE(str(context.user_id)) if context.user_id else None
                workflow_repo = WorkflowRepository(
                    db,
                    org_id=ctx_org_id,
                    user_id=ctx_user_id,
                    is_superuser=context.is_platform_admin,
                    is_external=context.is_external,
                )
                workflow = await workflow_repo.get(id=UUID_TYPE(workflow_id))
                if not workflow:
                    return error_result(f"Workflow '{workflow_id}' not found.")
                form.workflow_id = workflow_id
                updates_made.append("workflow_id")

            if launch_workflow_id is not None:
                if launch_workflow_id == "":
                    # Clear launch workflow
                    form.launch_workflow_id = None
                    updates_made.append("launch_workflow_id")
                else:
                    try:
                        UUID_TYPE(launch_workflow_id)
                    except ValueError:
                        return error_result(f"launch_workflow_id '{launch_workflow_id}' is not a valid UUID")

                    ctx_org_id = UUID_TYPE(str(context.org_id)) if context.org_id else None
                    ctx_user_id = UUID_TYPE(str(context.user_id)) if context.user_id else None
                    workflow_repo = WorkflowRepository(
                        db,
                        org_id=ctx_org_id,
                        user_id=ctx_user_id,
                        is_superuser=context.is_platform_admin,
                        is_external=context.is_external,
                    )
                    launch_workflow = await workflow_repo.get(id=UUID_TYPE(launch_workflow_id))
                    if not launch_workflow:
                        return error_result(f"Launch workflow '{launch_workflow_id}' not found.")
                    form.launch_workflow_id = launch_workflow_id
                    updates_made.append("launch_workflow_id")

            if is_active is not None:
                form.is_active = is_active
                updates_made.append("is_active")

            if fields is not None:
                # Validate new fields using Pydantic model
                from pydantic import ValidationError

                try:
                    FormSchema.model_validate({"fields": fields})
                except ValidationError as e:
                    errors_str = "; ".join(f"{'.'.join(str(x) for x in err['loc'])}: {err['msg']}" for err in e.errors())
                    return error_result(f"Invalid form schema: {errors_str}")
                except Exception as e:
                    return error_result(f"Error validating form schema: {str(e)}")

                # Delete existing fields
                await db.execute(
                    delete(FormFieldORM).where(FormFieldORM.form_id == form.id)
                )

                # Add new fields
                field_records = _form_schema_to_fields({"fields": fields}, form.id)
                for field in field_records:
                    db.add(field)

                updates_made.append("fields")

            if not updates_made:
                return error_result("No updates provided. Specify at least one field to update.")

            form.updated_at = datetime.now(timezone.utc)
            await db.flush()

            # Reload form with fields
            result = await db.execute(
                select(FormORM)
                .options(selectinload(FormORM.fields))
                .where(FormORM.id == form.id)
            )
            form = result.scalar_one()

            logger.info(f"Updated form {form.id}: {', '.join(updates_made)}")

            display_text = f"Updated form: {form.name} ({', '.join(updates_made)})"
            return success_result(display_text, {
                "success": True,
                "id": str(form.id),
                "name": form.name,
                "updates": updates_made,
            })

    except Exception as e:
        logger.exception(f"Error updating form via MCP: {e}")
        return error_result(f"Error updating form: {str(e)}")


# Tool metadata for registration
TOOLS = [
    ("list_forms", "List Forms", "List all forms with their URLs."),
("create_form", "Create Form", "Create a new form with fields linked to a workflow."),
    ("get_form", "Get Form", "Get detailed information about a specific form including all fields."),
    ("update_form", "Update Form", "Update an existing form's properties or fields."),
]


def register_tools(mcp: Any, get_context_fn: Any) -> None:
    """Register all forms tools with FastMCP."""
    from src.services.mcp_server.generators.fastmcp_generator import register_tool_with_context

    tool_funcs = {
        "list_forms": list_forms,
"create_form": create_form,
        "get_form": get_form,
        "update_form": update_form,
    }

    for tool_id, name, description in TOOLS:
        register_tool_with_context(mcp, tool_funcs[tool_id], tool_id, description, get_context_fn)
