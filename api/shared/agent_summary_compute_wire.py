"""Strict JSON wire codec for pure remote agent-summary compute."""

from __future__ import annotations

import json
from dataclasses import asdict
from decimal import Decimal, InvalidOperation
from typing import Any, Mapping

from shared.agent_summary_compute import (
    SummaryCompletion,
    SummaryCompletionRequest,
    SummaryComputeFailure,
    SummaryComputeInput,
    SummaryComputeResult,
    SummaryFields,
)


MAX_SUMMARY_COMPUTE_FRAME_BYTES = 8 * 1024 * 1024
_SUMMARY_FIELD_LIMITS = {
    "asked": 400,
    "did": 1200,
    "answered": 400,
    "confidence_reason": 500,
}


class SummaryComputeWireError(ValueError):
    """A remote summary frame does not satisfy the portable wire contract."""


def _json_bytes(value: Any, *, label: str) -> bytes:
    try:
        encoded = json.dumps(value, separators=(",", ":"), allow_nan=False).encode()
    except (TypeError, ValueError) as exc:
        raise SummaryComputeWireError(f"{label} must contain finite JSON values") from exc
    if len(encoded) > MAX_SUMMARY_COMPUTE_FRAME_BYTES:
        raise SummaryComputeWireError(f"{label} exceeds the {MAX_SUMMARY_COMPUTE_FRAME_BYTES} byte limit")
    return encoded


def _object(value: Any, *, label: str, fields: frozenset[str]) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or set(value) != fields:
        raise SummaryComputeWireError(f"{label} fields do not match the wire contract")
    return value


def _string(value: Any, *, label: str, nullable: bool = False) -> str | None:
    if value is None and nullable:
        return None
    if not isinstance(value, str):
        raise SummaryComputeWireError(f"{label} must be a string")
    return value


def _tokens(value: Any, *, label: str, nullable: bool = False) -> int | None:
    if value is None and nullable:
        return None
    if type(value) is not int or value < 0:
        raise SummaryComputeWireError(f"{label} must be a non-negative integer")
    return value


def _provider_cost(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value or value.strip() != value:
        raise SummaryComputeWireError("provider_cost must be a decimal string")
    try:
        decimal = Decimal(value)
    except (InvalidOperation, ValueError) as exc:
        raise SummaryComputeWireError("provider_cost must be a decimal string") from exc
    if not decimal.is_finite() or decimal < 0:
        raise SummaryComputeWireError("provider_cost must be a non-negative finite decimal string")
    return value


def summary_input_from_json(value: Any) -> SummaryComputeInput:
    """Reconstruct the exact portable summary input supplied by the API."""
    _json_bytes(value, label="Summary compute input")
    item = _object(
        value,
        label="Summary compute input",
        fields=frozenset({"agent_name", "agent_system_prompt", "run_input", "run_output", "model"}),
    )
    return SummaryComputeInput(
        agent_name=_string(item["agent_name"], label="agent_name") or "",
        agent_system_prompt=_string(item["agent_system_prompt"], label="agent_system_prompt") or "",
        run_input=item["run_input"],
        run_output=item["run_output"],
        model=_string(item["model"], label="model") or "",
    )


def summary_input_to_json(value: SummaryComputeInput) -> dict[str, Any]:
    """Serialize a summary input without changing its prompt-bearing values."""
    output = asdict(value)
    _json_bytes(output, label="Summary compute input")
    return output


def summary_completion_request_to_json(
    value: SummaryCompletionRequest,
) -> dict[str, Any]:
    """Serialize the one canonical request built by ``compute_summary``."""
    output = {
        "model": value.model,
        "messages": [asdict(message) for message in value.messages],
    }
    _json_bytes(output, label="Summary completion request")
    return output


def summary_completion_from_json(value: Any) -> SummaryCompletion:
    """Strictly reconstruct a provider-neutral completion callback result."""
    _json_bytes(value, label="Summary completion")
    item = _object(
        value,
        label="Summary completion",
        fields=frozenset({
            "content", "model", "input_tokens", "output_tokens",
            "cache_read_tokens", "cache_write_tokens", "reasoning_tokens", "provider_cost",
        }),
    )
    return SummaryCompletion(
        content=_string(item["content"], label="content", nullable=True),
        model=_string(item["model"], label="model", nullable=True),
        input_tokens=_tokens(item["input_tokens"], label="input_tokens", nullable=True),
        output_tokens=_tokens(item["output_tokens"], label="output_tokens", nullable=True),
        cache_read_tokens=_tokens(item["cache_read_tokens"], label="cache_read_tokens") or 0,
        cache_write_tokens=_tokens(item["cache_write_tokens"], label="cache_write_tokens") or 0,
        reasoning_tokens=_tokens(item["reasoning_tokens"], label="reasoning_tokens") or 0,
        provider_cost=_provider_cost(item["provider_cost"]),
    )


def summary_completion_to_json(value: SummaryCompletion) -> dict[str, Any]:
    """Serialize a validated completion for the API-owned callback boundary."""
    output = asdict(value)
    summary_completion_from_json(output)
    return output


def summary_compute_result_to_json(value: SummaryComputeResult) -> dict[str, Any]:
    """Return the bounded JSON-safe terminal payload for a compute child."""
    output = asdict(value)
    result = json.loads(_json_bytes(output, label="Summary compute result"))
    summary_compute_result_from_json(result)
    return result


def summary_compute_result_from_json(value: Any) -> SummaryComputeResult:
    """Reconstruct a terminal payload for the API persistence boundary."""
    _json_bytes(value, label="Summary compute result")
    item = _object(
        value,
        label="Summary compute result",
        fields=frozenset({"fields", "completion", "failure", "raw_content"}),
    )
    fields_value = item["fields"]
    fields: SummaryFields | None = None
    if fields_value is not None:
        field_item = _object(
            fields_value,
            label="Summary fields",
            fields=frozenset({"asked", "did", "answered", "confidence", "confidence_reason", "metadata"}),
        )
        confidence = field_item["confidence"]
        if confidence is not None and (type(confidence) not in (int, float) or isinstance(confidence, bool)):
            raise SummaryComputeWireError("confidence must be a finite number")
        if confidence is not None:
            _json_bytes(confidence, label="confidence")
            confidence = float(confidence)
            if not 0.0 <= confidence <= 1.0:
                raise SummaryComputeWireError("confidence must be between 0 and 1")
        metadata_value = field_item["metadata"]
        if metadata_value is not None:
            if not isinstance(metadata_value, Mapping) or not all(
                isinstance(key, str) and isinstance(item, str) and len(item) <= 256
                for key, item in metadata_value.items()
            ):
                raise SummaryComputeWireError("metadata must be a string map with canonical value limits")
            metadata = dict(metadata_value)
        else:
            metadata = None
        fields = SummaryFields(
            asked=_string(field_item["asked"], label="asked", nullable=True),
            did=_string(field_item["did"], label="did", nullable=True),
            answered=_string(field_item["answered"], label="answered", nullable=True),
            confidence=confidence,
            confidence_reason=_string(field_item["confidence_reason"], label="confidence_reason", nullable=True),
            metadata=metadata,
        )
        for name, limit in _SUMMARY_FIELD_LIMITS.items():
            field_value = getattr(fields, name)
            if field_value is not None and len(field_value) > limit:
                raise SummaryComputeWireError(f"{name} exceeds the canonical summary limit")
    completion = (
        summary_completion_from_json(item["completion"])
        if item["completion"] is not None
        else None
    )
    failure_value = item["failure"]
    failure: SummaryComputeFailure | None = None
    if failure_value is not None:
        failure_item = _object(
            failure_value,
            label="Summary compute failure",
            fields=frozenset({"kind", "error"}),
        )
        kind = _string(failure_item["kind"], label="failure kind")
        if kind not in {"empty_content", "invalid_json", "not_object", "provider"}:
            raise SummaryComputeWireError("Summary compute failure kind is invalid")
        failure = SummaryComputeFailure(
            kind=kind,  # pyright: ignore[reportArgumentType]
            error=_string(failure_item["error"], label="failure error") or "",
        )
    raw_content = _string(item["raw_content"], label="raw_content") or ""
    if (fields is None) == (failure is None):
        raise SummaryComputeWireError("Summary compute result must contain fields or failure, but not both")
    if fields is not None:
        if completion is None or raw_content != (completion.content or ""):
            raise SummaryComputeWireError("Successful summary result does not match its completion content")
    elif failure is not None and failure.kind == "provider":
        if completion is not None or raw_content:
            raise SummaryComputeWireError("Provider failure must not retain a completion payload")
    elif completion is None or raw_content != (completion.content or ""):
        raise SummaryComputeWireError("Summary failure does not match its completion content")
    return SummaryComputeResult(
        fields=fields,
        completion=completion,
        failure=failure,
        raw_content=raw_content,
    )
