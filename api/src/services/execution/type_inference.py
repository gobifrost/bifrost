"""
Type Inference Module

Extracts workflow parameter metadata from Python function signatures.
Eliminates the need for @param decorators by deriving all information
from type hints and default values.

Usage:
    from src.services.execution.type_inference import extract_parameters_from_signature

    @workflow
    async def my_workflow(name: str, count: int = 1, active: bool = True) -> dict:
        ...

    # Parameters are automatically derived:
    # - name: type=string, required=True, label="Name"
    # - count: type=int, required=False, default_value=1, label="Count"
    # - active: type=bool, required=False, default_value=True, label="Active"
"""

import inspect
import logging
import re
from enum import Enum
from typing import TYPE_CHECKING, Any, Literal, Union, get_args, get_origin, get_type_hints

if TYPE_CHECKING:
    pass  # ExecutionContext type only needed for type hints, not runtime

logger = logging.getLogger(__name__)


def _is_execution_context(param_type: Any) -> bool:
    """
    Check if a type annotation refers to ExecutionContext.

    This check is done by class name rather than identity to avoid
    circular import issues between type_inference.py and sdk.context.
    """
    if param_type is None or param_type is inspect.Parameter.empty:
        return False

    # Handle string annotations
    if isinstance(param_type, str):
        return "ExecutionContext" in param_type

    # Handle actual type
    type_name = getattr(param_type, "__name__", "")
    if type_name == "ExecutionContext":
        return True

    # Check qualname for nested classes
    qualname = getattr(param_type, "__qualname__", "")
    if "ExecutionContext" in qualname:
        return True

    return False

# Type mapping: Python type -> UI type string
TYPE_MAPPING: dict[type, str] = {
    str: "string",
    int: "int",
    float: "float",
    bool: "bool",
    list: "list",
    dict: "json",
}

# Valid UI type strings for parameter validation
VALID_PARAM_TYPES: set[str] = {"string", "int", "bool", "float", "json", "list"}

_JSON_TYPE_MAPPING: dict[type, str] = {
    str: "string",
    int: "integer",
    float: "number",
    bool: "boolean",
}


def get_ui_type(python_type: Any) -> str:
    """
    Convert Python type annotation to UI type string.

    Args:
        python_type: Python type annotation

    Returns:
        UI type string (string, int, bool, float, list, json)

    Examples:
        get_ui_type(str) -> "string"
        get_ui_type(int) -> "int"
        get_ui_type(list[str]) -> "list"
        get_ui_type(dict[str, Any]) -> "json"
        get_ui_type(str | None) -> "string"
        get_ui_type(Literal["a", "b"]) -> "string"
    """
    # Handle None type
    if python_type is type(None):
        return "string"

    # Direct mapping
    if python_type in TYPE_MAPPING:
        return TYPE_MAPPING[python_type]

    # Handle generic types (list[str], dict[str, Any], etc.)
    origin = get_origin(python_type)

    # Handle Literal types - infer base type from values
    if origin is Literal:
        args = get_args(python_type)
        if args:
            # Infer type from first value
            first_val = args[0]
            if isinstance(first_val, str):
                return "string"
            elif isinstance(first_val, bool):  # Must check bool before int (bool is subclass of int)
                return "bool"
            elif isinstance(first_val, int):
                return "int"
            elif isinstance(first_val, float):
                return "float"
        return "string"  # Default for empty Literal
    if isinstance(python_type, type) and issubclass(python_type, Enum):
        member_values = [member.value for member in python_type]
        if member_values:
            first_val = member_values[0]
            if isinstance(first_val, str):
                return "string"
            if isinstance(first_val, bool):
                return "bool"
            if isinstance(first_val, int):
                return "int"
            if isinstance(first_val, float):
                return "float"
        return "string"
    if origin is list:
        return "list"
    if origin is dict:
        return "json"

    # Handle Union types (str | None, Optional[str], Union[str, None])
    if origin is Union:
        args = get_args(python_type)
        # Filter out NoneType to get the actual type
        non_none_types = [t for t in args if t is not type(None)]
        if non_none_types:
            return get_ui_type(non_none_types[0])
        return "string"

    # Handle Python 3.10+ union syntax (str | None) - UnionType
    type_name = type(python_type).__name__
    if type_name == "UnionType":
        args = get_args(python_type)
        non_none_types = [t for t in args if t is not type(None)]
        if non_none_types:
            return get_ui_type(non_none_types[0])
        return "string"

    # Fallback for Any, unknown types, or complex types
    return "json"


def is_optional_type(python_type: Any) -> bool:
    """
    Check if a type annotation indicates an optional parameter.

    Args:
        python_type: Python type annotation

    Returns:
        True if the type is Optional (Union with None)

    Examples:
        is_optional_type(str) -> False
        is_optional_type(str | None) -> True
        is_optional_type(Optional[str]) -> True
    """
    origin = get_origin(python_type)

    # Handle Union types (Optional[str] is Union[str, None])
    if origin is Union:
        args = get_args(python_type)
        return type(None) in args

    # Handle Python 3.10+ union syntax (str | None)
    type_name = type(python_type).__name__
    if type_name == "UnionType":
        args = get_args(python_type)
        return type(None) in args

    return False


def get_literal_options(python_type: Any) -> list[dict[str, str]] | None:
    """
    Extract options from Literal type as {label, value} pairs.

    Args:
        python_type: Python type annotation

    Returns:
        List of {label, value} dicts if Literal type, None otherwise

    Examples:
        get_literal_options(Literal["Open", "Closed"]) -> [{"label": "Open", "value": "Open"}, {"label": "Closed", "value": "Closed"}]
        get_literal_options(str) -> None
        get_literal_options(str | None) -> None
    """
    origin = get_origin(python_type)

    # Handle Literal types directly
    if origin is Literal:
        args = get_args(python_type)
        return [{"label": str(v), "value": str(v)} for v in args]

    if isinstance(python_type, type) and issubclass(python_type, Enum):
        return [
            {"label": str(member.value), "value": str(member.value)}
            for member in python_type
        ]

    # Handle Union types that contain Literal (e.g., Literal["a", "b"] | None)
    if origin is Union:
        args = get_args(python_type)
        for arg in args:
            if arg is not type(None):
                options = get_literal_options(arg)
                if options:
                    return options

    # Handle Python 3.10+ union syntax
    type_name = type(python_type).__name__
    if type_name == "UnionType":
        args = get_args(python_type)
        for arg in args:
            if arg is not type(None):
                options = get_literal_options(arg)
                if options:
                    return options

    return None


def get_python_type_name(python_type: Any) -> str:
    """Render a human-readable Python annotation string."""
    if python_type is inspect.Parameter.empty:
        return "Any"
    if python_type is type(None):
        return "None"
    if python_type is Any:
        return "Any"
    if isinstance(python_type, type):
        if issubclass(python_type, Enum):
            return python_type.__name__
        return python_type.__name__

    origin = get_origin(python_type)
    args = get_args(python_type)

    if origin is Literal:
        return "Literal[" + ", ".join(repr(arg) for arg in args) + "]"
    if origin is Union:
        return " | ".join(get_python_type_name(arg) for arg in args)
    if type(python_type).__name__ == "UnionType":
        return " | ".join(get_python_type_name(arg) for arg in args)
    if origin in {list, dict, tuple, set}:
        inner = ", ".join(get_python_type_name(arg) for arg in args)
        return f"{getattr(origin, '__name__', str(origin))}[{inner}]" if inner else getattr(origin, "__name__", str(origin))
    return str(python_type).replace("typing.", "")


def _literal_schema_from_values(values: list[Any]) -> dict[str, Any]:
    schema: dict[str, Any] = {"enum": values}
    if not values:
        return schema
    first_val = values[0]
    if isinstance(first_val, str):
        schema["type"] = "string"
    elif isinstance(first_val, bool):
        schema["type"] = "boolean"
    elif isinstance(first_val, int):
        schema["type"] = "integer"
    elif isinstance(first_val, float):
        schema["type"] = "number"
    return schema


def build_json_schema(python_type: Any) -> dict[str, Any]:
    """Build a JSON Schema fragment from a Python annotation."""
    if python_type is inspect.Parameter.empty:
        return {"type": "string"}
    if python_type is type(None):
        return {"type": "null"}
    if python_type is Any:
        return {}

    if isinstance(python_type, type) and issubclass(python_type, Enum):
        values = [member.value for member in python_type]
        schema = _literal_schema_from_values(values)
        return schema or {"type": "string"}

    if python_type in _JSON_TYPE_MAPPING:
        return {"type": _JSON_TYPE_MAPPING[python_type]}

    origin = get_origin(python_type)
    args = get_args(python_type)

    if origin is Literal:
        return _literal_schema_from_values(list(args))

    if origin is list:
        items = build_json_schema(args[0]) if args else {}
        schema: dict[str, Any] = {"type": "array"}
        if items:
            schema["items"] = items
        else:
            schema["items"] = {}
        return schema

    if origin is dict:
        schema = {"type": "object", "additionalProperties": True}
        if len(args) >= 2:
            value_schema = build_json_schema(args[1])
            if value_schema:
                schema["additionalProperties"] = value_schema
        return schema

    if origin is Union:
        non_none_types = [t for t in args if t is not type(None)]
        if len(non_none_types) == 1:
            return build_json_schema(non_none_types[0])
        if non_none_types:
            return {"anyOf": [build_json_schema(t) for t in non_none_types]}
        return {"type": "string"}

    if type(python_type).__name__ == "UnionType":
        non_none_types = [t for t in args if t is not type(None)]
        if len(non_none_types) == 1:
            return build_json_schema(non_none_types[0])
        if non_none_types:
            return {"anyOf": [build_json_schema(t) for t in non_none_types]}
        return {"type": "string"}

    return {"type": "object"}


def generate_label(param_name: str) -> str:
    """
    Generate human-readable label from parameter name.

    Args:
        param_name: Parameter name (e.g., "user_email", "firstName")

    Returns:
        Human-readable label (e.g., "User Email", "First Name")

    Examples:
        generate_label("user_email") -> "User Email"
        generate_label("firstName") -> "First Name"
        generate_label("api_key") -> "Api Key"
    """
    # Replace underscores with spaces
    label = param_name.replace("_", " ")
    # Handle camelCase by inserting space before capital letters
    label = re.sub(r"([a-z])([A-Z])", r"\1 \2", label)
    # Title case
    return label.title()


def extract_parameters_from_signature(func: Any) -> list[dict[str, Any]]:
    """
    Extract parameter metadata from function signature.

    Args:
        func: The workflow/data provider function

    Returns:
        List of parameter dictionaries with:
        - name: str
        - type: str (string, int, bool, float, list, json)
        - required: bool
        - label: str
        - default_value: Any (optional, only if has default)
        - options: list[dict[str, str]] (optional, for Literal types)

    Note:
        - ExecutionContext parameters are excluded
        - *args and **kwargs are excluded
        - Parameters without type hints default to "string"
        - Literal types are converted to options list for dropdown UI
    """
    parameters: list[dict[str, Any]] = []

    try:
        sig = inspect.signature(func)

        # Try to get type hints (handles forward references)
        try:
            type_hints = get_type_hints(func)
        except Exception:
            type_hints = {}

        for param_name, param in sig.parameters.items():
            # Skip *args and **kwargs
            if param.kind in (
                inspect.Parameter.VAR_POSITIONAL,
                inspect.Parameter.VAR_KEYWORD,
            ):
                continue

            # Get type from type hints or annotation
            param_type = type_hints.get(param_name, param.annotation)

            # Skip ExecutionContext parameter (by type or by name)
            if _is_execution_context(param_type):
                continue

            # Skip parameter named "context" without type hint (legacy support)
            if param_name == "context" and param_type is inspect.Parameter.empty:
                continue

            # Determine if parameter has a default value
            has_default = param.default is not inspect.Parameter.empty
            default_value = param.default if has_default else None

            # Determine UI type from annotation
            if param_type is inspect.Parameter.empty:
                ui_type = "string"  # Default for untyped parameters
                is_optional = has_default
            else:
                ui_type = get_ui_type(param_type)
                is_optional = is_optional_type(param_type) or has_default

            # Build parameter metadata
            param_meta: dict[str, Any] = {
                "name": param_name,
                "type": ui_type,
                "required": not is_optional,
                "label": generate_label(param_name),
                "python_type": get_python_type_name(param_type),
                "json_schema": build_json_schema(param_type),
            }

            # Add default_value only if it exists and is serializable
            if has_default and default_value is not None:
                # Only include primitive default values that can be serialized
                if isinstance(default_value, (str, int, float, bool, list, dict)):
                    param_meta["default_value"] = default_value

            # Add options for Literal types (enables dropdown UI)
            if param_type is not inspect.Parameter.empty:
                options = get_literal_options(param_type)
                if options:
                    param_meta["options"] = options

            parameters.append(param_meta)

        return parameters

    except Exception as e:
        # Log error but return empty list to avoid breaking discovery
        logger.warning(f"Failed to extract parameters from {getattr(func, '__name__', func)}: {e}")
        return []
