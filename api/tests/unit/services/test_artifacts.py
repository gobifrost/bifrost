from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from src.models.orm import Artifact
from src.services.artifacts import ArtifactAccessError, ArtifactService, artifact_ref


@pytest.mark.asyncio
async def test_store_returns_opaque_reference_and_persists_bytes() -> None:
    db = MagicMock()
    db.flush = AsyncMock()
    storage = AsyncMock()
    user_id = uuid4()
    organization_id = uuid4()

    with patch(
        "src.services.artifacts.get_file_storage_service",
        return_value=storage,
    ):
        artifact = await ArtifactService(db).store(
            filename="Network Diagram.png",
            content_type="image/png",
            content=b"png-data",
            created_by_user_id=user_id,
            organization_id=organization_id,
        )

    ref = artifact_ref(artifact)
    assert ref.id == str(artifact.id)
    assert ref.model_dump(mode="json") == {
        "type": "bifrost_artifact",
        "id": str(artifact.id),
        "filename": "Network Diagram.png",
        "content_type": "image/png",
        "size_bytes": 8,
    }
    storage.write_raw_to_s3.assert_awaited_once_with(
        artifact.s3_key,
        b"png-data",
    )


@pytest.mark.asyncio
async def test_store_places_logical_file_in_workspace_prefix() -> None:
    db = MagicMock()
    db.flush = AsyncMock()
    storage = AsyncMock()
    workspace_id = uuid4()

    with patch(
        "src.services.artifacts.get_file_storage_service",
        return_value=storage,
    ):
        artifact = await ArtifactService(db).store(
            filename="Portrait.png",
            content_type="image/png",
            content=b"png-data",
            created_by_user_id=uuid4(),
            organization_id=uuid4(),
            workspace_id=workspace_id,
            logical_path="images/Portrait.png",
        )

    assert artifact.workspace_id == workspace_id
    assert artifact.logical_path == "images/Portrait.png"
    assert artifact.s3_key.startswith(f"_artifact_workspaces/{workspace_id}/")


@pytest.mark.asyncio
async def test_read_and_delete_resolve_canonical_storage_record() -> None:
    db = MagicMock()
    db.delete = AsyncMock()
    db.flush = AsyncMock()
    storage = AsyncMock()
    storage.read_uploaded_file.return_value = b"artifact-data"
    artifact = Artifact(
        id=uuid4(),
        created_by_user_id=uuid4(),
        organization_id=uuid4(),
        s3_key="_artifacts/artifact-file",
        filename="Artifact.pdf",
        content_type="application/pdf",
        size_bytes=13,
    )

    with patch(
        "src.services.artifacts.get_file_storage_service",
        return_value=storage,
    ):
        service = ArtifactService(db)
        assert await service.read(artifact) == b"artifact-data"
        await service.delete(artifact)

    storage.read_uploaded_file.assert_awaited_once_with(artifact.s3_key)
    storage.delete_raw_from_s3.assert_awaited_once_with(artifact.s3_key)
    db.delete.assert_awaited_once_with(artifact)


@pytest.mark.asyncio
async def test_get_authorized_hides_missing_or_out_of_scope_artifact() -> None:
    db = MagicMock()
    result = MagicMock()
    result.scalar_one_or_none.return_value = None
    db.execute = AsyncMock(return_value=result)

    with pytest.raises(ArtifactAccessError, match="not found"):
        await ArtifactService(db).get_authorized(
            uuid4(),
            user_id=uuid4(),
            organization_id=uuid4(),
        )
