from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from fastapi import HTTPException

from src.models.contracts.agents import ChatArtifactUpdate
from src.models.orm import MessageAttachment
from src.routers.chat import delete_chat_artifact, list_chat_artifacts, rename_chat_artifact


def _bound_artifact() -> MessageAttachment:
    return MessageAttachment(
        id=uuid4(),
        message_id=uuid4(),
        conversation_id=uuid4(),
        s3_key="_artifacts/conversation/report.pdf",
        filename="Quarterly Report.pdf",
        content_type="application/pdf",
        size_bytes=2048,
        created_at=datetime(2026, 8, 15, tzinfo=UTC),
    )


@pytest.mark.asyncio
async def test_list_chat_artifacts_returns_user_owned_file_context() -> None:
    artifact = _bound_artifact()
    result = MagicMock()
    result.all.return_value = [(artifact, "Quarterly review")]
    db = MagicMock()
    db.execute = AsyncMock(return_value=result)
    user = MagicMock(user_id=uuid4())

    files = await list_chat_artifacts(db=db, user=user)

    assert len(files) == 1
    assert files[0].kind == "artifact"
    assert files[0].conversation_title == "Quarterly review"
    assert files[0].message_id == artifact.message_id


@pytest.mark.asyncio
async def test_rename_chat_artifact_updates_only_display_metadata() -> None:
    artifact = _bound_artifact()
    original_key = artifact.s3_key
    result = MagicMock()
    result.one_or_none.return_value = (artifact, "Quarterly review")
    db = MagicMock()
    db.execute = AsyncMock(return_value=result)
    db.flush = AsyncMock()

    renamed = await rename_chat_artifact(
        attachment_id=artifact.id,
        request=ChatArtifactUpdate(filename="Executive Summary.pdf"),
        db=db,
        user=MagicMock(user_id=uuid4()),
    )

    assert renamed.filename == "Executive Summary.pdf"
    assert artifact.s3_key == original_key
    db.flush.assert_awaited_once()


@pytest.mark.asyncio
async def test_rename_chat_artifact_rejects_extension_changes() -> None:
    artifact = _bound_artifact()
    result = MagicMock()
    result.one_or_none.return_value = (artifact, "Quarterly review")
    db = MagicMock()
    db.execute = AsyncMock(return_value=result)

    with pytest.raises(HTTPException, match="existing extension"):
        await rename_chat_artifact(
            attachment_id=artifact.id,
            request=ChatArtifactUpdate(filename="Quarterly Report.html"),
            db=db,
            user=MagicMock(user_id=uuid4()),
        )


@pytest.mark.asyncio
async def test_delete_chat_artifact_removes_object_and_metadata() -> None:
    artifact = _bound_artifact()
    result = MagicMock()
    result.scalar_one_or_none.return_value = artifact
    db = MagicMock()
    db.execute = AsyncMock(return_value=result)
    db.delete = AsyncMock()
    db.flush = AsyncMock()
    storage = AsyncMock()

    with patch(
        "src.services.file_storage.service.get_file_storage_service",
        return_value=storage,
    ):
        response = await delete_chat_artifact(
            attachment_id=artifact.id,
            db=db,
            user=MagicMock(user_id=uuid4()),
        )

    assert response.status_code == 204
    storage.delete_raw_from_s3.assert_awaited_once_with(artifact.s3_key)
    db.delete.assert_awaited_once_with(artifact)
