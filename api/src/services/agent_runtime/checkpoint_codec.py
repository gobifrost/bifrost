"""Versioned codec for checkpointing Pydantic AI message history.

The durable runtime persists normalized model messages at every committed
model/tool boundary so another worker can resume the same Bifrost AgentRun
after a lease expires. This module is the only place that knows how those
messages are serialized; a future Pydantic AI upgrade should change this
module (and its version) rather than every reader/writer of checkpoints.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from pydantic_ai.messages import ModelMessage, ModelMessagesTypeAdapter

CHECKPOINT_MESSAGE_FORMAT_VERSION = 1
"""Runtime format version stamped on every checkpoint payload."""


class CheckpointDecodeError(ValueError):
    """Stored checkpoint payload cannot be resumed by this runtime version."""


def encode_messages(messages: Sequence[ModelMessage]) -> dict[str, Any]:
    """Serialize model messages into a versioned JSON-compatible payload."""
    return {
        "format_version": CHECKPOINT_MESSAGE_FORMAT_VERSION,
        "messages": ModelMessagesTypeAdapter.dump_python(
            list(messages), mode="json"
        ),
    }


def decode_messages(payload: dict[str, Any]) -> list[ModelMessage]:
    """Deserialize a checkpoint payload back into model messages.

    Raises:
        CheckpointDecodeError: payload version is unknown or the stored
            messages fail validation. Callers must surface this as a
            recovery problem, never silently restart the run.
    """
    version = payload.get("format_version")
    if version != CHECKPOINT_MESSAGE_FORMAT_VERSION:
        raise CheckpointDecodeError(
            f"Unsupported checkpoint format version: {version!r} "
            f"(runtime supports {CHECKPOINT_MESSAGE_FORMAT_VERSION})"
        )
    try:
        return list(
            ModelMessagesTypeAdapter.validate_python(payload.get("messages"))
        )
    except Exception as exc:
        raise CheckpointDecodeError(
            f"Stored checkpoint messages failed validation: {exc}"
        ) from exc
