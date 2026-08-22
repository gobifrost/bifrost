from contextlib import asynccontextmanager
from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest

from shared.artifact_generation import GeneratedArtifact
from src.jobs.platform.registry import get_platform_job_definition
from src.jobs.platform.video_generation import (
    SDK_VIDEO_GENERATION_DEFINITION,
    VIDEO_GENERATION_DEFINITION,
    SDKVideoGenerationPayload,
    VideoGenerationPayload,
    run_video_generation,
)


def test_video_generation_jobs_are_registered_with_encrypted_non_retrying_payloads() -> None:
    assert (
        get_platform_job_definition(VIDEO_GENERATION_DEFINITION.job_type)
        is VIDEO_GENERATION_DEFINITION
    )
    assert (
        get_platform_job_definition(SDK_VIDEO_GENERATION_DEFINITION.job_type)
        is SDK_VIDEO_GENERATION_DEFINITION
    )
    assert VIDEO_GENERATION_DEFINITION.payload_model is VideoGenerationPayload
    assert SDK_VIDEO_GENERATION_DEFINITION.payload_model is SDKVideoGenerationPayload
    assert VIDEO_GENERATION_DEFINITION.encrypt_payload is True
    assert VIDEO_GENERATION_DEFINITION.policy.max_attempts == 1
    assert VIDEO_GENERATION_DEFINITION.policy.retry_on_runner_loss is False


@pytest.mark.asyncio
async def test_chat_video_job_persists_completed_tool_state(monkeypatch) -> None:
    conversation_id = uuid4()
    message_id = uuid4()
    user_id = uuid4()
    attachment_id = uuid4()
    conversation = SimpleNamespace(id=conversation_id, user_id=user_id)
    message = SimpleNamespace(
        id=message_id,
        conversation_id=conversation_id,
        tool_state="running",
        tool_result=None,
    )

    class FakeDB:
        async def get(self, model, entity_id):
            if model.__name__ == "Conversation" and entity_id == conversation_id:
                return conversation
            if model.__name__ == "Message" and entity_id == message_id:
                return message
            return None

        async def commit(self) -> None:
            return None

    @asynccontextmanager
    async def fake_db_context():
        yield FakeDB()

    async def fake_generate(*args, **kwargs) -> GeneratedArtifact:
        return GeneratedArtifact(
            filename="Product Tour.mp4",
            content_type="video/mp4",
            content=b"video",
        )

    class FakeAttachmentService:
        def __init__(self, db) -> None:
            pass

        async def store_generated(self, **kwargs):
            stored_artifact = SimpleNamespace(
                id=attachment_id,
                filename=kwargs["filename"],
                content_type=kwargs["content_type"],
                size_bytes=len(kwargs["content"]),
            )
            return SimpleNamespace(
                id=attachment_id,
                artifact=stored_artifact,
                filename=kwargs["filename"],
                content_type=kwargs["content_type"],
                size_bytes=len(kwargs["content"]),
                conversation_id=kwargs["conversation_id"],
                created_at=datetime.now(timezone.utc),
            )

    monkeypatch.setattr("src.core.database.get_db_context", fake_db_context)
    monkeypatch.setattr(
        "src.services.media_generation.get_media_provider_config",
        lambda *args, **kwargs: _async_value(SimpleNamespace()),
    )
    monkeypatch.setattr(
        "src.services.media_generation.generate_video_with_config",
        fake_generate,
    )
    monkeypatch.setattr(
        "src.services.chat_attachments.ChatAttachmentService",
        FakeAttachmentService,
    )

    context = SimpleNamespace(
        job_id=uuid4(),
        requested_by_user_id=str(user_id),
        organization_id=uuid4(),
    )
    result = await run_video_generation(
        context,
        VideoGenerationPayload(
            conversation_id=conversation_id,
            message_id=message_id,
            filename="Product Tour",
            prompt="Show the product in use.",
        ),
    )

    assert message.tool_state == "completed"
    assert message.tool_result["status"] == "succeeded"
    artifact = result["artifact"]
    assert isinstance(artifact, dict)
    assert artifact["id"] == str(attachment_id)


async def _async_value(value):
    return value
