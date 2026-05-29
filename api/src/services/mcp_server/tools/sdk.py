"""
SDK Documentation MCP Tool

Generates accurate SDK documentation dynamically from actual SDK source code.
This ensures documentation is always up-to-date and accurate.
"""

import inspect
import logging
from typing import Any

from fastmcp.tools import ToolResult

from src.services.mcp_server.tool_result import error_result, success_result

logger = logging.getLogger(__name__)


_FILE_LOCATIONS_DOCS = """## File Locations

The `files` module resolves every operation as `{location}/{scope}/{path}`,
with `scope` derived from the current execution's organization. `workspace`
is the only unscoped location.

**Special locations:**

| Location | Usage | Example |
|----------|-------|---------|
| `"workspace"` (default) | Platform codebase, shared across orgs | `files.read("data/report.csv")` |
| `"uploads"` | Files uploaded via form file fields | `files.read(path, location="uploads")` |
| `"temp"` | Ephemeral scratch space | `files.write("scratch.txt", content, location="temp")` |

**Freeform locations** are user-defined (e.g. `"reports"`, `"exports"`) and
resolve as `{location}/{scope}/{path}`. Names must match `^[a-z0-9][a-z0-9-]*$`;
internal prefixes `_repo`, `_tmp`, and `_apps` are blocked.

```python
await files.write_bytes("q1.pdf", pdf, location="reports")
# stored at reports/{your_org_id}/q1.pdf
```

### Form file upload pattern

When a form has a `file` field, the workflow parameter receives a path that is
relative to the `uploads` location (e.g. `{form_id}/{uuid}/{filename}`). Pass it
straight to `files.read(..., location="uploads")` — the SDK adds the scope.

```python
from bifrost import workflow, files

@workflow
async def handle_upload(resume: str, cover_letters: list[str]) -> dict:
    resume_bytes = await files.read(resume, location="uploads")
    letters = []
    for path in cover_letters:
        letters.append(await files.read(path, location="uploads"))
    return {"resume_size": len(resume_bytes), "letter_count": len(letters)}
```

### Signed URLs for direct browser access

`files.get_signed_url(path, location=..., method="GET"|"PUT")` produces a
presigned S3 URL targeting the same key as `files.read`/`files.write` for the
same `(location, path)`. Useful for generating PDFs, CSVs, or other files in a
workflow and handing the browser a direct download URL.

```python
await files.write_bytes("report.pdf", pdf_bytes, location="workspace")
signed = await files.get_signed_url(
    "report.pdf", method="GET", location="workspace",
    content_type="application/pdf",
)
return {"download_url": signed["url"]}
```
"""


def _extract_class_methods(cls: type) -> list[dict[str, Any]]:
    """Extract method signatures and docstrings from a class."""
    methods = []
    for name, method in inspect.getmembers(cls, predicate=inspect.isfunction):
        if name.startswith("_"):
            continue
        sig = inspect.signature(method)
        # Skip 'self' parameter for instance methods
        params = []
        for param_name, param in sig.parameters.items():
            if param_name in ("self", "cls"):
                continue
            param_info = {"name": param_name}
            if param.annotation != inspect.Parameter.empty:
                param_info["type"] = _format_annotation(param.annotation)
            if param.default != inspect.Parameter.empty:
                param_info["default"] = repr(param.default)
            params.append(param_info)

        return_type = None
        if sig.return_annotation != inspect.Signature.empty:
            return_type = _format_annotation(sig.return_annotation)

        docstring = method.__doc__ or ""
        # Extract first line as summary
        summary = docstring.split("\n")[0].strip() if docstring else ""

        methods.append({
            "name": name,
            "params": params,
            "return_type": return_type,
            "summary": summary,
            "docstring": docstring,
        })
    return methods


def _format_annotation(annotation: Any) -> str:
    """Format a type annotation as a string."""
    if annotation is None:
        return "None"
    if hasattr(annotation, "__name__"):
        return annotation.__name__
    if hasattr(annotation, "__origin__"):
        # Handle generic types like list[str], dict[str, Any]
        origin = getattr(annotation, "__origin__", None)
        args = getattr(annotation, "__args__", ())
        if origin is not None:
            origin_name = getattr(origin, "__name__", str(origin))
            if args:
                args_str = ", ".join(_format_annotation(a) for a in args)
                return f"{origin_name}[{args_str}]"
            return origin_name
    return str(annotation)


def _generate_module_docs(module_name: str, cls: type) -> str:
    """Generate markdown documentation for an SDK module."""
    methods = _extract_class_methods(cls)
    if not methods:
        return ""

    lines = [f"### {module_name}", ""]

    # Get class docstring
    if cls.__doc__:
        summary = cls.__doc__.split("\n")[0].strip()
        lines.append(summary)
        lines.append("")

    for method in methods:
        # Method signature
        params_str = ", ".join(
            f"{p['name']}: {p.get('type', 'Any')}"
            + (f" = {p['default']}" if "default" in p else "")
            for p in method["params"]
        )
        return_str = f" -> {method['return_type']}" if method["return_type"] else ""
        lines.append(f"**`{module_name}.{method['name']}({params_str}){return_str}`**")

        if method["summary"]:
            lines.append(f"  {method['summary']}")
        lines.append("")

    return "\n".join(lines)


def _get_decorator_desc(func: Any) -> str:
    """Extract first line of description from a decorator's docstring."""
    if not func.__doc__:
        return ""
    return func.__doc__.split("Args:")[0].strip().split("\n")[0]


def _generate_decorator_docs() -> str:
    """Generate documentation for SDK decorators from actual source."""
    try:
        from bifrost.decorators import workflow, tool, data_provider

        # Build @workflow params dynamically from signature
        sig = inspect.signature(workflow)
        param_lines = []
        for name, param in sig.parameters.items():
            if name == "_func":
                continue
            p = f"    {name}"
            if param.annotation != inspect.Parameter.empty:
                p += f": {_format_annotation(param.annotation)}"
            if param.default != inspect.Parameter.empty:
                default = param.default
                if default is None:
                    p += " = None"
                elif isinstance(default, str):
                    p += f' = "{default}"'
                else:
                    p += f" = {default}"
            param_lines.append(f"{p},")
        workflow_params = "\n".join(param_lines)

        return f"""\
## Decorators

### @workflow

{_get_decorator_desc(workflow)}

```python
from bifrost import workflow

@workflow(
{workflow_params}
)
async def my_workflow(param1: str, param2: int = 10) -> dict:
    \"\"\"Workflow description.\"\"\"
    return {{"result": "success"}}
```

### @tool

{_get_decorator_desc(tool)}

```python
from bifrost import tool

@tool(description="Search for users")
async def search_users(query: str) -> list[dict]:
    \"\"\"Search for users.\"\"\"
    return []
```

### @data_provider

{_get_decorator_desc(data_provider)}

```python
from bifrost import data_provider

@data_provider(
    name="Customer List",
    description="Returns customers for dropdown",
    cache_ttl_seconds=300,
)
async def get_customers() -> list[dict]:
    \"\"\"Get customers for dropdown.\"\"\"
    return [
        {{"label": "Acme Corp", "value": "acme-123"}},
    ]
```
"""
    except ImportError as e:
        logger.warning(f"Could not import decorators: {e}")
        return ""


def _generate_error_docs() -> str:
    """Generate documentation for SDK error classes."""
    try:
        # Import to verify they exist (used in docstring examples)
        import bifrost
        _ = (bifrost.UserError, bifrost.WorkflowError, bifrost.ValidationError,
             bifrost.IntegrationError, bifrost.ConfigurationError)

        lines = ["## Error Classes", ""]
        lines.append("Use these errors to signal different failure modes in workflows:")
        lines.append("")
        lines.append("```python")
        lines.append("from bifrost import UserError, WorkflowError, ValidationError")
        lines.append("")
        lines.append("# User-facing error (shown to user)")
        lines.append('raise UserError("Invalid email format")')
        lines.append("")
        lines.append("# Workflow execution error")
        lines.append('raise WorkflowError("Failed to process request")')
        lines.append("")
        lines.append("# Validation error")
        lines.append('raise ValidationError("Missing required field: name")')
        lines.append("")
        lines.append("# Integration error (external service failed)")
        lines.append('raise IntegrationError("HaloPSA API returned 500")')
        lines.append("")
        lines.append("# Configuration error")
        lines.append('raise ConfigurationError("Missing API_KEY configuration")')
        lines.append("```")
        lines.append("")

        return "\n".join(lines)
    except ImportError:
        return ""


def _generate_context_docs() -> str:
    """Generate documentation for execution context."""
    lines = ["## Execution Context", ""]
    lines.append("Access execution context within workflows without parameters:")
    lines.append("")
    lines.append("```python")
    lines.append("from bifrost import context")
    lines.append("")
    lines.append("@workflow")
    lines.append("async def my_workflow() -> dict:")
    lines.append("    # Access caller information")
    lines.append("    user_id = context.user_id")
    lines.append("    org_id = context.org_id")
    lines.append("    execution_id = context.execution_id")
    lines.append("    ")
    lines.append("    # Access form inputs (if triggered from form)")
    lines.append("    form_data = context.form_inputs")
    lines.append("    ")
    lines.append("    return {\"user\": user_id}")
    lines.append("```")
    lines.append("")
    lines.append("### set_scope(org_id)")
    lines.append("")
    lines.append("Override the effective organization scope for all subsequent SDK calls.")
    lines.append("Only provider organizations can target a different org's scope.")
    lines.append("")
    lines.append("```python")
    lines.append("# Set scope to a managed org (provider orgs only)")
    lines.append("context.set_scope(target_org_id)")
    lines.append("integration = await integrations.get(\"Microsoft Graph\")  # uses target org")
    lines.append("")
    lines.append("# Reset to original scope")
    lines.append("context.set_scope(None)")
    lines.append("```")
    lines.append("")
    return "\n".join(lines)


def _generate_models_docs() -> str:
    """Generate documentation for SDK models."""
    try:
        # Import module to verify models exist
        import bifrost.models as _  # noqa: F401

        lines = ["## SDK Models", ""]
        lines.append("Common return types from SDK methods:")
        lines.append("")
        lines.append("| Model | Description |")
        lines.append("|-------|-------------|")
        lines.append("| `AIResponse` | AI completion response (content, tokens, model) |")
        lines.append("| `AIStreamChunk` | Streaming AI chunk (content, done, tokens) |")
        lines.append("| `ConfigData` | Configuration data with dot-notation access |")
        lines.append("| `DocumentData` | Table document with data and metadata |")
        lines.append("| `DocumentList` | Query result with documents and pagination |")
        lines.append("| `IntegrationData` | Integration config with OAuth credentials |")
        lines.append("| `KnowledgeDocument` | Knowledge base document with score |")
        lines.append("| `NamespaceInfo` | Knowledge namespace with document counts |")
        lines.append("| `OAuthCredentials` | OAuth tokens and connection details |")
        lines.append("| `TableInfo` | Table metadata (id, name, schema) |")
        lines.append("| `BatchResult` | Batch insert/upsert result (documents, count) |")
        lines.append("| `BatchDeleteResult` | Batch delete result (deleted_ids, count) |")
        lines.append("")

        return "\n".join(lines)
    except ImportError:
        return ""


async def get_sdk_schema(context: Any) -> ToolResult:  # noqa: ARG001
    """Get SDK documentation generated from actual SDK source code."""
    try:
        # Import SDK modules
        from bifrost import (
            ai,
            config,
            executions,
            files,
            forms,
            integrations,
            knowledge,
            organizations,
            roles,
            tables,
            users,
            workflows,
        )

        lines = ["# Bifrost SDK Documentation", ""]
        lines.append("All SDK methods are async and must be awaited.")
        lines.append("")
        lines.append("```python")
        lines.append("from bifrost import ai, config, files, integrations, knowledge, tables")
        lines.append("from bifrost import workflow, data_provider, context")
        lines.append("from bifrost import UserError, WorkflowError, ValidationError")
        lines.append("```")
        lines.append("")

        # Generate decorator docs
        lines.append(_generate_decorator_docs())

        # Generate context docs
        lines.append(_generate_context_docs())

        # Generate error docs
        lines.append(_generate_error_docs())

        # Generate module documentation
        lines.append("## SDK Modules")
        lines.append("")

        modules = [
            ("ai", ai),
            ("config", config),
            ("executions", executions),
            ("files", files),
            ("forms", forms),
            ("integrations", integrations),
            ("knowledge", knowledge),
            ("organizations", organizations),
            ("roles", roles),
            ("tables", tables),
            ("users", users),
            ("workflows", workflows),
        ]

        for name, module in modules:
            doc = _generate_module_docs(name, module)
            if doc:
                lines.append(doc)

        lines.append(_FILE_LOCATIONS_DOCS)

        # Generate models docs
        lines.append(_generate_models_docs())

        schema_doc = "\n".join(lines)
        return success_result("Bifrost SDK documentation", {"schema": schema_doc})

    except ImportError as e:
        logger.exception(f"Error importing SDK modules: {e}")
        return error_result(f"Error generating SDK documentation: {e}")


# Tool metadata for registration
TOOLS: list[tuple[str, str, str]] = []


def register_tools(mcp: Any, get_context_fn: Any) -> None:
    """Register all SDK tools with FastMCP."""
    from src.services.mcp_server.generators.fastmcp_generator import register_tool_with_context

    tool_funcs: dict[str, Any] = {}

    for tool_id, name, description in TOOLS:
        register_tool_with_context(mcp, tool_funcs[tool_id], tool_id, description, get_context_fn)
