"""
Table MCP Tools

Tools for listing, getting, creating, and updating tables.
Tables are flexible document stores scoped to global, organization, or application level.
"""

import logging
from typing import Any
from uuid import UUID, uuid4

from fastmcp.tools import ToolResult

from src.services.mcp_server.tool_result import error_result, success_result
from src.services.mcp_server.tools._org_scope import apply_mcp_org_scope
from src.services.mcp_server.tools.db import get_tool_db

logger = logging.getLogger(__name__)


async def list_tables(
    context: Any,
    scope: str | None = None,
) -> ToolResult:
    """List tables with org filtering for non-admins."""
    from sqlalchemy import select

    from src.models.orm.tables import Table

    logger.info(f"MCP list_tables called with scope={scope}")

    try:
        async with get_tool_db(context) as db:
            query = select(Table)

            # Org cascade: own org + global (admins unscoped).
            query = apply_mcp_org_scope(query, Table, context)

            # Apply scope filter if provided.
            if scope == "global":
                query = query.where(Table.organization_id.is_(None))
            elif scope == "organization":
                query = query.where(Table.organization_id.isnot(None))

            result = await db.execute(query.order_by(Table.name))
            tables = result.scalars().all()

            tables_data = []
            for table in tables:
                table_scope = "organization" if table.organization_id else "global"
                tables_data.append({
                    "id": str(table.id),
                    "name": table.name,
                    "description": table.description,
                    "scope": table_scope,
                    "organization_id": str(table.organization_id) if table.organization_id else None,
                    "created_at": table.created_at.isoformat() if table.created_at else None,
                })

            display_text = f"Found {len(tables_data)} table(s)"
            return success_result(display_text, {"tables": tables_data, "count": len(tables_data)})

    except Exception as e:
        logger.exception(f"Error listing tables via MCP: {e}")
        return error_result(f"Error listing tables: {str(e)}")


async def get_table(
    context: Any,
    table_id: str | None = None,
) -> ToolResult:
    """Get table details including schema."""
    from sqlalchemy import func, select

    from src.models.orm.tables import Document, Table

    logger.info(f"MCP get_table called with id={table_id}")

    if not table_id:
        return error_result("table_id is required")

    try:
        table_uuid = UUID(table_id)
    except ValueError:
        return error_result(f"Invalid table_id format: {table_id}")

    try:
        async with get_tool_db(context) as db:
            query = select(Table).where(Table.id == table_uuid)

            # Org cascade (external-aware): externals get no global tier.
            query = apply_mcp_org_scope(query, Table, context)

            result = await db.execute(query)
            table = result.scalar_one_or_none()

            if not table:
                return error_result(f"Table not found: {table_id}")

            # Get document count
            count_query = (
                select(func.count())
                .select_from(Document)
                .where(Document.table_id == table_uuid)
            )
            count_result = await db.execute(count_query)
            document_count = count_result.scalar() or 0

            table_scope = "organization" if table.organization_id else "global"

            # Extract columns from schema if available
            columns = []
            if table.schema and isinstance(table.schema, dict):
                columns = table.schema.get("columns", [])

            table_data = {
                "id": str(table.id),
                "name": table.name,
                "description": table.description,
                "scope": table_scope,
                "organization_id": str(table.organization_id) if table.organization_id else None,
                "schema": table.schema,
                "columns": columns,
                "document_count": document_count,
                "created_at": table.created_at.isoformat() if table.created_at else None,
                "updated_at": table.updated_at.isoformat() if table.updated_at else None,
                "created_by": table.created_by,
            }

            display_text = f"Table: {table.name} ({document_count} documents)"
            return success_result(display_text, table_data)

    except Exception as e:
        logger.exception(f"Error getting table via MCP: {e}")
        return error_result(f"Error getting table: {str(e)}")


async def get_table_schema(context: Any) -> ToolResult:  # noqa: ARG001
    """Return markdown documentation about table structure generated from Pydantic models."""
    from src.models.contracts.tables import TableCreate, TableUpdate
    from src.services.mcp_server.schema_utils import models_to_markdown

    # Generate model documentation
    model_docs = models_to_markdown([
        (TableCreate, "TableCreate (for creating tables)"),
        (TableUpdate, "TableUpdate (for updating tables)"),
    ], "Table Schema Documentation")

    # Additional conceptual documentation
    context_docs = """
## Table Scope

Tables can be scoped at two levels:

| Scope | organization_id | Visibility |
|-------|-----------------|------------|
| **global** | NULL | All organizations |
| **organization** | UUID | Single organization |

## Column Types

Supported column types for schema hints:

| Type | Description | Options |
|------|-------------|---------|
| `string` | Text value | `minLength`, `maxLength`, `pattern`, `enum` |
| `number` | Numeric value | `minimum`, `maximum` |
| `integer` | Whole number | `minimum`, `maximum` |
| `boolean` | True/false | - |
| `date` | ISO date string | - |
| `datetime` | ISO datetime string | - |
| `json` | Nested JSON object | - |
| `array` | List of values | `items` (type of array elements) |

## Column Properties

Each column can have these properties:
- `name` (required): Column identifier
- `type` (required): One of the types above
- `required`: Whether the field is required
- `description`: Human-readable description
- `default`: Default value
- `enum`: Array of allowed values (for string type)

## MCP Tools for Tables

- `list_tables` - List all accessible tables
- `get_table` - Get table details by ID
- `create_table` - Create a new table
- `update_table` - Update table properties

## Multi-tenancy

- **Platform admins**: See all tables
- **Regular users**: See global tables + their organization's tables

When creating: Set `scope` to 'global' or 'organization'
"""

    schema_doc = model_docs + context_docs
    return success_result("Table schema documentation", {"schema": schema_doc})


async def create_table(
    context: Any,
    name: str,
    description: str | None = None,
    scope: str = "organization",
    organization_id: str | None = None,
    columns: list[dict[str, Any]] | None = None,
) -> ToolResult:
    """Create a new table with explicit scope."""
    from sqlalchemy import select

    from shared.policies.probe import make_seed_admin_bypass
    from src.models.orm.tables import Table

    logger.info(f"MCP create_table called with name={name}, scope={scope}")

    if not name:
        return error_result("name is required")

    # Validate scope parameters
    if scope == "organization":
        if not organization_id:
            # Default to context org_id for non-admins
            if context.org_id:
                organization_id = str(context.org_id)
            else:
                return error_result("organization_id is required for organization scope")
    elif scope == "global":
        # Global tables can only be created by platform admins
        if not context.is_platform_admin:
            return error_result("Only platform admins can create global tables")
        organization_id = None

    # Parse UUIDs
    org_uuid: UUID | None = None

    if organization_id:
        try:
            org_uuid = UUID(organization_id)
        except ValueError:
            return error_result(f"Invalid organization_id format: {organization_id}")

    # Non-admins can only create tables in their own org
    if not context.is_platform_admin and context.org_id:
        if org_uuid and org_uuid != context.org_id:
            return error_result("Cannot create tables in other organizations")

    try:
        async with get_tool_db(context) as db:
            # Check for duplicate name within same scope. The global-scope
            # branch (org_uuid is None) is only reachable by a platform admin —
            # global table creation is admin-gated above, and an external
            # principal can never target global scope.
            query = select(Table).where(Table.name == name)
            if org_uuid:
                query = query.where(Table.organization_id == org_uuid)
            elif context.is_platform_admin:
                query = query.where(Table.organization_id.is_(None))
            else:
                # Defense-in-depth: a non-admin with no resolved org cannot
                # create/check a global table.
                return error_result("organization_id is required")

            existing = await db.execute(query)
            if existing.scalar_one_or_none():
                return error_result(f"Table with name '{name}' already exists in this scope")

            # Build schema from columns
            schema: dict[str, Any] | None = None
            if columns:
                schema = {"columns": columns}

            # Create table
            # Default seed: admin_bypass so platform admins aren't locked out.
            # Task 18 will wire policies into the MCP create path.
            table = Table(
                id=uuid4(),
                name=name,
                description=description,
                organization_id=org_uuid,
                schema=schema,
                access=make_seed_admin_bypass(),
                created_by=str(context.user_id),
            )
            db.add(table)
            await db.commit()

            display_text = f"Created table: {table.name}"
            return success_result(display_text, {
                "success": True,
                "id": str(table.id),
                "name": table.name,
                "scope": scope,
                "organization_id": str(org_uuid) if org_uuid else None,
            })

    except Exception as e:
        logger.exception(f"Error creating table via MCP: {e}")
        return error_result(f"Error creating table: {str(e)}")


async def update_table(
    context: Any,
    table_id: str,
    name: str | None = None,
    description: str | None = None,
    scope: str | None = None,
    organization_id: str | None = None,
    columns: list[dict[str, Any]] | None = None,
) -> ToolResult:
    """Update table properties."""
    from sqlalchemy import select

    from src.models.orm.tables import Table

    logger.info(f"MCP update_table called with id={table_id}")

    if not table_id:
        return error_result("table_id is required")

    try:
        table_uuid = UUID(table_id)
    except ValueError:
        return error_result(f"Invalid table_id format: {table_id}")

    try:
        async with get_tool_db(context) as db:
            query = select(Table).where(Table.id == table_uuid)

            # Non-admins can only update their org's tables (external-aware:
            # externals can't reach global tables to mutate them).
            query = apply_mcp_org_scope(query, Table, context)

            result = await db.execute(query)
            table = result.scalar_one_or_none()

            if not table:
                return error_result(f"Table not found: {table_id}")

            updates_made = []

            if name is not None:
                table.name = name
                updates_made.append("name")

            if description is not None:
                table.description = description
                updates_made.append("description")

            # Handle scope changes
            if scope is not None:
                # Validate scope change permissions
                if scope == "global" and not context.is_platform_admin:
                    return error_result("Only platform admins can set global scope")

                if scope == "global":
                    table.organization_id = None
                    updates_made.append("scope")
                elif scope == "organization":
                    if organization_id:
                        try:
                            table.organization_id = UUID(organization_id)
                        except ValueError:
                            return error_result(f"Invalid organization_id format: {organization_id}")
                    elif not table.organization_id:
                        # Default to context org_id
                        if context.org_id:
                            table.organization_id = context.org_id
                        else:
                            return error_result("organization_id required for organization scope")
                    updates_made.append("scope")

            if columns is not None:
                if table.schema is None:
                    table.schema = {}
                table.schema = {**table.schema, "columns": columns}
                updates_made.append("columns")

            if not updates_made:
                return error_result("No updates specified")

            await db.commit()

            current_scope = "organization" if table.organization_id else "global"

            display_text = f"Updated table: {table.name} ({', '.join(updates_made)})"
            return success_result(display_text, {
                "success": True,
                "id": str(table.id),
                "name": table.name,
                "scope": current_scope,
                "updates": updates_made,
            })

    except Exception as e:
        logger.exception(f"Error updating table via MCP: {e}")
        return error_result(f"Error updating table: {str(e)}")


async def delete_table(
    context: Any,
    table_id: str,
) -> ToolResult:
    """Delete a table and all its documents by ID."""
    from sqlalchemy import select

    from src.models.orm.tables import Table

    logger.info(f"MCP delete_table called with id={table_id}")

    if not table_id:
        return error_result("table_id is required")

    try:
        table_uuid = UUID(table_id)
    except ValueError:
        return error_result(f"Invalid table_id format: {table_id}")

    try:
        async with get_tool_db(context) as db:
            query = select(Table).where(Table.id == table_uuid)

            # Non-admins can only delete their org's tables
            if not context.is_platform_admin and context.org_id:
                query = query.where(
                    (Table.organization_id == context.org_id)
                )

            result = await db.execute(query)
            table = result.scalar_one_or_none()

            if not table:
                return error_result(f"Table not found: {table_id}")

            table_name = table.name
            await db.delete(table)
            await db.commit()

            display_text = f"Deleted table: {table_name}"
            return success_result(display_text, {
                "success": True,
                "id": table_id,
                "name": table_name,
            })

    except Exception as e:
        logger.exception(f"Error deleting table via MCP: {e}")
        return error_result(f"Error deleting table: {str(e)}")


# Tool metadata for registration
TOOLS = [
    ("list_tables", "List Tables", "List tables in the platform. Platform admin only."),
    ("get_table", "Get Table", "Get table details including schema by ID. Platform admin only."),
("create_table", "Create Table", "Create a new table with specified scope. Requires platform admin for global scope."),
    ("update_table", "Update Table", "Update table properties including name, description, scope, and columns."),
    ("delete_table", "Delete Table", "Delete a table and all its documents by ID."),
]


def register_tools(mcp: Any, get_context_fn: Any) -> None:
    """Register all tables tools with FastMCP."""
    from src.services.mcp_server.generators.fastmcp_generator import register_tool_with_context

    tool_funcs = {
        "list_tables": list_tables,
        "get_table": get_table,
"create_table": create_table,
        "update_table": update_table,
        "delete_table": delete_table,
    }

    for tool_id, name, description in TOOLS:
        register_tool_with_context(mcp, tool_funcs[tool_id], tool_id, description, get_context_fn)
