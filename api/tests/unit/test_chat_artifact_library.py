from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from fastapi import HTTPException

from shared.builtin_roles import USER_ROLE_ID
from src.core.principal import UserPrincipal
from src.models.contracts.agents import ChatArtifactUpdate
from src.models.enums import MessageRole
from src.models.orm import Artifact, MessageAttachment
from src.routers.chat import delete_chat_artifact, list_chat_artifacts, rename_chat_artifact
from src.services.authorization.context import AuthorizationContext
from src.services.authorization.enforce import Caller


def _owner(artifact: Artifact) -> UserPrincipal:
    return UserPrincipal(user_id=artifact.created_by_user_id, email="owner@contoso.example", organization_id=None)


@pytest.fixture(autouse=True)
def current_caller(monkeypatch):
    async def load(_db, principal):
        return Caller(principal, AuthorizationContext(
            user_id=principal.user_id,
            home_organization_id=principal.organization_id,
            base_role_id=USER_ROLE_ID,
            is_external=False,
            base_permissions=frozenset(),
        ))

    monkeypatch.setattr("src.routers.chat.load_caller", load)


def _artifact() -> Artifact:
    return Artifact(
        id=uuid4(),
        created_by_user_id=uuid4(),
        workspace_id=uuid4(),
        logical_path="Quarterly Report.pdf",
        s3_key="_artifact_workspaces/conversation/artifact/Quarterly Report.pdf",
        filename="Quarterly Report.pdf",
        content_type="application/pdf",
        size_bytes=2048,
        created_at=datetime(2026, 8, 15, tzinfo=UTC),
    )


def _binding(artifact: Artifact) -> MessageAttachment:
    return MessageAttachment(
        id=artifact.id,
        artifact_id=artifact.id,
        message_id=uuid4(),
        conversation_id=uuid4(),
        s3_key=artifact.s3_key,
        filename=artifact.filename,
        content_type=artifact.content_type,
        size_bytes=artifact.size_bytes,
        created_at=datetime(2026, 8, 15, tzinfo=UTC),
    )


@pytest.mark.asyncio
async def test_list_chat_artifacts_returns_user_owned_file_context() -> None:
    artifact = _artifact()
    binding = _binding(artifact)
    artifact_result = MagicMock()
    artifact_result.scalars.return_value.all.return_value = [artifact]
    binding_result = MagicMock()
    binding_result.all.return_value = [
        (binding, "Quarterly review", MessageRole.TOOL_CALL)
    ]
    db = MagicMock()
    db.execute = AsyncMock(side_effect=[artifact_result, binding_result])
    user = _owner(artifact)

    files = await list_chat_artifacts(db=db, user=user)

    assert len(files) == 1
    assert files[0].kind == "artifact"
    assert files[0].conversation_title == "Quarterly review"
    assert files[0].message_id == binding.message_id


@pytest.mark.asyncio
async def test_rename_chat_artifact_updates_only_display_metadata() -> None:
    artifact = _artifact()
    binding = _binding(artifact)
    original_key = artifact.s3_key
    artifact_result = MagicMock()
    artifact_result.scalar_one_or_none.return_value = artifact
    update_result = MagicMock()
    binding_result = MagicMock()
    binding_result.one_or_none.return_value = (
        binding,
        "Quarterly review",
        MessageRole.TOOL_CALL,
    )
    db = MagicMock()
    db.execute = AsyncMock(
        side_effect=[artifact_result, update_result, binding_result]
    )
    db.flush = AsyncMock()

    renamed = await rename_chat_artifact(
        attachment_id=artifact.id,
        request=ChatArtifactUpdate(filename="Executive Summary.pdf"),
        db=db,
        user=_owner(artifact),
    )

    assert renamed.filename == "Executive Summary.pdf"
    assert artifact.s3_key == original_key
    db.flush.assert_awaited_once()


@pytest.mark.asyncio
async def test_rename_chat_artifact_rejects_extension_changes() -> None:
    artifact = _artifact()
    result = MagicMock()
    result.scalar_one_or_none.return_value = artifact
    db = MagicMock()
    db.execute = AsyncMock(return_value=result)

    with pytest.raises(HTTPException, match="existing extension"):
        await rename_chat_artifact(
            attachment_id=artifact.id,
            request=ChatArtifactUpdate(filename="Quarterly Report.html"),
            db=db,
            user=_owner(artifact),
        )


@pytest.mark.asyncio
async def test_delete_chat_artifact_removes_object_and_metadata() -> None:
    artifact = _artifact()
    db = MagicMock()
    db.scalar = AsyncMock(return_value=artifact)
    db.delete = AsyncMock()
    db.flush = AsyncMock()
    storage = AsyncMock()

    with patch(
        "src.services.artifacts.get_file_storage_service",
        return_value=storage,
    ):
        response = await delete_chat_artifact(
            attachment_id=artifact.id,
            db=db,
            user=_owner(artifact),
        )

    assert response.status_code == 204
    storage.delete_raw_from_s3.assert_awaited_once_with(artifact.s3_key)
    db.delete.assert_awaited_once_with(artifact)
