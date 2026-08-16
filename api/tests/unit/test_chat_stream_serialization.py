"""Regression coverage for JSON-safe chat stream events."""

from datetime import UTC, datetime

from src.models.contracts.agents import ChatStreamChunk
from src.models.contracts.artifacts import ArtifactRef


def test_artifact_ready_chunk_dumps_datetime_as_json_string() -> None:
    created_at = datetime(2026, 8, 15, 14, 30, tzinfo=UTC)
    chunk = ChatStreamChunk(
        type="artifact_ready",
        message_id="message-1",
        artifact=ArtifactRef(
            filename="Welcome Page.html",
            content_type="text/html",
            size_bytes=1024,
            attachment_id="attachment-1",
            created_at=created_at,
        ),
    )

    payload = chunk.model_dump(mode="json", exclude_none=True)

    assert payload["artifact"]["created_at"] == "2026-08-15T14:30:00Z"
