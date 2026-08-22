from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from shared.artifact_generation import GeneratedArtifact
from src.services.chat_artifacts import (
    CREATE_DOCUMENT_TOOL,
    CREATE_IMAGE_TOOL,
    CREATE_VIDEO_TOOL,
    artifact_tool_definitions,
    execute_artifact_tool,
    promote_artifact_refs,
)


def test_chat_artifact_tools_include_dedicated_media_generators() -> None:
    definitions = {
        definition.name: definition
        for definition in artifact_tool_definitions(
            image_generation_enabled=True,
            video_generation_enabled=True,
        )
    }

    assert CREATE_IMAGE_TOOL in definitions
    assert definitions[CREATE_IMAGE_TOOL].parameters["required"] == ["filename", "prompt"]
    assert CREATE_VIDEO_TOOL in definitions
    assert definitions[CREATE_VIDEO_TOOL].parameters["required"] == ["filename", "prompt"]


def test_chat_artifact_tools_hide_unconfigured_media_generators() -> None:
    definitions = {
        definition.name
        for definition in artifact_tool_definitions(
            image_generation_enabled=False,
            video_generation_enabled=False,
        )
    }

    assert CREATE_IMAGE_TOOL not in definitions
    assert CREATE_VIDEO_TOOL not in definitions


@pytest.mark.asyncio
async def test_document_tool_reads_prior_workspace_image() -> None:
    conversation_id = uuid4()
    message_id = uuid4()
    user_id = uuid4()
    conversation = MagicMock(id=conversation_id, user_id=user_id)
    user = MagicMock(id=user_id, organization_id=uuid4(), is_superuser=False)
    db = MagicMock()
    db.get = AsyncMock(side_effect=[conversation, user])
    stored_image = MagicMock(content_type="image/png")
    service = MagicMock()
    service.resolve_workspace_path = AsyncMock(return_value=stored_image)
    service.read = AsyncMock(return_value=b"png")
    attachment = MagicMock()
    attachment.artifact = MagicMock(
        id=uuid4(),
        filename="Bluetick Guide.pdf",
        content_type="application/pdf",
        size_bytes=12,
    )
    attachment_service = MagicMock()
    attachment_service.store_generated = AsyncMock(return_value=attachment)
    generated = GeneratedArtifact(
        filename="Bluetick Guide.pdf",
        content_type="application/pdf",
        content=b"%PDF-content",
    )

    with (
        patch("src.services.chat_artifacts.ArtifactService", return_value=service),
        patch(
            "src.services.chat_artifacts.ChatAttachmentService",
            return_value=attachment_service,
        ),
        patch(
            "src.services.chat_artifacts.generate_document_with_images",
            return_value=generated,
        ) as render,
    ):
        _, ref = await execute_artifact_tool(
            db,
            tool_name=CREATE_DOCUMENT_TOOL,
            arguments={
                "filename": "Bluetick Guide",
                "format": "pdf",
                "title": "Bluetick Guide",
                "sections": [
                    {"images": [{"path": "Bluetick Portrait.png"}]}
                ],
            },
            conversation_id=conversation_id,
            message_id=message_id,
        )

    service.resolve_workspace_path.assert_awaited_once_with(
        conversation_id,
        "Bluetick Portrait.png",
        user_id=user_id,
        organization_id=user.organization_id,
        is_platform_admin=False,
    )
    render.assert_called_once()
    assert render.call_args.args[1] == {"Bluetick Portrait.png": b"png"}
    assert ref.filename == "Bluetick Guide.pdf"


@pytest.mark.asyncio
async def test_video_artifact_tool_enqueues_shared_platform_job() -> None:
    conversation_id = uuid4()
    message_id = uuid4()
    user_id = uuid4()
    conversation = MagicMock(id=conversation_id, user_id=user_id)
    user = MagicMock(
        id=user_id,
        organization_id=uuid4(),
        email="user@example.com",
        name="Example User",
    )
    db = MagicMock()
    db.get = AsyncMock(side_effect=[conversation, user])
    db.commit = AsyncMock()
    db.refresh = AsyncMock()
    job = MagicMock(id=uuid4(), status="queued", notification_id=uuid4())

    with (
        patch(
            "src.services.platform_jobs.enqueue_platform_job",
            AsyncMock(return_value=(job, False)),
        ) as enqueue,
        patch(
            "src.services.platform_jobs.publish_platform_job_update",
            AsyncMock(),
        ) as publish,
    ):
        attachment, result = await execute_artifact_tool(
            db,
            tool_name=CREATE_VIDEO_TOOL,
            arguments={"filename": "launch-loop", "prompt": "A launch loop"},
            conversation_id=conversation_id,
            message_id=message_id,
        )

    assert attachment is None
    assert isinstance(result, dict)
    assert result["type"] == "platform_job"
    assert result["job_id"] == str(job.id)
    enqueue.assert_awaited_once()
    db.commit.assert_awaited_once()
    publish.assert_awaited_once_with(job)


@pytest.mark.asyncio
async def test_promote_artifact_ref_attaches_same_identity_to_chat() -> None:
    organization_id = uuid4()
    conversation_id = uuid4()
    message_id = uuid4()
    artifact_id = uuid4()
    conversation_user_id = uuid4()
    db = MagicMock()
    user_org_result = MagicMock()
    user_org_result.scalar_one_or_none.return_value = organization_id
    artifact = MagicMock(
        id=artifact_id,
        filename="brief.pdf",
        content_type="application/pdf",
        size_bytes=13,
    )
    artifact_result = MagicMock()
    artifact_result.scalar_one_or_none.return_value = artifact
    db.execute = AsyncMock(side_effect=[user_org_result, artifact_result])

    attachment_service = MagicMock()
    attachment_service.attach_artifact = AsyncMock()

    result = {
        "output": {
            "type": "bifrost_artifact",
            "id": str(artifact_id),
            "filename": "brief.pdf",
            "content_type": "application/pdf",
            "size_bytes": 13,
        }
    }
    with patch(
        "src.services.chat_artifacts.ChatAttachmentService",
        return_value=attachment_service,
    ):
        promoted = await promote_artifact_refs(
            db,
            result=result,
            conversation_id=conversation_id,
            conversation_user_id=conversation_user_id,
            message_id=message_id,
            agent_organization_id=None,
        )

    attachment_service.attach_artifact.assert_awaited_once_with(
        artifact=artifact,
        conversation_id=conversation_id,
        message_id=message_id,
    )
    assert promoted[0].id == str(artifact_id)


@pytest.mark.asyncio
async def test_promote_artifact_ref_rejects_artifact_outside_chat_scope() -> None:
    db = MagicMock()
    user_org_result = MagicMock()
    user_org_result.scalar_one_or_none.return_value = uuid4()
    artifact_result = MagicMock()
    artifact_result.scalar_one_or_none.return_value = None
    db.execute = AsyncMock(side_effect=[user_org_result, artifact_result])

    with pytest.raises(ValueError, match="not found or is outside"):
        await promote_artifact_refs(
            db,
            result={
                "type": "bifrost_artifact",
                "id": str(uuid4()),
                "filename": "brief.pdf",
                "content_type": "application/pdf",
                "size_bytes": 13,
            },
            conversation_id=uuid4(),
            conversation_user_id=uuid4(),
            message_id=uuid4(),
            agent_organization_id=None,
        )
