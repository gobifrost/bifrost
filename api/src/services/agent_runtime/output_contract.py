"""Invocation-owned output contracts for agent runs.

Agents declare no mandatory input or output schemas. A caller may attach an
``output_schema`` to one invocation; the engine then requests structured
output, validates the final JSON against the exact caller schema (engine-side
JSON Schema validation is authoritative), permits exactly one bounded
correction turn when budget remains, and otherwise finishes
``contract_failed`` with the raw invalid output preserved.
"""

from __future__ import annotations

import json
from typing import Any

import jsonschema
from jsonschema.exceptions import SchemaError


class ContractError(ValueError):
    """An invocation output contract (schema) is unusable."""


def validate_output_schema(schema: dict[str, Any] | None) -> None:
    """Validate a caller-supplied output schema at admission.

    Raises:
        ContractError: schema is not a dict or not a valid JSON Schema.
    """
    if schema is None:
        return
    if not isinstance(schema, dict):
        raise ContractError("output_schema must be a JSON object (JSON Schema).")
    try:
        jsonschema.validators.validator_for(schema).check_schema(schema)
    except SchemaError as exc:
        raise ContractError(f"output_schema is not a valid JSON Schema: {exc}") from exc


def parse_final_output(raw_text: str) -> tuple[Any | None, bool]:
    """Parse final model text as JSON.

    Returns ``(parsed, was_json)``. Non-JSON output yields ``(None, False)``;
    the caller preserves the raw text and fails the contract.
    """
    try:
        return json.loads(raw_text), True
    except (json.JSONDecodeError, TypeError):
        return None, False


def validate_output(schema: dict[str, Any], parsed: Any) -> list[str]:
    """Validate parsed output against the exact caller schema.

    Returns a list of human-readable violations (empty when valid).
    """
    validator = jsonschema.validators.validator_for(schema)(schema)
    errors = sorted(validator.iter_errors(parsed), key=lambda e: list(e.path))
    return [f"{'/'.join(str(p) for p in error.path) or '<root>'}: {error.message}" for error in errors]


def correction_allowed(
    *,
    iterations_used: int,
    max_iterations: int | None,
    tokens_used: int,
    max_tokens: int | None,
) -> bool:
    """One correction turn is allowed only when budget remains."""
    if max_iterations is not None and iterations_used >= max_iterations:
        return False
    if max_tokens is not None and tokens_used >= max_tokens:
        return False
    return True


def contract_correction_prompt(errors: list[str]) -> str:
    """Bounded correction instruction naming the exact violations."""
    details = "\n".join(f"- {error}" for error in errors[:10])
    return (
        "Your previous response did not satisfy the caller's output contract. "
        "Respond again with ONLY the corrected JSON object, no prose. "
        f"Violations:\n{details}"
    )
