"""Shared helpers for workflow tool parameter schemas and validation."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from copy import deepcopy
from typing import Any

import pydantic_core
from jsonschema import FormatChecker
from jsonschema.exceptions import SchemaError
from jsonschema.validators import validator_for


_TYPE_TO_JSON_SCHEMA: dict[str, dict[str, Any]] = {
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
    "json": {"type": "object", "additionalProperties": True},
    "dict": {"type": "object", "additionalProperties": True},
    "object": {"type": "object", "additionalProperties": True},
}


def parameter_json_schema(parameter: Mapping[str, Any]) -> dict[str, Any]:
    """Build a JSON Schema fragment for one workflow parameter."""
    raw_schema = parameter.get("json_schema")
    schema = deepcopy(raw_schema) if isinstance(raw_schema, dict) else _TYPE_TO_JSON_SCHEMA.get(
        str(parameter.get("type", "string")).lower(),
        {"type": "string"},
    ).copy()

    options = parameter.get("options")
    if "enum" not in schema and isinstance(options, Sequence) and not isinstance(options, (str, bytes)):
        enum_values = []
        for option in options:
            if isinstance(option, Mapping) and "value" in option:
                enum_values.append(option["value"])
            elif isinstance(option, (str, int, float, bool)):
                enum_values.append(option)
        if enum_values:
            schema["enum"] = enum_values

    default_value = parameter.get("default_value")
    if default_value is not None and "default" not in schema:
        schema["default"] = default_value

    return schema


def parameters_json_schema(
    parameters_schema: Sequence[Mapping[str, Any]],
    *,
    allow_unknown_when_empty: bool = False,
) -> dict[str, Any]:
    """Build the full object schema for a tool's argument payload.

    Existing Solution registrations used an empty list both for genuinely
    zero-argument functions and for functions whose signature was never
    indexed. Callers can keep those ambiguous legacy rows permissive until a
    Solution redeploy records the inferred contract.
    """
    properties: dict[str, Any] = {}
    required: list[str] = []

    for parameter in parameters_schema:
        name = str(parameter.get("name", ""))
        if not name:
            continue

        properties[name] = parameter_json_schema(parameter)
        if parameter.get("required", False):
            required.append(name)

    schema: dict[str, Any] = {
        "type": "object",
        "properties": properties,
        "additionalProperties": allow_unknown_when_empty and not properties,
    }
    if required:
        schema["required"] = required
    return schema


def validate_arguments_against_schema(
    schema: dict[str, Any],
    arguments: dict[str, Any],
    *,
    max_issues: int = 10,
) -> tuple[list[dict[str, Any]], str | None]:
    """Validate arguments against a JSON Schema and return structured issues.

    Returns:
        (issues, schema_error) where `schema_error` is set only when the
        schema itself is invalid and therefore cannot be used for validation.
    """
    try:
        validator_class = validator_for(schema)
        validator_class.check_schema(schema)
        validator = validator_class(schema, format_checker=FormatChecker())
    except SchemaError as exc:
        return [], exc.message

    errors = sorted(
        validator.iter_errors(arguments),
        key=lambda error: [str(part) for part in error.absolute_path],
    )
    if not errors:
        return [], None

    issues = [
        {
            "path": _json_pointer(error.absolute_path),
            "message": error.message,
            "validator": error.validator,
            "expected": pydantic_core.to_jsonable_python(
                error.validator_value,
                fallback=str,
            ),
        }
        for error in errors[:max_issues]
    ]
    return issues, None


def _json_pointer(path: Any) -> str:
    parts = [str(part).replace("~", "~0").replace("/", "~1") for part in path]
    return "/" + "/".join(parts) if parts else "/"
