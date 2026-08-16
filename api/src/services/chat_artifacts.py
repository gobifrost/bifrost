"""Chat-local artifact tools backed by trusted document generators."""

from __future__ import annotations

import asyncio
import logging
from typing import Any
from uuid import UUID

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from shared.artifact_generation import (
    GeneratedArtifact,
    generate_document,
    generate_document_with_images,
    generate_spreadsheet,
    generate_text,
)
from src.models.contracts.artifacts import (
    ArtifactRef,
    DocumentArtifactSpec,
    ImageArtifactSpec,
    SpreadsheetArtifactSpec,
    TextArtifactSpec,
    VideoArtifactSpec,
)
from src.models.orm import Artifact, Conversation, Message, MessageAttachment, User
from src.services.artifacts import ArtifactService, artifact_ref
from src.services.chat_attachments import ChatAttachmentService
from src.services.llm import ToolDefinition
from src.services.media_generation import generate_image, record_media_usage

logger = logging.getLogger(__name__)

CREATE_DOCUMENT_TOOL = "create_document_artifact"
CREATE_SPREADSHEET_TOOL = "create_spreadsheet_artifact"
CREATE_TEXT_TOOL = "create_text_artifact"
CREATE_IMAGE_TOOL = "create_image_artifact"
CREATE_VIDEO_TOOL = "create_video_artifact"
LIST_ARTIFACTS_TOOL = "list_artifacts"
IMMEDIATE_ARTIFACT_TOOL_NAMES = frozenset(
    {CREATE_DOCUMENT_TOOL, CREATE_SPREADSHEET_TOOL, CREATE_TEXT_TOOL, CREATE_IMAGE_TOOL}
)
ASYNC_ARTIFACT_TOOL_NAMES = frozenset({CREATE_VIDEO_TOOL})
ARTIFACT_TOOL_NAMES = frozenset(
    {*IMMEDIATE_ARTIFACT_TOOL_NAMES, *ASYNC_ARTIFACT_TOOL_NAMES}
)
ARTIFACT_WORKSPACE_TOOL_NAMES = frozenset({LIST_ARTIFACTS_TOOL})
BUILTIN_ARTIFACT_TOOL_NAMES = frozenset(
    {*ARTIFACT_TOOL_NAMES, *ARTIFACT_WORKSPACE_TOOL_NAMES}
)
ARTIFACT_WORKSPACE_INSTRUCTIONS = """

Files you create or receive live in this Chat's shared artifact workspace.
Use list_artifacts when you need to discover files from an earlier turn, and
refer to those files by the returned filename/path when composing another
artifact. Never invent local filesystem paths or file:// links. Generated files
are presented to the user automatically as artifact cards.
""".strip()


def artifact_tool_definitions(
    *,
    image_generation_enabled: bool,
    video_generation_enabled: bool,
) -> list[ToolDefinition]:
    """Return only the artifact tools backed by configured generators."""
    definitions = [
        ToolDefinition(
            name=LIST_ARTIFACTS_TOOL,
            description=(
                "List the files currently available in this conversation's artifact "
                "workspace. Use this before composing a new file from earlier generated "
                "or uploaded files. Returned filenames are stable logical workspace paths."
            ),
            parameters={"type": "object", "properties": {}, "additionalProperties": False},
        ),
        ToolDefinition(
            name=CREATE_DOCUMENT_TOOL,
            description=(
                "Create and attach a polished PDF or DOCX file. Use flowing sections, "
                "narrative paragraphs, bullets, bounded tables, and images from the active "
                "artifact workspace. Image paths are filenames returned by earlier artifact "
                "tools or list_artifacts. Prefer this for reports, briefs, memos, letters, "
                "or downloadable documents."
            ),
            parameters=DocumentArtifactSpec.model_json_schema(),
        ),
        ToolDefinition(
            name=CREATE_SPREADSHEET_TOOL,
            description=(
                "Create and attach a styled XLSX workbook with one or more tabular sheets. "
                "Use descriptive headers and consistent row widths. Prefer this for data, "
                "trackers, exports, calculations, or spreadsheet deliverables."
            ),
            parameters=SpreadsheetArtifactSpec.model_json_schema(),
        ),
        ToolDefinition(
            name=CREATE_TEXT_TOOL,
            description=(
                "Create and attach a UTF-8 CSV, HTML, Markdown, plain-text, or JSON file. "
                "The content must be complete and ready for the user to preview or download."
            ),
            parameters=TextArtifactSpec.model_json_schema(),
        ),
    ]
    if image_generation_enabled:
        definitions.append(
            ToolDefinition(
                name=CREATE_IMAGE_TOOL,
                description=(
                    "Generate and attach a new image using the administrator-configured "
                    "image model. Use this when the user asks you to create an image, "
                    "illustration, graphic, or other raster visual."
                ),
                parameters=ImageArtifactSpec.model_json_schema(),
            )
        )
    if video_generation_enabled:
        definitions.append(
            ToolDefinition(
                name=CREATE_VIDEO_TOOL,
                description=(
                    "Start durable video generation using the administrator-configured "
                    "video model. The video continues generating after this tool returns "
                    "and will be attached to this Chat when ready."
                ),
                parameters=VideoArtifactSpec.model_json_schema(),
            )
        )
    return definitions


async def execute_artifact_tool(
    db: AsyncSession,
    *,
    tool_name: str,
    arguments: dict[str, Any],
    conversation_id: UUID,
    message_id: UUID,
) -> tuple[MessageAttachment | None, ArtifactRef | dict[str, Any]]:
    """Validate, generate, and durably attach one artifact to a tool-call message."""
    generated: GeneratedArtifact
    if tool_name == LIST_ARTIFACTS_TOOL:
        conversation = await db.get(Conversation, conversation_id)
        if conversation is None:
            raise ValueError("The Chat conversation no longer exists.")
        user = await db.get(User, conversation.user_id)
        if user is None:
            raise ValueError("The Chat user no longer exists.")
        artifacts = await ArtifactService(db).list_workspace(
            conversation_id,
            user_id=user.id,
            organization_id=user.organization_id,
            is_platform_admin=user.is_superuser,
        )
        return None, {
            "workspace_id": str(conversation_id),
            "files": [artifact_ref(item).model_dump(mode="json") for item in artifacts],
        }
    if tool_name == CREATE_DOCUMENT_TOOL:
        spec = DocumentArtifactSpec.model_validate(arguments)
        if any(section.images for section in spec.sections):
            conversation = await db.get(Conversation, conversation_id)
            if conversation is None:
                raise ValueError("The Chat conversation no longer exists.")
            user = await db.get(User, conversation.user_id)
            if user is None:
                raise ValueError("The Chat user no longer exists.")
            service = ArtifactService(db)
            image_content: dict[str, bytes] = {}
            for image in (
                image
                for section in spec.sections
                for image in section.images
            ):
                artifact = await service.resolve_workspace_path(
                    conversation_id,
                    image.path,
                    user_id=user.id,
                    organization_id=user.organization_id,
                    is_platform_admin=user.is_superuser,
                )
                if not artifact.content_type.startswith("image/"):
                    raise ValueError(f"{image.path} is not an image artifact.")
                image_content[image.path] = await service.read(artifact)
            generated = await asyncio.to_thread(
                generate_document_with_images,
                spec,
                image_content,
            )
        else:
            generated = await asyncio.to_thread(generate_document, spec)
    elif tool_name == CREATE_SPREADSHEET_TOOL:
        spec = SpreadsheetArtifactSpec.model_validate(arguments)
        generated = await asyncio.to_thread(generate_spreadsheet, spec)
    elif tool_name == CREATE_TEXT_TOOL:
        spec = TextArtifactSpec.model_validate(arguments)
        generated = await asyncio.to_thread(generate_text, spec)
    elif tool_name == CREATE_IMAGE_TOOL:
        spec = ImageArtifactSpec.model_validate(arguments)
        generated = await generate_image(
            db,
            filename=spec.filename,
            prompt=spec.prompt,
        )
    elif tool_name == CREATE_VIDEO_TOOL:
        spec = VideoArtifactSpec.model_validate(arguments)
        conversation = await db.get(Conversation, conversation_id)
        if conversation is None:
            raise ValueError("The Chat conversation no longer exists.")
        user = await db.get(User, conversation.user_id)
        if user is None:
            raise ValueError("The Chat user no longer exists.")

        from src.jobs.platform.video_generation import (
            VIDEO_GENERATION_DEFINITION,
            VideoGenerationPayload,
        )
        from src.services.platform_jobs import (
            enqueue_platform_job,
            ensure_platform_job_notification,
            publish_platform_job_update,
        )

        job, reused = await enqueue_platform_job(
            db,
            VIDEO_GENERATION_DEFINITION,
            VideoGenerationPayload(
                conversation_id=conversation_id,
                message_id=message_id,
                filename=spec.filename,
                prompt=spec.prompt,
            ),
            dedupe_key=str(message_id),
            organization_id=user.organization_id,
            requested_by_user_id=user.id,
            requested_by_email=user.email,
            requested_by_name=user.name or user.email,
            resource_type="chat_conversation",
            resource_id=str(conversation_id),
            title=f"Generating {spec.filename}",
            action_url=f"/chat/{conversation_id}",
        )
        if job.notification_id is None:
            try:
                await ensure_platform_job_notification(db, job)
            except Exception:
                logger.warning(
                    "Video generation queued without a progress notification",
                    extra={"platform_job_id": str(job.id)},
                    exc_info=True,
                )
        await db.commit()
        await db.refresh(job)
        await publish_platform_job_update(job)
        return None, {
            "type": "platform_job",
            "job_id": str(job.id),
            "status": job.status,
            "reused": reused,
            "kind": "video_generation",
            "conversation_id": str(conversation_id),
            "message_id": str(message_id),
        }
    else:
        raise ValueError(f"Unknown artifact tool: {tool_name}")

    attachment = await ChatAttachmentService(db).store_generated(
        conversation_id=conversation_id,
        message_id=message_id,
        filename=generated.filename,
        content_type=generated.content_type,
        content=generated.content,
    )
    if generated.provider is not None:
        conversation = await db.get(Conversation, conversation_id)
        user = await db.get(User, conversation.user_id) if conversation else None
        if conversation is not None and user is not None:
            await record_media_usage(
                db,
                generated,
                conversation_id=conversation_id,
                message_id=message_id,
                organization_id=user.organization_id,
                user_id=user.id,
            )
            tool_message = await db.get(Message, message_id)
            if tool_message is not None:
                tool_message.token_count_input = generated.input_tokens
                tool_message.token_count_output = generated.output_tokens
    return attachment, artifact_ref(attachment.artifact)


def find_artifact_refs(value: Any) -> list[ArtifactRef]:
    """Find canonical ArtifactRef dictionaries in arbitrary workflow results."""
    found: list[ArtifactRef] = []
    if isinstance(value, ArtifactRef):
        found.append(value)
    elif isinstance(value, dict):
        if value.get("type") == "bifrost_artifact":
            found.append(ArtifactRef.model_validate(value))
        else:
            for child in value.values():
                found.extend(find_artifact_refs(child))
    elif isinstance(value, list | tuple):
        for child in value:
            found.extend(find_artifact_refs(child))
    return found


async def promote_artifact_refs(
    db: AsyncSession,
    *,
    result: Any,
    conversation_id: UUID,
    conversation_user_id: UUID,
    message_id: UUID,
    agent_organization_id: UUID | None,
) -> list[ArtifactRef]:
    """Associate authorized workflow outputs with Chat without changing identity."""
    refs = find_artifact_refs(result)
    if not refs:
        return []

    user_org = (
        await db.execute(select(User.organization_id).where(User.id == conversation_user_id))
    ).scalar_one_or_none()
    promoted: list[ArtifactRef] = []
    for ref in refs:
        allowed = [Artifact.created_by_user_id == conversation_user_id]
        if user_org is not None:
            allowed.append(Artifact.organization_id == user_org)
        if agent_organization_id is not None:
            allowed.append(Artifact.organization_id == agent_organization_id)
        artifact = (
            await db.execute(
                select(Artifact).where(Artifact.id == UUID(ref.id)).where(or_(*allowed))
            )
        ).scalar_one_or_none()
        if artifact is None:
            raise ValueError(
                "Workflow artifact was not found or is outside this Chat scope."
            )
        await ChatAttachmentService(db).attach_artifact(
            artifact=artifact,
            conversation_id=conversation_id,
            message_id=message_id,
        )
        promoted.append(artifact_ref(artifact))
    return promoted
