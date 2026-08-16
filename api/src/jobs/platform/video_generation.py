"""Durable Chat video generation through the shared platform-job runner."""

from __future__ import annotations

import logging
from uuid import UUID

from pydantic import BaseModel, Field

from src.jobs.platform.base import (
    PlatformJobContext,
    PlatformJobDefinition,
    PlatformJobFailure,
    PlatformJobPolicy,
)

VIDEO_GENERATION_JOB_TYPE = "chat.video_generation"
logger = logging.getLogger(__name__)


class VideoGenerationPayload(BaseModel):
    conversation_id: UUID
    message_id: UUID
    filename: str = Field(min_length=1, max_length=200)
    prompt: str = Field(min_length=1, max_length=20_000)


class SDKVideoGenerationPayload(BaseModel):
    filename: str = Field(min_length=1, max_length=200)
    prompt: str = Field(min_length=1, max_length=20_000)
    workspace_id: UUID | None = None
    execution_id: UUID | None = None


async def run_video_generation(
    context: PlatformJobContext,
    raw_payload: BaseModel,
) -> dict[str, object]:
    import httpx

    from src.core.database import get_db_context
    from src.models.orm import Conversation, Message
    from src.services.artifacts import artifact_ref
    from src.services.chat_attachments import ChatAttachmentService
    from src.services.media_generation import (
        MediaGenerationError,
        generate_video_with_config,
        get_media_provider_config,
        record_media_usage,
    )

    payload = VideoGenerationPayload.model_validate(raw_payload)
    try:
        async with get_db_context() as db:
            conversation = await db.get(Conversation, payload.conversation_id)
            message = await db.get(Message, payload.message_id)
            if (
                conversation is None
                or str(conversation.user_id) != context.requested_by_user_id
                or message is None
                or message.conversation_id != conversation.id
            ):
                raise PlatformJobFailure(
                    "chat_target_not_found",
                    "The Chat message for this video no longer exists.",
                )

            provider_config = await get_media_provider_config(db, "video")

        async def report(phase: str, percent: float | None) -> None:
            await context.report(phase, percent=percent)

        artifact = await generate_video_with_config(
            provider_config,
            filename=payload.filename,
            prompt=payload.prompt,
            report=report,
        )
        async with get_db_context() as db:
            conversation = await db.get(Conversation, payload.conversation_id)
            message = await db.get(Message, payload.message_id)
            if (
                conversation is None
                or str(conversation.user_id) != context.requested_by_user_id
                or message is None
                or message.conversation_id != conversation.id
            ):
                raise PlatformJobFailure(
                    "chat_target_not_found",
                    "The Chat message for this video no longer exists.",
                )
            attachment = await ChatAttachmentService(db).store_generated(
                conversation_id=conversation.id,
                message_id=message.id,
                filename=artifact.filename,
                content_type=artifact.content_type,
                content=artifact.content,
            )
            await record_media_usage(
                db,
                artifact,
                conversation_id=conversation.id,
                message_id=message.id,
                organization_id=context.organization_id,
                user_id=conversation.user_id,
            )
            ref = artifact_ref(attachment.artifact)
            message.tool_state = "completed"
            message.token_count_input = artifact.input_tokens
            message.token_count_output = artifact.output_tokens
            message.tool_result = {
                "type": "platform_job",
                "job_id": str(context.job_id),
                "status": "succeeded",
                "kind": "video_generation",
                "conversation_id": str(conversation.id),
                "message_id": str(message.id),
                "artifact": ref.model_dump(mode="json"),
            }
            await db.commit()

        return {
            "conversation_id": str(payload.conversation_id),
            "message_id": str(payload.message_id),
            "artifact": ref.model_dump(mode="json"),
        }
    except PlatformJobFailure:
        raise
    except (MediaGenerationError, httpx.HTTPError, TypeError, ValueError) as exc:
        try:
            async with get_db_context() as db:
                message = await db.get(Message, payload.message_id)
                if message is not None and message.conversation_id == payload.conversation_id:
                    message.tool_state = "error"
                    message.tool_result = {
                        "type": "platform_job",
                        "job_id": str(context.job_id),
                        "status": "failed",
                        "kind": "video_generation",
                        "conversation_id": str(payload.conversation_id),
                        "message_id": str(payload.message_id),
                        "error": str(exc)[:500],
                    }
        except Exception:
            logger.warning(
                "Could not project video generation failure onto its Chat message",
                extra={"platform_job_id": str(context.job_id)},
                exc_info=True,
            )
        raise PlatformJobFailure(
            "video_generation_failed",
            str(exc),
            retryable=False,
        ) from exc


VIDEO_GENERATION_DEFINITION = PlatformJobDefinition(
    job_type=VIDEO_GENERATION_JOB_TYPE,
    payload_version=1,
    payload_model=VideoGenerationPayload,
    handler=run_video_generation,
    policy=PlatformJobPolicy(
        timeout_seconds=30 * 60,
        max_attempts=1,
        retry_on_runner_loss=False,
        min_memory_headroom_mb=512,
        allow_running_cancellation=True,
    ),
    encrypt_payload=True,
)


async def run_sdk_video_generation(
    context: PlatformJobContext,
    raw_payload: BaseModel,
) -> dict[str, object]:
    import httpx

    from src.core.database import get_db_context
    from src.services.artifacts import ArtifactService, artifact_ref
    from src.services.media_generation import (
        MediaGenerationError,
        generate_video_with_config,
        get_media_provider_config,
        record_media_usage,
    )

    payload = SDKVideoGenerationPayload.model_validate(raw_payload)
    try:
        async with get_db_context() as db:
            provider_config = await get_media_provider_config(db, "video")

        async def report(phase: str, percent: float | None) -> None:
            await context.report(phase, percent=percent)

        artifact = await generate_video_with_config(
            provider_config,
            filename=payload.filename,
            prompt=payload.prompt,
            report=report,
        )
        async with get_db_context() as db:
            stored = await ArtifactService(db).store(
                filename=artifact.filename,
                content_type=artifact.content_type,
                content=artifact.content,
                created_by_user_id=UUID(context.requested_by_user_id),
                organization_id=context.organization_id,
                workspace_id=payload.workspace_id,
                logical_path=artifact.filename,
            )
            ref = artifact_ref(stored)
            await record_media_usage(
                db,
                artifact,
                execution_id=payload.execution_id,
                organization_id=context.organization_id,
                user_id=UUID(context.requested_by_user_id),
            )
        return {"artifact": ref.model_dump(mode="json")}
    except (MediaGenerationError, httpx.HTTPError, ValueError) as exc:
        raise PlatformJobFailure(
            "video_generation_failed",
            str(exc),
            retryable=False,
        ) from exc


SDK_VIDEO_GENERATION_DEFINITION = PlatformJobDefinition(
    job_type="sdk.video_generation",
    payload_version=1,
    payload_model=SDKVideoGenerationPayload,
    handler=run_sdk_video_generation,
    policy=VIDEO_GENERATION_DEFINITION.policy,
    encrypt_payload=True,
)
