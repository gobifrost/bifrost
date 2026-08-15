from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from src.services.chat_artifacts import promote_artifact_refs


@pytest.mark.asyncio
async def test_promote_artifact_ref_copies_scoped_file_into_chat_storage() -> None:
    organization_id = uuid4()
    conversation_id = uuid4()
    message_id = uuid4()
    attachment_id = uuid4()
    db = MagicMock()
    query_result = MagicMock()
    query_result.scalar_one_or_none.return_value = organization_id
    db.execute = AsyncMock(return_value=query_result)

    storage = AsyncMock()
    storage.read_uploaded_file.return_value = b"%PDF-promoted"
    attachment = MagicMock(
        id=attachment_id,
        filename="brief.pdf",
        content_type="application/pdf",
        size_bytes=13,
        conversation_id=conversation_id,
        created_at=datetime.now(UTC),
    )
    attachment_service = MagicMock()
    attachment_service.store_generated = AsyncMock(return_value=attachment)

    result = {
        "output": {
            "type": "bifrost_artifact",
            "filename": "brief.pdf",
            "content_type": "application/pdf",
            "size_bytes": 13,
            "path": "artifacts/run-1/brief.pdf",
            "location": "temp",
            "scope": str(organization_id),
        }
    }
    with (
        patch(
            "src.services.chat_artifacts.get_file_storage_service",
            return_value=storage,
        ),
        patch(
            "src.services.chat_artifacts.ChatAttachmentService",
            return_value=attachment_service,
        ),
    ):
        promoted = await promote_artifact_refs(
            db,
            result=result,
            conversation_id=conversation_id,
            conversation_user_id=uuid4(),
            message_id=message_id,
            agent_organization_id=None,
        )

    storage.read_uploaded_file.assert_awaited_once_with(
        f"_tmp/{organization_id}/artifacts/run-1/brief.pdf"
    )
    attachment_service.store_generated.assert_awaited_once_with(
        conversation_id=conversation_id,
        message_id=message_id,
        filename="brief.pdf",
        content_type="application/pdf",
        content=b"%PDF-promoted",
    )
    assert promoted[0].attachment_id == str(attachment_id)


@pytest.mark.asyncio
async def test_promote_artifact_ref_rejects_cross_organization_scope() -> None:
    db = MagicMock()
    query_result = MagicMock()
    query_result.scalar_one_or_none.return_value = uuid4()
    db.execute = AsyncMock(return_value=query_result)

    with pytest.raises(ValueError, match="different organization"):
        await promote_artifact_refs(
            db,
            result={
                "type": "bifrost_artifact",
                "filename": "brief.pdf",
                "content_type": "application/pdf",
                "size_bytes": 13,
                "path": "artifacts/brief.pdf",
                "location": "temp",
                "scope": str(uuid4()),
            },
            conversation_id=uuid4(),
            conversation_user_id=uuid4(),
            message_id=uuid4(),
            agent_organization_id=None,
        )
