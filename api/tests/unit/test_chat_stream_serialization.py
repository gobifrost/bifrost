"""Regression coverage for JSON-safe chat stream events."""

from src.models.contracts.agents import ChatStreamChunk
from src.models.contracts.artifacts import ArtifactRef


def test_artifact_ready_chunk_dumps_portable_reference() -> None:
    chunk = ChatStreamChunk(
        type="artifact_ready",
        message_id="message-1",
        artifact=ArtifactRef(
            id="attachment-1",
            filename="Welcome Page.html",
            content_type="text/html",
            size_bytes=1024,
        ),
    )

    payload = chunk.model_dump(mode="json", exclude_none=True)

    assert payload["artifact"] == {
        "type": "bifrost_artifact",
        "id": "attachment-1",
        "filename": "Welcome Page.html",
        "content_type": "text/html",
        "size_bytes": 1024,
    }
