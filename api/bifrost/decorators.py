"""
Workflow, Tool, and Data Provider Decorators - Standalone SDK Version

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
from dataclasses import dataclass, field
from typing import Any, Callable, Literal, TypeVar

logger = logging.getLogger(__name__)

F = TypeVar("F", bound=Callable[..., Any])

# Type discriminator for all executable types
ExecutableType = Literal["workflow", "tool", "data_provider"]


@dataclass
class WorkflowParameter:
    """Metadata about a workflow parameter derived from function signature."""

    name: str
    type_hint: str
    default: Any | None = None
    required: bool = True
    description: str | None = None
    ui_type: str = "text"
    options: list[str] | None = None


@dataclass
class WorkflowMetadata:
    """
    Metadata attached to workflow functions by the @workflow decorator.

    Only identity parameters are set via decorators. All execution configuration
    (timeouts, schedules, endpoints, etc.) is managed via UI/API and stored in DB.
    """

    # Identity (settable via decorator)
    name: str = ""
    description: str = ""
    category: str = "General"
    tags: list[str] = field(default_factory=list)

    # Type discriminator
    type: ExecutableType = "workflow"

    # Parameters (derived from function signature)
    parameters: list[WorkflowParameter] = field(default_factory=list)

    # Tool Configuration (settable via decorator)
    is_tool: bool = False

    # Source info (set by discovery)
    source_file_path: str | None = None
    relative_file_path: str | None = None

    def __post_init__(self) -> None:
        """Sync legacy 'is_tool' field with 'type' field."""
        if self.is_tool and self.type == "workflow":
            self.type = "tool"
        elif self.type == "tool":
            self.is_tool = True


@dataclass
class DataProviderMetadata:
    """
    Metadata attached to data provider functions by the @data_provider decorator.

    Only identity parameters are set via decorators. Execution configuration
    (timeouts, cache TTL) is managed via UI/API and stored in DB.
    """

    # Identity (settable via decorator)
    name: str = ""
    description: str = ""
    category: str = "General"
    tags: list[str] = field(default_factory=list)

    # Type discriminator
    type: ExecutableType = "data_provider"

    # Parameters (derived from function signature)
    parameters: list[WorkflowParameter] = field(default_factory=list)

    # Source info (set by discovery)
    source_file_path: str | None = None
    relative_file_path: str | None = None


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
) -> Callable[[F], F] | F:
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

    def decorator(func: F) -> F:
        # Extract description from docstring
        func_description = description
        if func_description is None and func.__doc__:
            func_description = func.__doc__.split("\n")[0].strip()

        # Determine type based on is_tool flag
        workflow_type: ExecutableType = "tool" if is_tool else "workflow"

        # Create metadata with identity fields only
        metadata = WorkflowMetadata(
            name=name or func.__name__,
            description=func_description or "",
            category=category,
            tags=tags or [],
            type=workflow_type,
        )

        # Attach metadata to function (all executable types use same attribute)
        func._executable_metadata = metadata  # type: ignore
        return func

    if _func is not None:
        return decorator(_func)
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
) -> Callable[[F], F] | F:
    """
    Decorator for registering AI agent tools.

    This is an alias for @workflow(is_tool=True) that provides a cleaner API
    for creating workflows specifically designed as AI agent tools.

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
) -> Callable[[F], F] | F:
    """
    Decorator for data provider functions.

    Data providers return dynamic options for form fields and app builder.
    Data providers are stored in the workflows table with type='data_provider'.
    Only identity parameters are accepted - execution configuration is managed via UI/API.

    Usage:
        @data_provider
        async def get_departments() -> list[str]:
            '''Get list of departments.'''
            return ["Engineering", "Sales", "Marketing"]

        @data_provider(category="m365")
        async def get_m365_users() -> list[dict]:
            '''Returns M365 users for the organization.'''
            ...

    Args:
        name: Provider name (defaults to function name)
        description: Description (defaults to first line of docstring)
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

    def decorator(func: F) -> F:
        func_description = description
        if func_description is None and func.__doc__:
            func_description = func.__doc__.split("\n")[0].strip()

        metadata = DataProviderMetadata(
            name=name or func.__name__,
            description=func_description or "",
            category=category,
            tags=tags or [],
            type="data_provider",
        )

        # Attach metadata to function (all executable types use same attribute)
        func._executable_metadata = metadata  # type: ignore
        return func

    if _func is not None:
        return decorator(_func)
    return decorator
