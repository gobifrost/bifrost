"""Chat-local artifact tools backed by trusted document generators."""

from __future__ import annotations

import asyncio
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from shared.artifact_generation import (
    GeneratedArtifact,
    generate_document,
    generate_spreadsheet,
    generate_text,
)
from src.models.contracts.artifacts import (
    ArtifactRef,
    DocumentArtifactSpec,
    SpreadsheetArtifactSpec,
    TextArtifactSpec,
)
from src.models.orm import MessageAttachment
from src.models.orm import User
from src.services.chat_attachments import ChatAttachmentService
from src.services.file_storage.service import get_file_storage_service
from src.services.llm import ToolDefinition
from shared.file_paths import resolve_s3_key

CREATE_DOCUMENT_TOOL = "create_document_artifact"
CREATE_SPREADSHEET_TOOL = "create_spreadsheet_artifact"
CREATE_TEXT_TOOL = "create_text_artifact"
ARTIFACT_TOOL_NAMES = frozenset(
    {CREATE_DOCUMENT_TOOL, CREATE_SPREADSHEET_TOOL, CREATE_TEXT_TOOL}
)


def artifact_tool_definitions() -> list[ToolDefinition]:
    """Return format-specific schemas rather than one ambiguous catch-all tool."""
    return [
        ToolDefinition(
            name=CREATE_DOCUMENT_TOOL,
            description=(
                "Create and attach a polished PDF or DOCX file. Use flowing sections, "
                "narrative paragraphs, bullets, and bounded tables. Prefer this when the "
                "user asks for a report, brief, memo, letter, or downloadable document."
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


async def execute_artifact_tool(
    db: AsyncSession,
    *,
    tool_name: str,
    arguments: dict[str, Any],
    conversation_id: UUID,
    message_id: UUID,
) -> tuple[MessageAttachment, ArtifactRef]:
    """Validate, generate, and durably attach one artifact to a tool-call message."""
    generated: GeneratedArtifact
    if tool_name == CREATE_DOCUMENT_TOOL:
        spec = DocumentArtifactSpec.model_validate(arguments)
        generated = await asyncio.to_thread(generate_document, spec)
    elif tool_name == CREATE_SPREADSHEET_TOOL:
        spec = SpreadsheetArtifactSpec.model_validate(arguments)
        generated = await asyncio.to_thread(generate_spreadsheet, spec)
    elif tool_name == CREATE_TEXT_TOOL:
        spec = TextArtifactSpec.model_validate(arguments)
        generated = await asyncio.to_thread(generate_text, spec)
    else:
        raise ValueError(f"Unknown artifact tool: {tool_name}")

    attachment = await ChatAttachmentService(db).store_generated(
        conversation_id=conversation_id,
        message_id=message_id,
        filename=generated.filename,
        content_type=generated.content_type,
        content=generated.content,
    )
    return attachment, ArtifactRef(
        filename=attachment.filename,
        content_type=attachment.content_type,
        size_bytes=attachment.size_bytes,
        attachment_id=str(attachment.id),
        conversation_id=str(attachment.conversation_id),
        created_at=attachment.created_at,
    )


def find_artifact_refs(value: Any) -> list[ArtifactRef]:
    """Find canonical ArtifactRef dictionaries in arbitrary workflow results."""
    found: list[ArtifactRef] = []
    if isinstance(value, dict):
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
    """Copy managed workflow outputs into Chat's durable, authorized file store."""
    refs = [ref for ref in find_artifact_refs(result) if ref.attachment_id is None]
    if not refs:
        return []

    user_org = (
        await db.execute(select(User.organization_id).where(User.id == conversation_user_id))
    ).scalar_one_or_none()
    allowed_scopes = {"global"}
    if user_org:
        allowed_scopes.add(str(user_org))
    if agent_organization_id:
        allowed_scopes.add(str(agent_organization_id))

    storage = get_file_storage_service(db)
    promoted: list[ArtifactRef] = []
    for ref in refs:
        if not ref.path or not ref.location or not ref.scope:
            raise ValueError("Workflow artifact references must include path, location, and scope.")
        if ref.location == "workspace":
            raise ValueError("Workspace files cannot be promoted to Chat artifacts.")
        if ref.scope not in allowed_scopes:
            raise ValueError("Workflow artifact belongs to a different organization scope.")
        content = await storage.read_uploaded_file(
            resolve_s3_key(ref.location, ref.scope, ref.path)
        )
        attachment = await ChatAttachmentService(db).store_generated(
            conversation_id=conversation_id,
            message_id=message_id,
            filename=ref.filename,
            content_type=ref.content_type,
            content=content,
        )
        promoted.append(
            ArtifactRef(
                filename=attachment.filename,
                content_type=attachment.content_type,
                size_bytes=attachment.size_bytes,
                attachment_id=str(attachment.id),
                conversation_id=str(attachment.conversation_id),
                created_at=attachment.created_at,
            )
        )
    return promoted
