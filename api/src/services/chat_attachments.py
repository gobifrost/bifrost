"""Validation, storage, and message binding for chat file attachments."""

from __future__ import annotations

import csv
import io
from pathlib import Path
from dataclasses import dataclass
from itertools import islice
from uuid import UUID

from PIL import Image, UnidentifiedImageError
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.orm import Artifact, Conversation, MessageAttachment, User
from src.services.artifacts import ArtifactService
from src.services.file_storage.service import get_file_storage_service

MAX_FILE_SIZE_BYTES = 25 * 1024 * 1024
MAX_FILES_PER_MESSAGE = 5
MAX_CONVERSATION_BYTES = 500 * 1024 * 1024
MAX_IMAGE_PIXELS = 40_000_000
MAX_EXTRACTED_TEXT_CHARS = 200_000

IMAGE_CONTENT_TYPES = {
    "image/png",
    "image/jpeg",
    "image/jpg",
    "image/webp",
    "image/gif",
}
PDF_CONTENT_TYPES = {"application/pdf"}
TEXT_CONTENT_TYPES = {
    "text/plain",
    "text/markdown",
    "text/csv",
    "application/csv",
    "application/json",
    "text/json",
    "application/x-yaml",
    "text/yaml",
    "text/x-yaml",
}
EXTENSION_CONTENT_TYPES = {
    ".txt": "text/plain",
    ".md": "text/markdown",
    ".markdown": "text/markdown",
    ".csv": "text/csv",
    ".json": "application/json",
    ".yaml": "text/yaml",
    ".yml": "text/yaml",
}


class ChatAttachmentError(ValueError):
    """Raised when a chat attachment cannot be accepted or bound."""


@dataclass(frozen=True)
class ChatInputFile:
    """Provider-neutral binary input loaded from attachment storage."""

    filename: str
    content_type: str
    data: bytes


def is_binary_model_input(content_type: str) -> bool:
    """Return whether Pydantic AI should receive the original bytes."""
    return content_type in IMAGE_CONTENT_TYPES or content_type in PDF_CONTENT_TYPES


def _is_text(content_type: str) -> bool:
    return content_type in TEXT_CONTENT_TYPES or content_type.startswith("text/")


def _normalize_content_type(filename: str, content_type: str) -> str:
    if content_type and content_type != "application/octet-stream":
        return content_type
    return EXTENSION_CONTENT_TYPES.get(Path(filename).suffix.lower(), content_type)


def _validate_image(content: bytes) -> None:
    try:
        with Image.open(io.BytesIO(content)) as image:
            if image.width * image.height > MAX_IMAGE_PIXELS:
                raise ChatAttachmentError(
                    f"Image exceeds the {MAX_IMAGE_PIXELS:,}-pixel limit."
                )
            image.verify()
    except ChatAttachmentError:
        raise
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise ChatAttachmentError("Attachment is not a valid image.") from exc


def _extract_text(content: bytes, content_type: str) -> str | None:
    if not _is_text(content_type):
        return None
    decoded = content.decode("utf-8", errors="replace")
    if content_type in {"text/csv", "application/csv"}:
        rows = csv.reader(io.StringIO(decoded))
        preview = list(islice(rows, 21))
        output = "\n".join(",".join(cell for cell in row) for row in preview)
        if next(rows, None) is not None:
            output += "\n… (more rows)"
        return output[:MAX_EXTRACTED_TEXT_CHARS]
    return decoded[:MAX_EXTRACTED_TEXT_CHARS]


class ChatAttachmentService:
    """Store, retrieve, and bind user-provided chat files."""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def _artifact_owner(self, conversation_id: UUID) -> tuple[UUID, UUID | None]:
        conversation = await self.db.get(Conversation, conversation_id)
        if conversation is None:
            raise ChatAttachmentError("The Chat conversation no longer exists.")
        user = await self.db.get(User, conversation.user_id)
        if user is None:
            raise ChatAttachmentError("The Chat user no longer exists.")
        return user.id, user.organization_id

    async def _ensure_conversation_capacity(
        self, conversation_id: UUID, content_size: int
    ) -> None:
        total_result = await self.db.execute(
            select(func.coalesce(func.sum(MessageAttachment.size_bytes), 0)).where(
                MessageAttachment.conversation_id == conversation_id
            )
        )
        if int(total_result.scalar() or 0) + content_size > MAX_CONVERSATION_BYTES:
            raise ChatAttachmentError("This conversation's 500 MB file limit was reached.")

    async def store(
        self,
        *,
        conversation_id: UUID,
        filename: str,
        content_type: str,
        content: bytes,
    ) -> MessageAttachment:
        content_type = _normalize_content_type(filename, content_type)
        if not filename:
            raise ChatAttachmentError("Attachment filename is required.")
        if not content:
            raise ChatAttachmentError(f"{filename} is empty.")
        if len(content) > MAX_FILE_SIZE_BYTES:
            raise ChatAttachmentError(f"{filename} is too large (maximum 25 MB).")
        if not (
            content_type in IMAGE_CONTENT_TYPES
            or content_type in PDF_CONTENT_TYPES
            or _is_text(content_type)
        ):
            raise ChatAttachmentError(
                "Unsupported file type. Attach an image, PDF, CSV, or text file."
            )
        if content_type in IMAGE_CONTENT_TYPES:
            _validate_image(content)
        if content_type in PDF_CONTENT_TYPES and not content.startswith(b"%PDF-"):
            raise ChatAttachmentError("Attachment is not a valid PDF.")

        await self._ensure_conversation_capacity(conversation_id, len(content))

        user_id, organization_id = await self._artifact_owner(conversation_id)
        artifact = await ArtifactService(self.db).store(
            filename=filename,
            content_type=content_type,
            content=content,
            created_by_user_id=user_id,
            organization_id=organization_id,
            storage_family="upload",
            workspace_id=conversation_id,
            logical_path=filename,
        )

        attachment = MessageAttachment(
            id=artifact.id,
            artifact_id=artifact.id,
            artifact=artifact,
            message_id=None,
            conversation_id=conversation_id,
            s3_key=artifact.s3_key,
            filename=artifact.filename,
            content_type=artifact.content_type,
            size_bytes=artifact.size_bytes,
            extracted_text=_extract_text(content, content_type),
        )
        try:
            self.db.add(attachment)
            await self.db.flush()
        except Exception:
            await get_file_storage_service(self.db).delete_raw_from_s3(artifact.s3_key)
            raise
        return attachment

    async def store_generated(
        self,
        *,
        conversation_id: UUID,
        message_id: UUID,
        filename: str,
        content_type: str,
        content: bytes,
    ) -> MessageAttachment:
        """Store a validated generated file using the same durable model as uploads."""
        from shared.artifact_generation import validate_artifact_content

        try:
            validate_artifact_content(
                filename=filename,
                content_type=content_type,
                content=content,
            )
        except ValueError as exc:
            raise ChatAttachmentError(str(exc)) from exc
        await self._ensure_conversation_capacity(conversation_id, len(content))

        user_id, organization_id = await self._artifact_owner(conversation_id)
        artifact = await ArtifactService(self.db).store(
            filename=filename,
            content_type=content_type,
            content=content,
            created_by_user_id=user_id,
            organization_id=organization_id,
            workspace_id=conversation_id,
            logical_path=filename,
        )
        attachment = MessageAttachment(
            id=artifact.id,
            artifact_id=artifact.id,
            artifact=artifact,
            message_id=message_id,
            conversation_id=conversation_id,
            s3_key=artifact.s3_key,
            filename=artifact.filename,
            content_type=artifact.content_type,
            size_bytes=artifact.size_bytes,
            extracted_text=_extract_text(content, content_type),
        )
        try:
            self.db.add(attachment)
            await self.db.flush()
        except Exception:
            await get_file_storage_service(self.db).delete_raw_from_s3(artifact.s3_key)
            raise
        return attachment

    async def bind(
        self,
        *,
        attachment_ids: list[UUID],
        message_id: UUID,
        conversation_id: UUID,
    ) -> list[MessageAttachment]:
        if len(attachment_ids) > MAX_FILES_PER_MESSAGE:
            raise ChatAttachmentError("Attach no more than 5 files per message.")
        if not attachment_ids:
            return []

        result = await self.db.execute(
            select(MessageAttachment).where(
                MessageAttachment.artifact_id.in_(attachment_ids)
            )
        )
        found = {
            attachment.artifact_id: attachment for attachment in result.scalars().all()
        }
        if len(found) != len(set(attachment_ids)):
            raise ChatAttachmentError("One or more attachments were not found.")
        attachments: list[MessageAttachment] = []
        for attachment_id in attachment_ids:
            attachment = found[attachment_id]
            if attachment.conversation_id != conversation_id:
                raise ChatAttachmentError("Attachment belongs to another conversation.")
            if attachment.message_id not in (None, message_id):
                raise ChatAttachmentError("Attachment is already bound to another message.")
            attachment.message_id = message_id
            attachments.append(attachment)
        await self.db.flush()
        return attachments

    async def attach_artifact(
        self,
        *,
        artifact: Artifact,
        conversation_id: UUID,
        message_id: UUID,
    ) -> MessageAttachment:
        """Associate an existing canonical artifact with a Chat message."""
        existing = (
            await self.db.execute(
                select(MessageAttachment)
                .where(MessageAttachment.artifact_id == artifact.id)
                .where(MessageAttachment.conversation_id == conversation_id)
                .where(MessageAttachment.message_id == message_id)
            )
        ).scalar_one_or_none()
        if existing is not None:
            return existing
        await self._ensure_conversation_capacity(conversation_id, artifact.size_bytes)
        extracted_text = None
        if _is_text(artifact.content_type):
            content = await ArtifactService(self.db).read(artifact)
            extracted_text = _extract_text(content, artifact.content_type)
        attachment = MessageAttachment(
            artifact_id=artifact.id,
            artifact=artifact,
            message_id=message_id,
            conversation_id=conversation_id,
            s3_key=artifact.s3_key,
            filename=artifact.filename,
            content_type=artifact.content_type,
            size_bytes=artifact.size_bytes,
            extracted_text=extracted_text,
        )
        self.db.add(attachment)
        await self.db.flush()
        return attachment

    async def load_binary_input(self, attachment: MessageAttachment) -> ChatInputFile:
        storage = get_file_storage_service(self.db)
        return ChatInputFile(
            filename=attachment.filename,
            content_type=attachment.content_type,
            data=await storage.read_uploaded_file(attachment.s3_key),
        )
