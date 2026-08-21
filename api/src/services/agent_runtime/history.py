"""Provider-neutral persisted-message replay shared by local and remote agents."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, Protocol
from uuid import UUID

from src.services.llm.base import LLMInputFile, LLMMessage, ToolCallRequest


class PersistedMessage(Protocol):
    """The small persisted-message surface required for runtime replay."""

    id: UUID
    role: Any
    content: str | None
    sequence: int
    tool_calls: list[dict[str, Any]] | None
    tool_call_id: str | None
    tool_name: str | None
    tool_input: dict[str, Any] | None


UserInput = tuple[str | None, list[LLMInputFile]]


def _role_value(message: PersistedMessage) -> str:
    role = message.role
    value = getattr(role, "value", role)
    return str(value)


def build_runtime_message_history(
    *,
    system_prompt: str,
    persisted_messages: Sequence[PersistedMessage],
    user_inputs: Mapping[UUID, UserInput] | None = None,
) -> list[LLMMessage]:
    """Convert durable Chat rows into the one canonical runtime history shape."""

    messages = [LLMMessage(role="system", content=system_prompt)]
    inputs = user_inputs or {}
    seen_tool_call_ids: dict[str, int] = {}
    tool_call_id_remap: dict[tuple[int, str], str] = {}

    for message in persisted_messages:
        role = _role_value(message)
        if role == "user":
            content, input_files = inputs.get(
                message.id,
                (message.content, []),
            )
            messages.append(
                LLMMessage(
                    role="user",
                    content=content,
                    input_files=input_files,
                )
            )
            continue

        if role == "assistant":
            calls = None
            if message.tool_calls:
                calls = []
                for raw_call in message.tool_calls:
                    original_id = str(raw_call["id"])
                    call_id = original_id
                    if call_id in seen_tool_call_ids:
                        seen_tool_call_ids[call_id] += 1
                        call_id = f"{call_id}_t{seen_tool_call_ids[call_id]}"
                        tool_call_id_remap[(message.sequence, original_id)] = call_id
                    else:
                        seen_tool_call_ids[call_id] = 1
                    calls.append(
                        ToolCallRequest(
                            id=call_id,
                            name=str(raw_call["name"]),
                            arguments=(
                                raw_call.get("arguments", {})
                                if isinstance(raw_call.get("arguments", {}), dict)
                                else {}
                            ),
                        )
                    )
            messages.append(
                LLMMessage(
                    role="assistant",
                    content=message.content,
                    tool_calls=calls,
                )
            )
            continue

        if role == "tool_call":
            original_id = message.tool_call_id or ""
            call_id = original_id
            if call_id in seen_tool_call_ids:
                seen_tool_call_ids[call_id] += 1
                call_id = f"{call_id}_t{seen_tool_call_ids[call_id]}"
                tool_call_id_remap[(message.sequence, original_id)] = call_id
            else:
                seen_tool_call_ids[call_id] = 1
            request = ToolCallRequest(
                id=call_id,
                name=message.tool_name or "",
                arguments=(message.tool_input if isinstance(message.tool_input, dict) else {}),
            )
            for prior in reversed(messages):
                if prior.role == "assistant":
                    if prior.tool_calls is None:
                        prior.tool_calls = []
                    prior.tool_calls.append(request)
                    break
            else:
                messages.append(
                    LLMMessage(role="assistant", content=None, tool_calls=[request])
                )
            continue

        if role == "tool":
            call_id = message.tool_call_id
            if call_id:
                best_sequence = -1
                for (sequence, original_id), remapped in tool_call_id_remap.items():
                    if (
                        original_id == call_id
                        and sequence < message.sequence
                        and sequence > best_sequence
                    ):
                        best_sequence = sequence
                        call_id = remapped
            messages.append(
                LLMMessage(
                    role="tool",
                    content=message.content,
                    tool_call_id=call_id,
                    tool_name=message.tool_name,
                )
            )

    return fix_dangling_tool_calls(fix_interleaved_messages(messages))


def fix_interleaved_messages(messages: Sequence[LLMMessage]) -> list[LLMMessage]:
    """Move user messages after the tool results required by providers."""

    result = list(messages)
    index = 0
    while index < len(result):
        message = result[index]
        if message.role == "assistant" and message.tool_calls:
            expected_ids = {call.id for call in message.tool_calls}
            cursor = index + 1
            tool_results: list[LLMMessage] = []
            displaced: list[LLMMessage] = []
            while cursor < len(result):
                call_id = result[cursor].tool_call_id
                if (
                    result[cursor].role == "tool"
                    and call_id
                    and call_id in expected_ids
                ):
                    tool_results.append(result[cursor])
                    expected_ids.discard(call_id)
                elif not expected_ids:
                    break
                else:
                    displaced.append(result[cursor])
                cursor += 1
            if displaced and tool_results:
                result[index + 1 : cursor] = tool_results + displaced
        index += 1
    return result


def fix_dangling_tool_calls(messages: Sequence[LLMMessage]) -> list[LLMMessage]:
    """Inject an interrupted result for every persisted dangling tool call."""

    result = list(messages)
    index = 0
    while index < len(result):
        message = result[index]
        if message.role == "assistant" and message.tool_calls:
            expected_ids = {call.id for call in message.tool_calls}
            cursor = index + 1
            found_ids: set[str] = set()
            while cursor < len(result) and result[cursor].role == "tool":
                if result[cursor].tool_call_id:
                    found_ids.add(result[cursor].tool_call_id)
                cursor += 1
            for call_id in expected_ids - found_ids:
                call = next(call for call in message.tool_calls if call.id == call_id)
                result.insert(
                    cursor,
                    LLMMessage(
                        role="tool",
                        content="[Tool execution was interrupted]",
                        tool_call_id=call_id,
                        tool_name=call.name,
                    ),
                )
                cursor += 1
        index += 1
    return result


__all__ = [
    "PersistedMessage",
    "UserInput",
    "build_runtime_message_history",
    "fix_dangling_tool_calls",
    "fix_interleaved_messages",
]
