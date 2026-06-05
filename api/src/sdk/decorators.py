"""
Workflow, Tool, and Data Provider Decorators

Decorators for attaching metadata to executable user code.
No registration - metadata is stored on the function and discovered dynamically.

All executable types are stored in the workflows table with a type discriminator:
- @workflow: type='workflow' - Standard workflows
- @tool: type='tool' - AI agent tools
- @data_provider: type='data_provider' - Data providers for forms/app builder

Parameter information is derived from function signatures - no @param decorator needed.

## Decorator Parameters

Only identity parameters are accepted in decorators. All other configuration
(schedules, timeouts, endpoints, etc.) is managed via the UI/API.

Allowed parameters:
- name: Override function name (stable identifier)
- description: Override docstring
- category: Hint for organization (overridable in UI)
- tags: Hints for filtering (overridable in UI)
- is_tool: Mark as AI agent tool (@workflow only)

Unknown parameters are ignored with a warning for backwards compatibility.
"""

import logging
from collections.abc import Callable
from typing import Any

from src.services.execution.module_loader import (
    DataProviderMetadata,
    ExecutableType,
    WorkflowMetadata,
    WorkflowParameter,
)
from src.services.execution.type_inference import extract_parameters_from_signature

logger = logging.getLogger(__name__)


def workflow(
    _func: Callable | None = None,
    *,
    # Identity parameters only
    name: str | None = None,
    description: str | None = None,
    category: str = "General",
    tags: list[str] | None = None,
    is_tool: bool = False,
    # Accept unknown params for backwards compatibility
    **kwargs: Any,
):
    """
    Decorator for registering workflow functions.

    Parameters are automatically derived from function signatures.
    Only identity parameters are accepted - all other configuration
    (schedules, timeouts, endpoints) is managed via the UI/API.

    Usage:
        @workflow
        async def greet_user(name: str, count: int = 1) -> dict:
            '''Greet a user multiple times.'''
            return {"greetings": [f"Hello {name}!" for _ in range(count)]}

        @workflow(category="Admin", tags=["user", "m365"])
        async def onboard_user(email: str, license_type: str = "E3") -> dict:
            '''Onboard a new M365 user.'''
            ...

    Args:
        name: Initial MCP tool name (defaults to function name)
        description: Description (defaults to first line of docstring)
        category: Category for organization (default: "General")
        tags: Optional list of tags for filtering
        is_tool: If True, available as AI agent tool

    Returns:
        Decorated function with _executable_metadata attribute
    """
    # Warn about deprecated parameters
    if kwargs:
        unknown_params = sorted(kwargs.keys())
        logger.warning(
            "Unknown @workflow parameters ignored: %s. "
            "Configuration should be set via UI/API.",
            ", ".join(unknown_params),
        )

    def decorator(func: Callable) -> Callable:
        # Derive name from function name if not provided
        workflow_name = name or func.__name__

        # Derive description from docstring if not provided
        workflow_description = description
        if workflow_description is None and func.__doc__:
            # Use first line of docstring for general description
            workflow_description = func.__doc__.strip().split('\n')[0].strip()
        workflow_description = workflow_description or ""

        # Get tags with default
        workflow_tags = tags if tags is not None else []

        # Extract parameters from function signature
        param_dicts = extract_parameters_from_signature(func)
        parameters = [
            WorkflowParameter(
                name=p["name"],
                type=p["type"],
                label=p.get("label"),
                required=p["required"],
                default_value=p.get("default_value"),
                options=p.get("options"),
            )
            for p in param_dicts
        ]

        # Get source file path
        source_file_path = None
        if hasattr(func, '__code__'):
            source_file_path = func.__code__.co_filename

        # Determine type based on is_tool flag
        workflow_type: ExecutableType = "tool" if is_tool else "workflow"

        # Initialize metadata with identity fields only
        # Execution config (timeout, schedule, endpoint, etc.) comes from DB
        metadata = WorkflowMetadata(
            name=workflow_name,
            description=workflow_description,
            category=category,
            tags=workflow_tags,
            type=workflow_type,
            source_file_path=source_file_path,
            parameters=parameters,
            function=func,
        )

        # Store metadata on function for dynamic discovery
        # All executable types use the same attribute name for unified loading
        func._executable_metadata = metadata  # type: ignore[attr-defined]

        logger.debug(
            f"Workflow decorator applied: {workflow_name} "
            f"({len(parameters)} params, type={workflow_type})"
        )

        # Return function unchanged (for normal Python execution)
        return func

    # Support both @workflow and @workflow(...) syntax
    if _func is not None:
        # Called as @workflow without parentheses
        return decorator(_func)
    else:
        # Called as @workflow(...) with arguments
        return decorator


def tool(
    _func: Callable | None = None,
    *,
    # Identity parameters only
    name: str | None = None,
    description: str | None = None,
    category: str = "General",
    tags: list[str] | None = None,
    # Accept unknown params for backwards compatibility
    **kwargs: Any,
):
    """
    Decorator for registering AI agent tools.

    This is an alias for @workflow(is_tool=True) that provides a cleaner API
    for creating workflows specifically designed as AI agent tools.

    Parameters are automatically derived from function signatures.

    Usage:
        @tool
        async def get_user_info(email: str) -> dict:
            '''Get user information by email address.'''
            ...

        @tool(description="Search for users by name or email")
        async def search_users(query: str, limit: int = 10) -> list[dict]:
            '''Search for users matching the query.'''
            ...

    Args:
        name: Tool name (defaults to function name)
        description: LLM-friendly description (defaults to first line of docstring)
        category: Category for organization (default: "General")
        tags: Optional list of tags for filtering

    Returns:
        Decorated function
    """
    # Forward any unknown kwargs to workflow for consistent warning
    return workflow(
        _func,
        name=name,
        description=description,
        category=category,
        tags=tags,
        is_tool=True,
        **kwargs,
    )


def data_provider(
    _func: Callable | None = None,
    *,
    # Identity parameters only
    name: str | None = None,
    description: str | None = None,
    category: str = "General",
    tags: list[str] | None = None,
    # Accept unknown params for backwards compatibility
    **kwargs: Any,
):
    """
    Decorator for registering data provider functions.

    Data providers return dynamic options for form fields and app builder.
    Parameters are automatically derived from function signatures.
    Only identity parameters are accepted - execution configuration is managed via UI/API.

    Data providers are stored in the workflows table with type='data_provider'.

    Usage:
        @data_provider
        async def get_available_licenses() -> list[dict]:
            '''Returns available M365 licenses.'''
            ...

        @data_provider(category="m365")
        async def get_m365_users() -> list[dict]:
            '''Returns M365 users for the organization.'''
            ...

    Args:
        name: Data provider name (defaults to function name)
        description: Human-readable description (defaults to first line of docstring)
        category: Category for organization (default: "General")
        tags: Optional list of tags for filtering

    Returns:
        Decorated function with _executable_metadata attribute
    """
    # Warn about deprecated parameters
    if kwargs:
        unknown_params = sorted(kwargs.keys())
        logger.warning(
            "Unknown @data_provider parameters ignored: %s. "
            "Configuration should be set via UI/API.",
            ", ".join(unknown_params),
        )

    def decorator(func: Callable) -> Callable:
        # Derive name from function name if not provided
        provider_name = name or func.__name__

        # Derive description from docstring if not provided
        provider_description = description
        if provider_description is None and func.__doc__:
            # Use first line of docstring
            provider_description = func.__doc__.strip().split('\n')[0].strip()
        provider_description = provider_description or ""

        # Extract parameters from function signature
        param_dicts = extract_parameters_from_signature(func)
        parameters = [
            WorkflowParameter(
                name=p["name"],
                type=p["type"],
                label=p.get("label"),
                required=p["required"],
                default_value=p.get("default_value"),
                options=p.get("options"),
            )
            for p in param_dicts
        ]

        # Get source file path
        source_file_path = None
        if hasattr(func, '__code__'):
            source_file_path = func.__code__.co_filename

        # Get tags with default
        provider_tags = tags if tags is not None else []

        # Create metadata with identity fields only
        # Execution config (timeout, cache_ttl) comes from DB
        metadata = DataProviderMetadata(
            name=provider_name,
            description=provider_description,
            category=category,
            tags=provider_tags,
            type="data_provider",
            parameters=parameters,
            function=func,
            source_file_path=source_file_path,
        )

        # Store metadata on function for dynamic discovery
        # All executable types use the same attribute name for unified loading
        func._executable_metadata = metadata  # type: ignore[attr-defined]

        logger.debug(
            f"Data provider decorator applied: {provider_name} "
            f"({len(parameters)} params)"
        )

        # Return function unchanged
        return func

    # Support both @data_provider and @data_provider(...) syntax
    if _func is not None:
        # Called as @data_provider without parentheses
        return decorator(_func)
    else:
        # Called as @data_provider(...) with arguments
        return decorator
