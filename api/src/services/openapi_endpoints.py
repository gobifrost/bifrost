"""
Dynamic OpenAPI Generation for Workflow Endpoints.

Generates per-workflow OpenAPI documentation for endpoint-enabled workflows.
Each workflow with endpoint_enabled=True gets its own OpenAPI path entry
with proper parameter schemas, descriptions, and allowed methods.

This module provides:
- generate_workflow_openapi_schema(): Create OpenAPI schema for a single workflow
- register_workflow_endpoints(): Register all endpoint-enabled workflows at startup
- refresh_workflow_endpoint(): Update a single workflow's route (for live updates)
"""

import logging
from typing import Any

from fastapi import FastAPI, Header, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models import Workflow
from src.services.tool_schema import parameter_json_schema

logger = logging.getLogger(__name__)

# Type mapping from our internal types to OpenAPI types
TYPE_TO_OPENAPI = {
    "string": {"type": "string"},
    "str": {"type": "string"},
    "int": {"type": "integer"},
    "integer": {"type": "integer"},
    "float": {"type": "number"},
    "number": {"type": "number"},
    "bool": {"type": "boolean"},
    "boolean": {"type": "boolean"},
    "list": {"type": "array", "items": {"type": "string"}},
    "array": {"type": "array", "items": {"type": "string"}},
    "json": {"type": "object"},
    "dict": {"type": "object"},
    "object": {"type": "object"},
}


def _param_to_openapi_schema(param: dict[str, Any]) -> dict[str, Any]:
    """
    Convert a workflow parameter to OpenAPI schema.

    Args:
        param: Parameter dict with name, type, required, label, default_value

    Returns:
        OpenAPI schema dict for the parameter
    """
    schema = parameter_json_schema(param)
    if not schema:
        param_type = param.get("type", "string")
        schema = TYPE_TO_OPENAPI.get(param_type, {"type": "string"}).copy()
    elif (
        param.get("json_schema") is None
        and str(param.get("type", "")).lower() in {"json", "dict", "object"}
        and schema == {"type": "object", "additionalProperties": True}
    ):
        schema = {"type": "object"}

    if param.get("default_value") is not None:
        schema["default"] = param["default_value"]

    return schema


def generate_workflow_openapi_schema(workflow: Workflow) -> dict[str, Any]:
    """
    Generate OpenAPI path schema for a workflow endpoint.

    Creates a complete OpenAPI path entry with:
    - Operations for each allowed HTTP method
    - Parameter schemas from workflow.parameters_schema
    - Request body schema for POST/PUT/PATCH methods
    - Proper descriptions and tags

    Args:
        workflow: Workflow ORM model with endpoint configuration

    Returns:
        OpenAPI path schema dict
    """
    path_schema: dict[str, Any] = {}
    allowed_methods = workflow.allowed_methods or ["POST"]
    parameters_schema = workflow.parameters_schema or []

    # Build query parameters (used for all methods)
    query_params = []
    for param in parameters_schema:
        param_schema = _param_to_openapi_schema(param)
        query_param = {
            "name": param["name"],
            "in": "query",
            "required": param.get("required", False),
            "schema": param_schema,
        }
        # Add description from label if available
        if param.get("label"):
            query_param["description"] = param["label"]
        query_params.append(query_param)

    # Build request body schema (for POST/PUT/PATCH)
    request_body_schema = {
        "type": "object",
        "properties": {},
    }
    required_props = []
    for param in parameters_schema:
        param_schema = _param_to_openapi_schema(param)
        request_body_schema["properties"][param["name"]] = param_schema
        if param.get("required", False):
            required_props.append(param["name"])

    if required_props:
        request_body_schema["required"] = required_props

    # Create operation for each allowed method
    for method in allowed_methods:
        method_lower = method.lower()

        operation = {
            "summary": f"{workflow.name}",
            "description": workflow.description or f"Execute {workflow.name} workflow",
            "operationId": f"execute_{workflow.name}_{method_lower}",
            "tags": ["Workflow Endpoints"],
            "security": [{"BifrostApiKey": []}],
            "responses": {
                "200": {
                    "description": "Successful execution",
                    "content": {
                        "application/json": {
                            "schema": {
                                "$ref": "#/components/schemas/EndpointExecuteResponse"
                            }
                        }
                    },
                },
                "401": {
                    "description": "Invalid or missing API key",
                },
                "404": {
                    "description": "Workflow not found or not endpoint-enabled",
                },
                "405": {
                    "description": "HTTP method not allowed for this workflow",
                },
            },
        }

        # Add parameters based on method
        if method_lower == "get":
            # GET uses query parameters only
            if query_params:
                operation["parameters"] = query_params
        else:
            # POST/PUT/PATCH/DELETE can use both query params and body
            if query_params:
                operation["parameters"] = query_params

            if method_lower in ("post", "put", "patch"):
                operation["requestBody"] = {
                    "description": "Workflow input parameters",
                    "required": False,
                    "content": {
                        "application/json": {
                            "schema": request_body_schema
                        }
                    },
                }

        path_schema[method_lower] = operation

    return path_schema


async def get_endpoint_enabled_workflows(db: AsyncSession) -> list[Workflow]:
    """
    Get all active workflows with endpoint_enabled=True.

    Args:
        db: Database session

    Returns:
        List of endpoint-enabled Workflow models
    """
    stmt = select(Workflow).where(
        Workflow.endpoint_enabled == True,  # noqa: E712
        Workflow.is_active == True,  # noqa: E712
    )
    result = await db.execute(stmt)
    return list(result.scalars().all())


def register_workflow_endpoint(app: FastAPI, workflow: Workflow) -> None:
    """
    Register a single workflow endpoint route.

    Creates a dedicated route handler for the workflow that delegates
    to the main endpoint execution logic.

    Args:
        app: FastAPI application instance
        workflow: Workflow to register
    """
    from src.routers.endpoints import execute_endpoint, EndpointExecuteResponse

    workflow_name = workflow.name
    allowed_methods = workflow.allowed_methods or ["POST"]

    # Check if route already exists and remove it
    path = f"/api/endpoints/{workflow_name}"
    app.routes = [r for r in app.routes if getattr(r, "path", None) != path]

    # Create the route handler
    # We use api_route to support multiple methods
    @app.api_route(
        path,
        methods=allowed_methods,
        response_model=EndpointExecuteResponse,
        summary=workflow.name,
        description=workflow.description or f"Execute {workflow.name} workflow",
        operation_id=f"execute_{workflow_name}",
        tags=["Workflow Endpoints"],
        name=f"execute_{workflow_name}",
    )
    async def workflow_endpoint_handler(
        request: Request,
        x_bifrost_key: str = Header(..., alias="X-Bifrost-Key"),
    ) -> EndpointExecuteResponse:
        # Delegate to the main execute_endpoint function
        return await execute_endpoint(
            workflow_name=workflow_name,
            request=request,
            x_bifrost_key=x_bifrost_key,
        )

    # Update the OpenAPI schema for this endpoint with proper parameters
    _update_openapi_schema(app, workflow)

    logger.info(f"Registered endpoint: /api/endpoints/{workflow_name} [{', '.join(allowed_methods)}]")


def _update_openapi_schema(app: FastAPI, workflow: Workflow) -> None:
    """
    Update the OpenAPI schema with workflow-specific parameters.

    This modifies the cached OpenAPI schema to include proper parameter
    definitions for the workflow endpoint.

    Args:
        app: FastAPI application instance
        workflow: Workflow with parameter definitions
    """
    # Force regeneration of OpenAPI schema on next request
    app.openapi_schema = None

    # Store workflow schemas for custom openapi() override
    if not hasattr(app, "_workflow_schemas"):
        app._workflow_schemas = {}

    app._workflow_schemas[workflow.name] = generate_workflow_openapi_schema(workflow)


async def register_workflow_endpoints(app: FastAPI, db: AsyncSession) -> int:
    """
    Register all endpoint-enabled workflows at startup.

    Queries the database for all active workflows with endpoint_enabled=True
    and registers a dedicated route for each one.

    Args:
        app: FastAPI application instance
        db: Database session

    Returns:
        Number of endpoints registered
    """
    workflows = await get_endpoint_enabled_workflows(db)

    for workflow in workflows:
        register_workflow_endpoint(app, workflow)

    # Install custom OpenAPI generator
    _install_custom_openapi(app)

    logger.info(f"Registered {len(workflows)} workflow endpoints")
    return len(workflows)


def refresh_workflow_endpoint(app: FastAPI, workflow: Workflow) -> None:
    """
    Refresh a single workflow endpoint (for live updates).

    Called when a workflow file is updated and endpoint_enabled changes
    or when endpoint configuration (allowed_methods, etc.) changes.

    Args:
        app: FastAPI application instance
        workflow: Updated workflow model
    """
    if workflow.endpoint_enabled:
        register_workflow_endpoint(app, workflow)
    else:
        # Remove the endpoint if it was disabled
        remove_workflow_endpoint(app, workflow.name)


def remove_workflow_endpoint(app: FastAPI, workflow_name: str) -> None:
    """
    Remove a workflow endpoint route.

    Args:
        app: FastAPI application instance
        workflow_name: Name of workflow to remove
    """
    path = f"/api/endpoints/{workflow_name}"
    original_count = len(app.routes)
    app.routes = [r for r in app.routes if getattr(r, "path", None) != path]

    if len(app.routes) < original_count:
        # Force regeneration of OpenAPI schema
        app.openapi_schema = None

        # Remove from workflow schemas
        if hasattr(app, "_workflow_schemas"):
            app._workflow_schemas.pop(workflow_name, None)

        logger.info(f"Removed endpoint: /api/endpoints/{workflow_name}")


def _install_custom_openapi(app: FastAPI) -> None:
    """
    Install a custom OpenAPI generator that includes workflow parameters.

    This overrides the default openapi() method to inject proper parameter
    schemas for each workflow endpoint.
    """
    original_openapi = app.openapi

    def custom_openapi():
        if app.openapi_schema:
            return app.openapi_schema

        # Generate base schema
        openapi_schema = original_openapi()

        # Add security scheme for API key
        if "components" not in openapi_schema:
            openapi_schema["components"] = {}
        if "securitySchemes" not in openapi_schema["components"]:
            openapi_schema["components"]["securitySchemes"] = {}

        openapi_schema["components"]["securitySchemes"]["BifrostApiKey"] = {
            "type": "apiKey",
            "in": "header",
            "name": "X-Bifrost-Key",
            "description": "Workflow API key for endpoint access",
        }

        # Add EndpointExecuteResponse schema if not present
        if "schemas" not in openapi_schema["components"]:
            openapi_schema["components"]["schemas"] = {}

        if "EndpointExecuteResponse" not in openapi_schema["components"]["schemas"]:
            openapi_schema["components"]["schemas"]["EndpointExecuteResponse"] = {
                "type": "object",
                "properties": {
                    "execution_id": {"type": "string", "description": "Unique execution ID"},
                    "status": {"type": "string", "description": "Execution status"},
                    "message": {"type": "string", "nullable": True, "description": "Optional message"},
                    "result": {"description": "Workflow result data"},
                    "error": {"type": "string", "nullable": True, "description": "Error message if failed"},
                    "duration_ms": {"type": "integer", "nullable": True, "description": "Execution duration in milliseconds"},
                },
                "required": ["execution_id", "status"],
            }

        # Inject workflow-specific schemas
        if hasattr(app, "_workflow_schemas"):
            for workflow_name, schema in app._workflow_schemas.items():
                path_key = f"/api/endpoints/{workflow_name}"
                if path_key in openapi_schema.get("paths", {}):
                    # Merge our detailed schema with the auto-generated one
                    for method, operation in schema.items():
                        if method in openapi_schema["paths"][path_key]:
                            # Update parameters
                            if "parameters" in operation:
                                openapi_schema["paths"][path_key][method]["parameters"] = operation["parameters"]
                            # Update request body
                            if "requestBody" in operation:
                                openapi_schema["paths"][path_key][method]["requestBody"] = operation["requestBody"]
                            # Update security
                            if "security" in operation:
                                openapi_schema["paths"][path_key][method]["security"] = operation["security"]

        app.openapi_schema = openapi_schema
        return app.openapi_schema

    app.openapi = custom_openapi
