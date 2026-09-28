"""
Form MCP Tools — thin wrappers around the REST API.

``bifrost_form_list``, ``bifrost_form_get``, ``bifrost_form_create``,
``bifrost_form_update``, ``bifrost_form_delete``.

These tools are thin wrappers: they resolve human refs, assemble the shared
Form DTO via ``bifrost/dto_flags.py``, then call the corresponding REST
endpoint through the in-process HTTP bridge (``_http_bridge``). No ORM, no
repositories, no ``AsyncSession`` — all side effects (audit logs, cache
invalidation, role sync, reference validation) happen behind the REST
handler, matching the CLI's path exactly.
"""

from __future__ import annotations

from typing import Any

from fastmcp.tools import ToolResult

from shared.form_runtime import DEFAULT_FORM_CONFIRMATION_MARKDOWN
from src.services.mcp_server.tool_result import error_result, success_result
from src.services.mcp_server.tools._http_bridge import call_rest, rest_client

def _ref_error_payload(exc: Exception) -> dict[str, Any]:
    """Format a ref-resolution error for the ToolResult structured body."""
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


async def _resolve_ref(context: Any, kind: str, value: str) -> str:
    from bifrost.refs import RefResolver

    async with rest_client(context) as http:
        return await RefResolver(http).resolve(kind, value)  # type: ignore[arg-type]


async def _assemble_form_body(
    context: Any,
    fields: dict[str, Any],
    *,
    is_update: bool,
    scope: str | None,
) -> dict[str, Any]:
    """Assemble a shared Form DTO payload and resolve every supported ref.

    Only keys the caller actually passed are sent — omitted fields never
    appear in the assembled body (``assemble_body`` drops ``None`` values),
    which is how ``update_form`` distinguishes an omitted field (unchanged)
    from an explicit ``null`` (clears the field, e.g. ``description``) —
    see ``FormUpdate`` / ``update_form`` in ``src/routers/forms.py``. This
    thin wrapper never sends a spurious ``null`` for a field the caller
    didn't set.
    """
    from bifrost.dto_flags import assemble_body
    from bifrost.refs import RefResolver
    from src.models.contracts.forms import FormCreate, FormUpdate

    model_cls = FormUpdate if is_update else FormCreate
    async with rest_client(context) as http:
        resolver = RefResolver(http)
        body = await assemble_body(model_cls, fields, resolver=resolver)
        if scope is not None:
            if scope == "global":
                body["organization_id"] = None
            else:
                body["organization_id"] = await resolver.resolve("org", scope)
    return body


async def bifrost_form_list(context: Any) -> ToolResult:
    """List Forms visible to the caller — thin wrapper over ``GET /api/forms``."""
    status_code, body = await call_rest(context, "GET", "/api/forms")
    if status_code != 200:
        return _rest_error("List Forms", status_code, body)
    forms = body if isinstance(body, list) else []
    return success_result(
        f"Found {len(forms)} form(s)",
        {"forms": forms, "count": len(forms)},
    )


async def bifrost_form_get(context: Any, form_ref: str) -> ToolResult:
    """Get one Form by UUID or accessible name — thin wrapper over ``GET /api/forms/{uuid}``."""
    if not form_ref:
        return error_result("form_ref is required")
    try:
        form_id = await _resolve_ref(context, "form", form_ref)
    except Exception as exc:
        return error_result(f"could not resolve form {form_ref!r}", _ref_error_payload(exc))

    status_code, body = await call_rest(context, "GET", f"/api/forms/{form_id}")
    if status_code != 200:
        return _rest_error("Get Form", status_code, body)
    payload = body if isinstance(body, dict) else {"body": body}
    return success_result(f"Form: {payload.get('name', form_id)}", payload)


async def bifrost_form_create(
    context: Any,
    name: str,
    workflow_id: str,
    fields: list[dict[str, Any]],
    description: str | None = None,
    confirmation_markdown: str = DEFAULT_FORM_CONFIRMATION_MARKDOWN,
    launch_workflow_id: str | None = None,
    access_level: str | None = None,
    role_ids: list[str] | None = None,
    scope: str | None = None,
) -> ToolResult:
    """Create a Form through ``POST /api/forms`` (platform admin only).

    ``workflow_id`` / ``launch_workflow_id`` accept UUIDs or human refs.
    ``scope`` is ``global``, an organization UUID/name, or omitted for the
    caller's home organization.
    """
    body_fields = {
        "name": name,
        "description": description,
        "confirmation_markdown": confirmation_markdown,
        "workflow_id": workflow_id,
        "launch_workflow_id": launch_workflow_id,
        "form_schema": {"fields": fields} if fields is not None else None,
        "access_level": access_level or "role_based",
        "role_ids": role_ids,
    }
    try:
        body = await _assemble_form_body(context, body_fields, is_update=False, scope=scope)
    except Exception as exc:
        return error_result(f"invalid Form input: {exc}", _ref_error_payload(exc))

    status_code, resp = await call_rest(context, "POST", "/api/forms", json_body=body)
    if status_code not in (200, 201):
        return _rest_error("Create Form", status_code, resp)
    payload = resp if isinstance(resp, dict) else {"body": resp}
    return success_result(f"Created form: {payload.get('name', name)}", payload)


async def bifrost_form_update(
    context: Any,
    form_ref: str,
    name: str | None = None,
    description: str | None = None,
    confirmation_markdown: str | None = None,
    workflow_id: str | None = None,
    launch_workflow_id: str | None = None,
    fields: list[dict[str, Any]] | None = None,
    is_active: bool | None = None,
    access_level: str | None = None,
    role_ids: list[str] | None = None,
    clear_roles: bool | None = None,
    scope: str | None = None,
) -> ToolResult:
    """Update a Form through ``PATCH /api/forms/{uuid}`` (platform admin only).

    Only fields explicitly passed are sent to the server; the REST handler
    treats an explicit ``null`` (e.g. ``description=None`` passed by name)
    as "clear this field" and an omitted field as "leave unchanged" — this
    wrapper's underlying ``assemble_body`` call omits any kwarg the caller
    didn't pass, so it can never accidentally clear a field the caller
    meant to leave alone.
    """
    if not form_ref:
        return error_result("form_ref is required")
    try:
        form_id = await _resolve_ref(context, "form", form_ref)
    except Exception as exc:
        return error_result(f"could not resolve form {form_ref!r}", _ref_error_payload(exc))

    body_fields: dict[str, Any] = {
        "name": name,
        "description": description,
        "confirmation_markdown": confirmation_markdown,
        "workflow_id": workflow_id,
        "launch_workflow_id": launch_workflow_id,
        "form_schema": {"fields": fields} if fields is not None else None,
        "is_active": is_active,
        "access_level": access_level,
        "role_ids": role_ids,
        "clear_roles": clear_roles,
    }

    try:
        body = await _assemble_form_body(context, body_fields, is_update=True, scope=scope)
    except Exception as exc:
        return error_result(f"invalid Form input: {exc}", _ref_error_payload(exc))
    if not body:
        return error_result("No updates provided")

    status_code, resp = await call_rest(context, "PATCH", f"/api/forms/{form_id}", json_body=body)
    if status_code != 200:
        return _rest_error("Update Form", status_code, resp)
    payload = resp if isinstance(resp, dict) else {"body": resp}
    return success_result(f"Updated form: {payload.get('name', form_id)}", payload)


async def bifrost_form_delete(context: Any, form_ref: str, purge: bool = False) -> ToolResult:
    """Delete a Form — thin wrapper over ``DELETE /api/forms/{uuid}`` (platform admin only).

    Soft-deletes (sets ``is_active=False``) by default; ``purge=True``
    permanently removes an already-inactive form.
    """
    if not form_ref:
        return error_result("form_ref is required")
    try:
        form_id = await _resolve_ref(context, "form", form_ref)
    except Exception as exc:
        return error_result(f"could not resolve form {form_ref!r}", _ref_error_payload(exc))

    status_code, resp = await call_rest(
        context, "DELETE", f"/api/forms/{form_id}", params={"purge": purge} if purge else None
    )
    if status_code not in (200, 204):
        return _rest_error("Delete Form", status_code, resp)
    return success_result(f"Deleted form {form_id}", {"deleted": form_id})


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


TOOLS = [
    ("bifrost_form_list", "List Forms", "List all Forms visible to the caller."),
    ("bifrost_form_get", "Get Form", "Get a Form by UUID or accessible name."),
    ("bifrost_form_create", "Create Form", "Create a new Form with fields linked to a workflow."),
    ("bifrost_form_update", "Update Form", "Update an existing Form's properties or fields."),
    ("bifrost_form_delete", "Delete Form", "Delete (or purge) a Form."),
]


def register_tools(mcp: Any, get_context_fn: Any) -> None:
    """Register all Form tools with FastMCP."""
    from src.services.mcp_server.generators.fastmcp_generator import register_tool_with_context

    tool_funcs = {
        "bifrost_form_list": bifrost_form_list,
        "bifrost_form_get": bifrost_form_get,
        "bifrost_form_create": bifrost_form_create,
        "bifrost_form_update": bifrost_form_update,
        "bifrost_form_delete": bifrost_form_delete,
    }

    for tool_id, _name, description in TOOLS:
        register_tool_with_context(mcp, tool_funcs[tool_id], tool_id, description, get_context_fn)


__all__ = [
    "TOOLS",
    "bifrost_form_create",
    "bifrost_form_delete",
    "bifrost_form_get",
    "bifrost_form_list",
    "bifrost_form_update",
    "get_form_schema",
    "register_tools",
]
