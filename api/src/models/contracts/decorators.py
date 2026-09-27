"""
Decorator property contract models.

Used by the decorator properties API for reading and writing
decorator properties in Python source files.
"""

from typing import Any, Literal

from pydantic import BaseModel, Field

DecoratorType = Literal["workflow", "data_provider", "tool", "service"]


class DecoratorInfo(BaseModel):
    """Information about a discovered decorator in a source file."""

    decorator_type: DecoratorType
    function_name: str
    line_number: int = 0
    properties: dict[str, Any]
    has_parentheses: bool = Field(
        description="True if decorator uses parentheses syntax: @workflow(...)"
    )


class DecoratorPropertiesResponse(BaseModel):
    """Response containing all decorators found in a file."""

    path: str = Field(description="File path")
    decorators: list[DecoratorInfo] = Field(
        description="List of discovered decorators"
    )


class UpdatePropertiesRequest(BaseModel):
    """Request to update properties on a decorator."""

    path: str = Field(description="File path")
    function_name: str = Field(description="Target function name")
    properties: dict[str, Any] = Field(description="Properties to set/update")
    expected_etag: str | None = Field(
        default=None,
        description="ETag for optimistic concurrency control with Monaco editor",
    )


class UpdatePropertiesResponse(BaseModel):
    """Response after updating decorator properties."""

    modified: bool = Field(description="Whether any changes were made")
    changes: list[str] = Field(description="Human-readable list of changes made")
    new_etag: str = Field(description="New ETag after modification")
