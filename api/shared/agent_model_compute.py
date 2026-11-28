"""Credential-free PydanticAI model RPC primitives.

The worker owns an agent graph but never selects a provider or receives model
settings.  It sends a bounded, JSON-only model request to an API-owned callback;
the API callback supplies its already-authorized ``Model`` and settings.  This
module intentionally contains no HTTP, database, ORM, or provider imports.
"""

from __future__ import annotations

import importlib
import json
import sys
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from contextvars import ContextVar
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from functools import lru_cache
from typing import Any, cast

from pydantic import TypeAdapter, ValidationError
from pydantic_ai.messages import (
    FinalResultEvent,
    ModelMessage,
    ModelMessagesTypeAdapter,
    ModelResponse,
    ModelResponseStreamEvent,
)
from pydantic_ai.models import Model, ModelRequestParameters, StreamedResponse
from pydantic_ai.native_tools import SUPPORTED_NATIVE_TOOLS
from pydantic_ai.profiles import DEFAULT_PROFILE, ModelProfile
from pydantic_ai.settings import ModelSettings
from pydantic_ai.tools import RunContext
from pydantic_ai.usage import RequestUsage, RunUsage

WIRE_VERSION = 1
"""Version of this intentionally small, in-process-neutral RPC wire."""

MAX_WIRE_BYTES = 8 * 1024 * 1024
MAX_MESSAGES = 2_048
MAX_TOOLS = 512

_REQUEST_PARAMETER_FIELDS = frozenset(
    {
        "function_tools",
        "native_tools",
        "tool_visibility",
        "revealed_tool_names",
        "introduced_tool_names",
        "deferred_capability_ids",
        "deferred_capabilities",
        "output_mode",
        "output_object",
        "output_tools",
        "prompted_output_template",
        "allow_text_output",
        "allow_image_output",
        "instruction_parts",
        "instruction_visibility",
        "thinking",
        "cache",
    }
)
_MODEL_REQUEST_PARAMETERS = TypeAdapter(ModelRequestParameters)
_MODEL_RESPONSE = TypeAdapter(ModelResponse)
_STREAM_EVENT = TypeAdapter(ModelResponseStreamEvent)
_REQUEST_USAGE = TypeAdapter(RequestUsage)
_RUN_USAGE = TypeAdapter(RunUsage)
_model_output_cap: ContextVar[int | None] = ContextVar(
    "agent_model_compute_output_cap", default=None
)

_PROFILE_FIELDS = frozenset(DEFAULT_PROFILE)
_PROFILE_BOOL_FIELDS = frozenset(
    {
        "supports_tools",
        "supports_text_output",
        "supports_tool_return_schema",
        "supports_json_schema_output",
        "supports_json_object_output",
        "supports_image_output",
        "supports_audio_input",
        "supports_image_input",
        "supports_document_input",
        "supports_audio_file_input",
        "supports_video_input",
        "native_output_requires_schema_in_instructions",
        "supports_cache",
        "supports_auto_cache",
        "supports_thinking",
        "thinking_always_enabled",
        "thinking_enabled_by_default",
        "supports_forced_tool_choice",
        "supports_forced_tool_choice_with_thinking",
        "forced_tool_choice_disables_thinking",
        "may_tokenize_special_tokens_in_content",
        "mimics_assistant_message_formatting",
        "ignore_streamed_leading_whitespace",
    }
)
_PROFILE_OPTIONAL_INT_FIELDS = frozenset({"context_window", "tool_call_id_length"})
_NATIVE_TOOL_IDS: dict[type[object], str] = {
    tool: f"{tool.__module__}:{tool.__qualname__}" for tool in SUPPORTED_NATIVE_TOOLS
}
_NATIVE_TOOLS_BY_ID = {
    identifier: tool for tool, identifier in _NATIVE_TOOL_IDS.items()
}


# Constant wire IDs, never caller-supplied import paths. Keep provider SDKs out
# of API codec imports; physical templates explicitly preload them before ready.
_PROFILE_TRANSFORMER_PATHS = {
    "inline-defs": ("pydantic_ai._json_schema", "InlineDefsJsonSchemaTransformer"),
    "google": ("pydantic_ai.profiles.google", "GoogleJsonSchemaTransformer"),
    "google-openapi": ("pydantic_ai.profiles.google", "GoogleOpenAPISchemaTransformer"),
    "openai": ("pydantic_ai.profiles.openai", "OpenAIJsonSchemaTransformer"),
    "openai-gpt-oss-inline-defs": (
        "pydantic_ai.profiles.openai", "_GptOssInlineDefsJsonSchemaTransformer"
    ),
    "openai-gpt-oss": ("pydantic_ai.profiles.openai", "_GptOssJsonSchemaTransformer"),
    "openai-guarded": ("pydantic_ai.profiles.openai", "_OpenAIGuardedJsonSchemaTransformer"),
    "openai-internal": ("pydantic_ai.profiles.openai", "_OpenAIJsonSchemaTransformer"),
    "anthropic": ("pydantic_ai.providers.anthropic", "AnthropicJsonSchemaTransformer"),
}


def _profile_transformer_id(transformer: object) -> str | None:
    # Identity against loaded SDK classes prevents a class spoofing trusted
    # __module__/__qualname__ strings. Reverse order retains prior alias precedence.
    for identifier, (module_name, attribute) in reversed(
        _PROFILE_TRANSFORMER_PATHS.items()
    ):
        module = sys.modules.get(module_name)
        if module is not None and getattr(module, attribute, None) is transformer:
            return identifier
    return None


@lru_cache(maxsize=len(_PROFILE_TRANSFORMER_PATHS))
def _profile_transformer(identifier: str) -> type[object]:
    module_name, attribute = _PROFILE_TRANSFORMER_PATHS[identifier]
    return cast(type[object], getattr(importlib.import_module(module_name), attribute))


def preload_model_profile_transformers() -> None:
    """Warm every supported transformer before a physical template is measured."""
    for identifier in _PROFILE_TRANSFORMER_PATHS:
        _profile_transformer(identifier)


class ModelWireError(ValueError):
    """A model RPC payload is malformed, oversized, or outside the wire contract."""


RequestCallback = Callable[[bytes], Awaitable[bytes]]
StreamCallback = Callable[[bytes], AsyncIterator[bytes]]


def current_model_output_cap() -> int | None:
    """The dynamic worker cap in effect for the current API-owned model call."""

    return _model_output_cap.get()


@dataclass(frozen=True)
class ModelWireRequest:
    """A worker request plus its only permitted runtime setting override."""

    messages: list[ModelMessage]
    parameters: ModelRequestParameters
    max_tokens: int | None


def serialize_model_profile(profile: ModelProfile) -> bytes:
    """Encode a full, trusted PydanticAI profile for a physical worker bootstrap.

    ``Model.profile`` is already a fully-resolved provider-neutral mapping. The
    profile callable form is intentionally unsupported: callbacks can carry
    process-local code and are neither portable nor safe to deserialize.
    """

    resolved = dict(profile)
    if not _PROFILE_FIELDS.issubset(resolved):
        raise ModelWireError("model profile must be fully resolved before bootstrap")
    transformer = resolved["json_schema_transformer"]
    if transformer is None:
        transformer_id: str | None = None
    elif transformer_id := _profile_transformer_id(transformer):
        pass
    else:
        raise ModelWireError("model profile uses an unsupported schema transformer")
    native_tools = resolved["supported_native_tools"]
    if not isinstance(native_tools, frozenset):
        raise ModelWireError("model profile has invalid native tool definitions")
    try:
        native_tool_ids = sorted(_NATIVE_TOOL_IDS[tool] for tool in native_tools)
    except KeyError as exc:
        raise ModelWireError("model profile uses an unsupported native tool") from exc
    retention = resolved["default_cache_retention"]
    if retention is not None and not isinstance(retention, timedelta):
        raise ModelWireError("model profile has an invalid cache retention")
    wire_profile = {
        key: resolved[key]
        for key in _PROFILE_FIELDS
        if key
        not in {
            "json_schema_transformer",
            "supported_native_tools",
            "supported_document_media_types",
            "supported_cache_retentions",
            "default_cache_retention",
            "thinking_tags",
        }
    }
    wire_profile.update(
        {
            "json_schema_transformer": transformer_id,
            "supported_native_tools": native_tool_ids,
            "supported_document_media_types": sorted(
                resolved["supported_document_media_types"]
            ),
            "supported_cache_retentions": list(resolved["supported_cache_retentions"]),
            "default_cache_retention": (
                retention.total_seconds() if retention is not None else None
            ),
            "thinking_tags": list(resolved["thinking_tags"]),
        }
    )
    return _dump_json(
        {"version": WIRE_VERSION, "kind": "model-profile", "profile": wire_profile}
    )


def deserialize_model_profile(payload: bytes) -> ModelProfile:
    """Decode the explicit, callable-free profile bootstrap contract."""

    envelope = _load_json(payload)
    if not isinstance(envelope, dict) or set(envelope) != {
        "version",
        "kind",
        "profile",
    }:
        raise ModelWireError("model profile has unexpected fields")
    if envelope["version"] != WIRE_VERSION or envelope["kind"] != "model-profile":
        raise ModelWireError("unsupported model profile wire version or kind")
    profile = envelope["profile"]
    if not isinstance(profile, dict) or set(profile) != _PROFILE_FIELDS:
        raise ModelWireError("model profile has unexpected fields")
    for field in _PROFILE_BOOL_FIELDS:
        if type(profile[field]) is not bool:
            raise ModelWireError(f"model profile field {field} must be a boolean")
    if not isinstance(
        profile["supports_image_output"], bool
    ):  # pragma: no cover - set above
        raise ModelWireError("model profile has invalid image-output support")
    if not isinstance(profile["prompted_output_template"], str):
        raise ModelWireError("model profile has invalid prompted output template")
    if profile["default_structured_output_mode"] not in {
        "tool",
        "native",
        "prompted",
        "auto",
    }:
        raise ModelWireError("model profile has invalid structured output mode")
    if profile["tool_deferral_mode"] not in {None, "standalone", "with_tool_search"}:
        raise ModelWireError("model profile has invalid tool deferral mode")
    if profile["tool_addition_mode"] not in {None, "by_reference", "with_definitions"}:
        raise ModelWireError("model profile has invalid tool addition mode")
    for field in _PROFILE_OPTIONAL_INT_FIELDS:
        value = profile[field]
        if value is not None and (type(value) is not int or value < 0):
            raise ModelWireError(
                f"model profile field {field} must be a non-negative integer"
            )
    transformer_id = profile["json_schema_transformer"]
    if transformer_id is not None and transformer_id not in _PROFILE_TRANSFORMER_PATHS:
        raise ModelWireError("model profile has an unsupported schema transformer")
    native_tool_ids = profile["supported_native_tools"]
    if not isinstance(native_tool_ids, list) or not all(
        isinstance(identifier, str) and identifier in _NATIVE_TOOLS_BY_ID
        for identifier in native_tool_ids
    ):
        raise ModelWireError("model profile has unsupported native tools")
    document_types = profile["supported_document_media_types"]
    if not isinstance(document_types, list) or not all(
        isinstance(media_type, str) for media_type in document_types
    ):
        raise ModelWireError("model profile has invalid document media types")
    cache_retentions = profile["supported_cache_retentions"]
    if not isinstance(cache_retentions, list) or not all(
        retention in {"5m", "30m", "1h", "12h"} for retention in cache_retentions
    ):
        raise ModelWireError("model profile has invalid cache retentions")
    default_cache_retention = profile["default_cache_retention"]
    if default_cache_retention is not None and (
        type(default_cache_retention) not in {int, float} or default_cache_retention < 0
    ):
        raise ModelWireError("model profile has invalid default cache retention")
    thinking_tags = profile["thinking_tags"]
    if (
        not isinstance(thinking_tags, list)
        or len(thinking_tags) != 2
        or not all(isinstance(tag, str) for tag in thinking_tags)
    ):
        raise ModelWireError("model profile has invalid thinking tags")
    decoded = dict(profile)
    decoded["json_schema_transformer"] = (
        _profile_transformer(transformer_id) if transformer_id is not None else None
    )
    decoded["supported_native_tools"] = frozenset(
        _NATIVE_TOOLS_BY_ID[identifier] for identifier in native_tool_ids
    )
    decoded["supported_document_media_types"] = frozenset(document_types)
    decoded["supported_cache_retentions"] = tuple(cache_retentions)
    decoded["default_cache_retention"] = (
        timedelta(seconds=default_cache_retention)
        if default_cache_retention is not None
        else None
    )
    decoded["thinking_tags"] = tuple(thinking_tags)
    return cast(ModelProfile, decoded)


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ModelWireError(f"duplicate JSON field: {key}")
        result[key] = value
    return result


def _load_json(payload: bytes) -> Any:
    if not isinstance(payload, bytes):
        raise ModelWireError("wire payload must be bytes")
    if len(payload) > MAX_WIRE_BYTES:
        raise ModelWireError(f"wire payload exceeds {MAX_WIRE_BYTES} byte maximum")
    try:
        return json.loads(
            payload,
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=lambda value: (_ for _ in ()).throw(
                ModelWireError(f"invalid JSON constant: {value}")
            ),
        )
    except (TypeError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ModelWireError("invalid JSON wire payload") from exc


def _dump_json(value: Any) -> bytes:
    payload = json.dumps(value, separators=(",", ":"), ensure_ascii=False).encode()
    if len(payload) > MAX_WIRE_BYTES:
        raise ModelWireError(f"wire payload exceeds {MAX_WIRE_BYTES} byte maximum")
    return payload


def _validate_json_value(adapter: TypeAdapter[Any], value: Any) -> Any:
    try:
        # PydanticAI serializes timestamps as ISO-8601 strings.  Feed the
        # bounded JSON directly to Pydantic's JSON parser so those canonical
        # representations round-trip; strict Python validation treats them as
        # plain strings instead of datetimes.
        return adapter.validate_json(_dump_json(value), extra="forbid")
    except (ValidationError, ValueError, TypeError) as exc:
        raise ModelWireError("invalid typed wire payload") from exc


def _dump_typed(adapter: TypeAdapter[Any], value: Any) -> Any:
    try:
        return _load_json(adapter.dump_json(value))
    except (ValidationError, ValueError, TypeError) as exc:
        raise ModelWireError("wire payload is not JSON-safe") from exc


def serialize_run_usage(usage: RunUsage) -> dict[str, Any]:
    """Serialize native usage, including Decimal provider cost, as typed JSON."""
    return cast(dict[str, Any], _dump_typed(_RUN_USAGE, usage))


def deserialize_run_usage(value: Any) -> RunUsage:
    """Restore native usage field types before ledger arithmetic."""
    return _validate_json_value(_RUN_USAGE, value)


def _without_part_routing_fields(part: Any) -> Any:
    """Drop routing metadata from one serialized response part only."""

    if not isinstance(part, dict):
        return part
    return {key: value for key, value in part.items() if key != "provider_details"}


def _without_response_routing_fields(response: Any) -> Any:
    """Drop response routing metadata without walking user or tool payloads."""

    if not isinstance(response, dict):
        return response
    sanitized = {
        key: value
        for key, value in response.items()
        if key not in {"provider_details", "provider_url"}
    }
    if isinstance(parts := sanitized.get("parts"), list):
        sanitized["parts"] = [_without_part_routing_fields(part) for part in parts]
    return sanitized


def _without_message_routing_fields(messages: list[Any]) -> list[Any]:
    """Sanitize only typed response messages, preserving request/tool content verbatim."""

    return [
        _without_response_routing_fields(message)
        if isinstance(message, dict) and message.get("kind") == "response"
        else message
        for message in messages
    ]


def _without_stream_event_routing_fields(event: Any) -> Any:
    """Drop typed provider metadata from response stream parts and deltas only."""

    if not isinstance(event, dict):
        return event
    sanitized = dict(event)
    if sanitized.get("event_kind") in {"part_start", "part_end"}:
        sanitized["part"] = _without_part_routing_fields(sanitized.get("part"))
    elif sanitized.get("event_kind") == "part_delta":
        sanitized["delta"] = _without_part_routing_fields(sanitized.get("delta"))
    return sanitized


def _request_envelope(
    messages: list[ModelMessage], parameters: ModelRequestParameters
) -> dict[str, Any]:
    if parameters.native_tools:
        raise ModelWireError("native tools are not supported by the remote model wire")
    if len(messages) > MAX_MESSAGES:
        raise ModelWireError(f"wire request exceeds {MAX_MESSAGES} messages")
    if len(parameters.function_tools) + len(parameters.output_tools) > MAX_TOOLS:
        raise ModelWireError(f"wire request exceeds {MAX_TOOLS} tools")
    if len(parameters.deferred_capabilities) > MAX_TOOLS:
        raise ModelWireError(f"wire request exceeds {MAX_TOOLS} deferred capabilities")
    return {
        "version": WIRE_VERSION,
        "kind": "request",
        "messages": _without_message_routing_fields(
            _dump_typed(ModelMessagesTypeAdapter, messages)
        ),
        "parameters": _dump_typed(_MODEL_REQUEST_PARAMETERS, parameters),
    }


def serialize_model_request(
    messages: list[ModelMessage],
    parameters: ModelRequestParameters,
    *,
    max_tokens: int | None = None,
) -> bytes:
    """Serialize a bounded request with only a constrained output-token cap."""

    if max_tokens is not None and (type(max_tokens) is not int or max_tokens <= 0):
        raise ModelWireError("wire max_tokens must be a positive integer")
    return _dump_json(
        {**_request_envelope(messages, parameters), "max_tokens": max_tokens}
    )


def deserialize_model_request(
    payload: bytes,
) -> ModelWireRequest:
    """Strictly parse a worker request accepted by a trusted API model adapter."""

    envelope = _load_json(payload)
    if not isinstance(envelope, dict):
        raise ModelWireError("wire envelope must be an object")
    expected = {"version", "kind", "messages", "parameters", "max_tokens"}
    if set(envelope) != expected:
        raise ModelWireError("wire envelope has unexpected fields")
    if envelope["version"] != WIRE_VERSION or envelope["kind"] != "request":
        raise ModelWireError("unsupported model request wire version or kind")
    messages_raw = envelope["messages"]
    parameters_raw = envelope["parameters"]
    if not isinstance(messages_raw, list) or not isinstance(parameters_raw, dict):
        raise ModelWireError("wire request has invalid message or parameter shape")
    if len(messages_raw) > MAX_MESSAGES:
        raise ModelWireError(f"wire request exceeds {MAX_MESSAGES} messages")
    unexpected_parameter_fields = set(parameters_raw) - _REQUEST_PARAMETER_FIELDS
    if unexpected_parameter_fields:
        raise ModelWireError("wire request parameters have unexpected fields")
    if parameters_raw.get("native_tools"):
        raise ModelWireError("native tools are not supported by the remote model wire")
    deferred = parameters_raw.get("deferred_capabilities", [])
    if isinstance(deferred, list) and len(deferred) > MAX_TOOLS:
        raise ModelWireError(f"wire request exceeds {MAX_TOOLS} deferred capabilities")
    messages = _validate_json_value(ModelMessagesTypeAdapter, messages_raw)
    parameters = _validate_json_value(_MODEL_REQUEST_PARAMETERS, parameters_raw)
    if parameters.native_tools:
        raise ModelWireError("native tools are not supported by the remote model wire")
    if len(parameters.function_tools) + len(parameters.output_tools) > MAX_TOOLS:
        raise ModelWireError(f"wire request exceeds {MAX_TOOLS} tools")
    max_tokens = envelope["max_tokens"]
    if max_tokens is not None and (type(max_tokens) is not int or max_tokens <= 0):
        raise ModelWireError("wire max_tokens must be a positive integer")
    return ModelWireRequest(messages, parameters, max_tokens)


def _max_tokens_from_settings(model_settings: ModelSettings | None) -> int | None:
    """Extract the sole worker-owned setting that changes a request budget."""

    if model_settings is None:
        return None
    max_tokens = model_settings.get("max_tokens")
    if max_tokens is None:
        return None
    if type(max_tokens) is not int or max_tokens <= 0:
        raise ModelWireError("model_settings.max_tokens must be a positive integer")
    return max_tokens


def _trusted_settings_with_max_tokens(
    trusted_settings: ModelSettings | None, max_tokens: int | None
) -> ModelSettings | None:
    """Apply the worker cap without accepting arbitrary provider settings."""

    if max_tokens is None:
        return trusted_settings
    effective = dict(trusted_settings or {})
    trusted_cap = effective.get("max_tokens")
    if type(trusted_cap) is int and trusted_cap > 0:
        effective["max_tokens"] = min(trusted_cap, max_tokens)
    else:
        effective["max_tokens"] = max_tokens
    return cast(ModelSettings, effective)


def serialize_model_response(response: ModelResponse) -> bytes:
    """Serialize a response without provider URLs/details, retaining exact usage."""

    return _dump_json(
        {
            "version": WIRE_VERSION,
            "kind": "response",
            "response": _without_response_routing_fields(
                _dump_typed(_MODEL_RESPONSE, response)
            ),
        }
    )


def deserialize_model_response(payload: bytes) -> ModelResponse:
    """Strictly parse an API response sent back to a worker."""

    envelope = _load_json(payload)
    if not isinstance(envelope, dict) or set(envelope) != {
        "version",
        "kind",
        "response",
    }:
        raise ModelWireError("wire response has unexpected fields")
    if envelope["version"] != WIRE_VERSION or envelope["kind"] != "response":
        raise ModelWireError("unsupported model response wire version or kind")
    return _validate_json_value(_MODEL_RESPONSE, envelope["response"])


def _serialize_usage(usage: RequestUsage) -> bytes:
    return _dump_json(
        {
            "version": WIRE_VERSION,
            "kind": "usage",
            "usage": _dump_typed(_REQUEST_USAGE, usage),
        }
    )


def _deserialize_usage(payload: bytes) -> RequestUsage:
    envelope = _load_json(payload)
    if not isinstance(envelope, dict) or set(envelope) != {"version", "kind", "usage"}:
        raise ModelWireError("wire usage has unexpected fields")
    if envelope["version"] != WIRE_VERSION or envelope["kind"] != "usage":
        raise ModelWireError("unsupported model usage wire version or kind")
    return _validate_json_value(_REQUEST_USAGE, envelope["usage"])


def _serialize_stream_event(event: ModelResponseStreamEvent) -> bytes:
    return _dump_json(
        {
            "version": WIRE_VERSION,
            "kind": "stream-event",
            "event": _without_stream_event_routing_fields(
                _dump_typed(_STREAM_EVENT, event)
            ),
        }
    )


def _serialize_stream_final(response: ModelResponse) -> bytes:
    return _dump_json(
        {
            "version": WIRE_VERSION,
            "kind": "stream-final",
            "response": _without_response_routing_fields(
                _dump_typed(_MODEL_RESPONSE, response)
            ),
        }
    )


def deserialize_model_stream_frame(
    payload: bytes,
) -> tuple[str, ModelResponseStreamEvent | ModelResponse]:
    envelope = _load_json(payload)
    if not isinstance(envelope, dict) or envelope.get("version") != WIRE_VERSION:
        raise ModelWireError("unsupported model stream wire version")
    kind = envelope.get("kind")
    if kind == "stream-event" and set(envelope) == {"version", "kind", "event"}:
        return kind, _validate_json_value(_STREAM_EVENT, envelope["event"])
    if kind == "stream-final" and set(envelope) == {"version", "kind", "response"}:
        return kind, _validate_json_value(_MODEL_RESPONSE, envelope["response"])
    raise ModelWireError("invalid model stream frame")


async def serve_model_request(
    model: Model,
    trusted_settings: ModelSettings | None,
    payload: bytes,
) -> bytes:
    """Run a worker request through an already-authorized API-owned model."""

    request = deserialize_model_request(payload)
    token = _model_output_cap.set(request.max_tokens)
    try:
        return serialize_model_response(
            await model.request(
                request.messages,
                _trusted_settings_with_max_tokens(trusted_settings, request.max_tokens),
                request.parameters,
            )
        )
    finally:
        _model_output_cap.reset(token)


async def serve_model_count_tokens(
    model: Model,
    trusted_settings: ModelSettings | None,
    payload: bytes,
) -> bytes:
    """Delegate pre-request counting to the same trusted API model."""

    request = deserialize_model_request(payload)
    token = _model_output_cap.set(request.max_tokens)
    try:
        return _serialize_usage(
            await model.count_tokens(
                request.messages,
                _trusted_settings_with_max_tokens(trusted_settings, request.max_tokens),
                request.parameters,
            )
        )
    finally:
        _model_output_cap.reset(token)


async def serve_model_stream(
    model: Model,
    trusted_settings: ModelSettings | None,
    payload: bytes,
    *,
    run_context: RunContext[object] | None = None,
) -> AsyncIterator[bytes]:
    """Stream real PydanticAI events and one terminal response from the trusted model.

    ``run_context`` belongs to the API adapter.  It intentionally never crosses
    the worker wire, preserving server-side run/conversation identity.
    """

    request = deserialize_model_request(payload)
    token = _model_output_cap.set(request.max_tokens)
    try:
        async with model.request_stream(
            request.messages,
            _trusted_settings_with_max_tokens(trusted_settings, request.max_tokens),
            request.parameters,
            run_context,
        ) as stream:
            async for event in stream:
                yield _serialize_stream_event(event)
            yield _serialize_stream_final(stream.get())
    finally:
        _model_output_cap.reset(token)


class _RemoteStreamedResponse(StreamedResponse):
    """A live stream that applies API-produced events without synthesizing deltas."""

    def __init__(
        self,
        *,
        model_request_parameters: ModelRequestParameters,
        fallback_model_name: str,
        source: AsyncIterator[bytes],
    ) -> None:
        super().__init__(model_request_parameters)
        self._fallback_model_name = fallback_model_name
        self._source = source
        self._timestamp = datetime.now().astimezone()
        self._final_response: ModelResponse | None = None

    def __aiter__(self) -> AsyncIterator[ModelResponseStreamEvent]:
        if self._event_iterator is None:
            self._event_iterator = self._get_event_iterator()
        return self._event_iterator

    async def _get_event_iterator(self) -> AsyncIterator[ModelResponseStreamEvent]:
        async for payload in self._source:
            kind, frame = deserialize_model_stream_frame(payload)
            if kind == "stream-event":
                assert not isinstance(frame, ModelResponse)
                self._parts_manager.apply_event(frame)
                if isinstance(frame, FinalResultEvent):
                    self.final_result_event = frame
                if self._first_chunk_monotonic is None:
                    self._first_chunk_monotonic = time.perf_counter()
                yield frame
                continue
            assert isinstance(frame, ModelResponse)
            if self._final_response is not None:
                raise ModelWireError("model stream has more than one final response")
            self._final_response = frame
            self._usage = frame.usage
            self._usage_received = True
            self.provider_response_id = frame.provider_response_id
            self.provider_details = frame.provider_details
            self.canonical_system = frame.canonical_system
            self.finish_reason = frame.finish_reason
            self.state = frame.state
            self.metadata = frame.metadata
            self.failed_attempts = frame.failed_attempts
        if self._final_response is None:
            raise ModelWireError("model stream ended without a final response")
        self._finished = True

    async def close_stream(self) -> None:
        aclose = getattr(self._source, "aclose", None)
        if aclose is not None:
            await aclose()

    def get(self) -> ModelResponse:
        if self._final_response is None:
            return super().get()
        state = "complete" if self._finished else "incomplete"
        if self.cancelled and not self._finished:
            state = "interrupted"
        if self._final_response.state == "suspended":
            state = "suspended"
        return replace(
            self._final_response,
            parts=self._parts_manager.get_parts(),
            state=state,
        )

    @property
    def usage(self) -> RequestUsage:
        return self._final_response.usage if self._final_response else self._usage

    @property
    def model_name(self) -> str:
        return (
            self._final_response and self._final_response.model_name
        ) or self._fallback_model_name

    @property
    def provider_name(self) -> str | None:
        return self._final_response.provider_name if self._final_response else None

    @property
    def provider_url(self) -> str | None:
        return None

    @property
    def timestamp(self) -> datetime:
        return (
            self._final_response.timestamp if self._final_response else self._timestamp
        )


class RemoteModel(Model):
    """PydanticAI model façade whose provider calls are injected RPC callbacks."""

    def __init__(
        self,
        *,
        model_name: str,
        request: RequestCallback,
        request_stream: StreamCallback,
        count_tokens: RequestCallback,
        system: str = "remote",
        profile_payload: bytes,
    ) -> None:
        # This trusted bootstrap is JSON-only. It carries provider-neutral
        # capabilities, never a provider selector, credential, URL, or callback.
        super().__init__(profile=deserialize_model_profile(profile_payload))
        self._model_name = model_name
        self._request_callback = request
        self._stream_callback = request_stream
        self._count_tokens_callback = count_tokens
        self._system = system

    @property
    def model_name(self) -> str:
        return self._model_name

    @property
    def system(self) -> str:
        return self._system

    async def request(
        self,
        messages: list[ModelMessage],
        model_settings: ModelSettings | None,
        model_request_parameters: ModelRequestParameters,
    ) -> ModelResponse:
        payload = serialize_model_request(
            messages,
            model_request_parameters,
            max_tokens=_max_tokens_from_settings(model_settings),
        )
        return deserialize_model_response(await self._request_callback(payload))

    async def count_tokens(
        self,
        messages: list[ModelMessage],
        model_settings: ModelSettings | None,
        model_request_parameters: ModelRequestParameters,
    ) -> RequestUsage:
        payload = serialize_model_request(
            messages,
            model_request_parameters,
            max_tokens=_max_tokens_from_settings(model_settings),
        )
        return _deserialize_usage(await self._count_tokens_callback(payload))

    @asynccontextmanager
    async def request_stream(
        self,
        messages: list[ModelMessage],
        model_settings: ModelSettings | None,
        model_request_parameters: ModelRequestParameters,
        run_context: RunContext[object] | None = None,
    ) -> AsyncIterator[StreamedResponse]:
        del run_context
        payload = serialize_model_request(
            messages,
            model_request_parameters,
            max_tokens=_max_tokens_from_settings(model_settings),
        )
        yield _RemoteStreamedResponse(
            model_request_parameters=model_request_parameters,
            fallback_model_name=self.model_name,
            source=self._stream_callback(payload),
        )
