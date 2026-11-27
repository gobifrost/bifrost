"""Pure compute contract for post-run agent summaries.

This module deliberately has no platform imports.  The API-side caller owns
loading a run, resolving its authorized model client, and persisting the
result.  A remote compute caller can supply the same serializable input and a
scoped completion callback without receiving database, Redis, storage, or
provider configuration access.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Literal


# Bump this whenever the prompt or user-message payload changes meaningfully,
# so callers can use it to select runs for summary backfills.
SUMMARIZE_PROMPT_VERSION = "v4"

SUMMARIZE_SYSTEM_PROMPT = """You summarize what an AI agent did on a single run.

You receive the agent's name, the system prompt that defines its job, the
run's input, and the run's output. Produce a JSON object with:

  - asked: one short sentence (<100 chars) describing what the user or event
    asked for, in the user's voice. Extract the specific ask, not a generic
    restatement of the agent's purpose.

  - did: a short prose explanation of how the agent worked through the
    request — the way someone would describe their work to a coworker. 1-4
    sentences (<800 chars total). Walk through the meaningful decisions
    and the reason behind each one ("I needed X, so I called Y; that gave
    me Z, which told me to..."). Skip filler tool calls (a couple of
    look-ups aren't worth narrating). DO NOT restate the agent's role or
    purpose — describe THIS run.

    *** TOOL MARKERS — STRICT RULE ***
    If the run made any tool calls (visible in the input as
    tool_call/tool_response entries, or implied by the agent's output),
    EVERY tool name you mention in `did` MUST be wrapped in square
    brackets. Use the exact machine-readable tool name from the run's
    tool_call steps (e.g. `ai_ticketing_get_ticket_details`,
    `delegate_to_security_subagent`), NOT a friendly paraphrase. The
    brackets are required syntax — the UI renders them as clickable chips.

    GOOD example (markers present, tool names exact):
      "I pulled the ticket via [ai_ticketing_get_ticket_details], saw the
      EOL alert came from a Windows 2012 R2 host, then delegated to
      [delegate_to_security_subagent] which recommended an in-place
      upgrade. Wrote the categorization back with
      [ai_ticketing_update_ticket]."

    BAD example (markers MISSING — do not do this):
      "I pulled the ticket details, saw the alert came from a 2012 R2
      host, then asked the security sub-agent for guidance and updated
      the ticket."

    If no tools were called on this run, write `did` as plain prose with
    no brackets. Brackets are required ONLY when tools were actually used.

  - answered: one short sentence (<100 chars) capturing the agent's final
    answer or outcome — the user-facing result of the run. Different from
    `did`: `did` is the work, `answered` is the result.

  - confidence: float 0.0-1.0 — how confident the agent's output appears to
    be.
  - confidence_reason: one sentence explaining the confidence assessment.
  - metadata: object of k/v pairs (string -> string) extracting notable
    entities from the run — the specific decisions, IDs, customer names,
    categories, severity levels, billing status, and so on. Max 8 entries.

Return a single JSON object and nothing else. Do not wrap it in markdown code
fences. Do not add a preamble, trailing prose, or explanation. The first
character of your response must be `{` and the last must be `}`."""


@dataclass(frozen=True)
class SummaryMessage:
    """A provider-neutral summary prompt message."""

    role: Literal["system", "user"]
    content: str


@dataclass(frozen=True)
class SummaryComputeInput:
    """Serializable source snapshot for one summary request."""

    agent_name: str
    agent_system_prompt: str
    run_input: Any
    run_output: Any
    model: str


@dataclass(frozen=True)
class SummaryCompletionRequest:
    """The exact one-shot request a caller must complete."""

    model: str
    messages: tuple[SummaryMessage, ...]


@dataclass(frozen=True)
class SummaryCompletion:
    """Serializable completion data returned by the caller's model boundary.

    ``provider_cost`` is a decimal string so transport callers do not lose
    precision; the persistence boundary restores its native decimal type.
    """

    content: str | None = None
    model: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    reasoning_tokens: int = 0
    provider_cost: str | None = None


@dataclass(frozen=True)
class SummaryFields:
    """Validated values suitable for persistence by the platform caller."""

    asked: str | None
    did: str | None
    answered: str | None
    confidence: float | None
    confidence_reason: str | None
    # ``None`` preserves the historical distinction between an invalid
    # metadata shape (leave existing metadata untouched) and an empty object.
    metadata: dict[str, str] | None


@dataclass(frozen=True)
class SummaryComputeFailure:
    kind: Literal["empty_content", "invalid_json", "not_object", "provider"]
    error: str


@dataclass(frozen=True)
class SummaryComputeResult:
    """Serializable success or failure of one completion attempt."""

    fields: SummaryFields | None
    completion: SummaryCompletion | None
    failure: SummaryComputeFailure | None
    raw_content: str = ""


SummaryCompletionCallback = Callable[
    [SummaryCompletionRequest], Awaitable[SummaryCompletion]
]


def build_summary_completion_request(
    summary_input: SummaryComputeInput,
) -> SummaryCompletionRequest:
    """Build the stable provider-neutral prompt for a source snapshot."""
    user_content = json.dumps(
        {
            "agent_name": summary_input.agent_name,
            "agent_system_prompt": summary_input.agent_system_prompt,
            "input": summary_input.run_input,
            "output": summary_input.run_output,
        },
        default=str,
    )
    return SummaryCompletionRequest(
        model=summary_input.model,
        messages=(
            SummaryMessage(role="system", content=SUMMARIZE_SYSTEM_PROMPT),
            SummaryMessage(role="user", content=user_content),
        ),
    )


async def compute_summary(
    summary_input: SummaryComputeInput,
    complete: SummaryCompletionCallback,
) -> SummaryComputeResult:
    """Complete and parse one summary request without platform side effects."""
    try:
        completion = await complete(build_summary_completion_request(summary_input))
    except Exception as exc:
        return SummaryComputeResult(
            fields=None,
            completion=None,
            failure=SummaryComputeFailure(
                kind="provider",
                error=(
                    f"LLM provider request failed ({type(exc).__name__}): "
                    f"{str(exc)[:160]}"
                ),
            ),
        )

    raw_content = completion.content or ""
    if not raw_content.strip():
        return SummaryComputeResult(
            fields=None,
            completion=completion,
            failure=SummaryComputeFailure(
                kind="empty_content",
                error=(
                    "Summarization model returned empty content. "
                    "Check model output filtering / reasoning-token budget."
                ),
            ),
            raw_content=raw_content,
        )

    try:
        parsed = json.loads(extract_json_object(raw_content))
    except json.JSONDecodeError as exc:
        raw_preview = raw_content[:500]
        looks_truncated = (
            raw_preview.lstrip().startswith("{")
            and not raw_preview.rstrip().endswith("}")
        )
        error = (
            "Summarization model response was truncated mid-object "
            "(token budget exhausted). Retry or reduce run payload."
            if looks_truncated
            else f"Invalid JSON from summarization model: {str(exc)[:200]}"
        )
        return SummaryComputeResult(
            fields=None,
            completion=completion,
            failure=SummaryComputeFailure(kind="invalid_json", error=error),
            raw_content=raw_content,
        )

    if not isinstance(parsed, dict):
        return SummaryComputeResult(
            fields=None,
            completion=completion,
            failure=SummaryComputeFailure(
                kind="not_object",
                error="Summarization model did not return a JSON object",
            ),
            raw_content=raw_content,
        )

    raw_metadata = parsed.get("metadata") or {}
    metadata = (
        {
            str(key): str(value)[:256]
            for key, value in raw_metadata.items()
            if isinstance(value, (str, int, float))
        }
        if isinstance(raw_metadata, dict)
        else None
    )
    return SummaryComputeResult(
        fields=SummaryFields(
            asked=truncate(parsed.get("asked"), 400),
            did=truncate(parsed.get("did"), 1200),
            answered=truncate(parsed.get("answered"), 400),
            confidence=clamp_confidence(parsed.get("confidence")),
            confidence_reason=truncate(parsed.get("confidence_reason"), 500),
            metadata=metadata,
        ),
        completion=completion,
        failure=None,
        raw_content=raw_content,
    )


def extract_json_object(text: str) -> str:
    """Best-effort extraction of a JSON object from an LLM response."""
    value = (text or "").strip()
    if not value:
        return value

    if value.startswith("```"):
        first_newline = value.find("\n")
        if first_newline != -1:
            value = value[first_newline + 1 :]
        if value.rstrip().endswith("```"):
            value = value.rstrip()[:-3].rstrip()

    start = value.find("{")
    if start == -1:
        return value
    depth = 0
    in_string = False
    escaped = False
    for index in range(start, len(value)):
        character = value[index]
        if escaped:
            escaped = False
            continue
        if in_string:
            if character == "\\":
                escaped = True
            elif character == '"':
                in_string = False
            continue
        if character == '"':
            in_string = True
        elif character == "{":
            depth += 1
        elif character == "}":
            depth -= 1
            if depth == 0:
                return value[start : index + 1]
    return value[start:]


def clamp_confidence(value: Any) -> float | None:
    """Clamp an LLM-returned confidence to [0.0, 1.0]."""
    if value is None:
        return None
    try:
        confidence = float(value)
    except (TypeError, ValueError):
        return None
    return max(0.0, min(1.0, confidence))


def truncate(value: Any, max_len: int) -> str | None:
    """Coerce to a non-empty truncated string."""
    if value is None:
        return None
    text = str(value)[:max_len]
    return text or None
