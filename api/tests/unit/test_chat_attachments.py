from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from src.models.orm import MessageAttachment
from src.services.chat_attachments import (
    MAX_FILE_SIZE_BYTES,
    ChatAttachmentError,
    ChatAttachmentService,
)


def _db_with_total(total: int = 0) -> MagicMock:
    db = MagicMock()
    result = MagicMock()
    result.scalar.return_value = total
    db.execute = AsyncMock(return_value=result)
    db.flush = AsyncMock()
    return db


@pytest.mark.asyncio
async def test_store_text_attachment_extracts_content_and_writes_s3() -> None:
    db = _db_with_total()
    storage = AsyncMock()
    conversation_id = uuid4()

    with patch(
        "src.services.chat_attachments.get_file_storage_service",
        return_value=storage,
    ):
        attachment = await ChatAttachmentService(db).store(
            conversation_id=conversation_id,
            filename="notes.md",
            content_type="text/markdown",
            content=b"# Notes\nHello",
        )

    assert attachment.extracted_text == "# Notes\nHello"
    assert attachment.s3_key.startswith(f"_attachments/{conversation_id}/")
    assert attachment.s3_key.endswith("_notes.md")
    storage.write_raw_to_s3.assert_awaited_once_with(
        attachment.s3_key, b"# Notes\nHello"
    )


@pytest.mark.asyncio
async def test_store_infers_known_text_type_when_browser_omits_it() -> None:
    db = _db_with_total()
    storage = AsyncMock()

    with patch(
        "src.services.chat_attachments.get_file_storage_service",
        return_value=storage,
    ):
        attachment = await ChatAttachmentService(db).store(
            conversation_id=uuid4(),
            filename="notes.md",
            content_type="application/octet-stream",
            content=b"# Notes",
        )

    assert attachment.content_type == "text/markdown"
    assert attachment.extracted_text == "# Notes"


@pytest.mark.asyncio
async def test_store_rejects_unsupported_and_oversized_files() -> None:
    service = ChatAttachmentService(_db_with_total())
    with pytest.raises(ChatAttachmentError, match="Unsupported file type"):
        await service.store(
            conversation_id=uuid4(),
            filename="archive.zip",
            content_type="application/zip",
            content=b"not a zip",
        )
    with pytest.raises(ChatAttachmentError, match="too large"):
        await service.store(
            conversation_id=uuid4(),
            filename="large.txt",
            content_type="text/plain",
            content=b"x" * (MAX_FILE_SIZE_BYTES + 1),
        )


@pytest.mark.asyncio
async def test_store_removes_s3_object_when_persistence_fails() -> None:
    db = _db_with_total()
    db.flush.side_effect = RuntimeError("database unavailable")
    storage = AsyncMock()

    with patch(
        "src.services.chat_attachments.get_file_storage_service",
        return_value=storage,
    ):
        with pytest.raises(RuntimeError, match="database unavailable"):
            await ChatAttachmentService(db).store(
                conversation_id=uuid4(),
                filename="notes.txt",
                content_type="text/plain",
                content=b"hello",
            )

    storage.delete_raw_from_s3.assert_awaited_once()


@pytest.mark.asyncio
async def test_bind_rejects_cross_conversation_attachment() -> None:
    attachment = MessageAttachment(
        id=uuid4(),
        message_id=None,
        conversation_id=uuid4(),
        s3_key="_attachments/file",
        filename="file.txt",
        content_type="text/plain",
        size_bytes=4,
    )
    db = MagicMock()
    result = MagicMock()
    scalars = MagicMock()
    scalars.all.return_value = [attachment]
    result.scalars.return_value = scalars
    db.execute = AsyncMock(return_value=result)
    db.flush = AsyncMock()

    with pytest.raises(ChatAttachmentError, match="another conversation"):
        await ChatAttachmentService(db).bind(
            attachment_ids=[attachment.id],
            message_id=uuid4(),
            conversation_id=uuid4(),
        )
