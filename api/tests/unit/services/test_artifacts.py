from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from src.models.orm import Artifact
from src.services.artifacts import ArtifactAccessError, ArtifactService, artifact_ref
from src.core.principal import UserPrincipal
from src.services.authorization.context import AuthorizationContext
from src.services.authorization.enforce import Caller


def _caller(*, original_user_id=None, actor_user_id=None, organization_id=None) -> Caller:
    original_user_id = original_user_id or uuid4()
    actor_user_id = actor_user_id or original_user_id
    organization_id = organization_id or uuid4()
    principal = UserPrincipal(
        user_id=actor_user_id,
        email="actor@example.test",
        organization_id=organization_id,
        is_active=True,
        is_verified=True,
    )
    return Caller(
        principal,
        AuthorizationContext(
            user_id=original_user_id,
            home_organization_id=organization_id,
            base_role_id=uuid4(),
            is_external=False,
            base_permissions=frozenset(),
        ),
    )


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
        artifact = await ArtifactService(db).store_trusted_job(
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
    result = MagicMock()
    result.scalar_one_or_none.return_value = None
    db.execute = AsyncMock(return_value=result)
    storage = AsyncMock()
    workspace_id = uuid4()

    with patch(
        "src.services.artifacts.get_file_storage_service",
        return_value=storage,
    ):
        artifact = await ArtifactService(db).store_trusted_job(
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
async def test_get_for_caller_hides_missing_or_out_of_scope_artifact() -> None:
    db = MagicMock()
    db.scalar = AsyncMock(return_value=None)
    caller = _caller()

    with patch("src.services.artifacts.refresh_artifact_caller", AsyncMock(return_value=caller)), pytest.raises(
        ArtifactAccessError, match="not found"
    ):
        await ArtifactService(db).get_for_caller(uuid4(), caller=caller)


@pytest.mark.asyncio
async def test_run_as_actor_artifact_is_not_implicitly_owned_by_original_user() -> None:
    """Effects attribution never becomes original-user private ownership."""
    db = MagicMock()
    original_user_id, actor_user_id, organization_id = uuid4(), uuid4(), uuid4()
    caller = _caller(
        original_user_id=original_user_id,
        actor_user_id=actor_user_id,
        organization_id=organization_id,
    )
    db.scalar = AsyncMock(
        return_value=Artifact(
            id=uuid4(),
            created_by_user_id=actor_user_id,
            organization_id=organization_id,
            s3_key="_artifacts/run-as.pdf",
            filename="Run As.pdf",
            content_type="application/pdf",
            size_bytes=1,
        )
    )

    with patch("src.services.artifacts.refresh_artifact_caller", AsyncMock(return_value=caller)), pytest.raises(
        ArtifactAccessError, match="not found"
    ):
        await ArtifactService(db).get_for_caller(uuid4(), caller=caller)


@pytest.mark.asyncio
async def test_store_refuses_mixed_organization_workspace_before_persistence() -> None:
    """Legacy mixed workspaces have no trustworthy single write target."""
    db = MagicMock()
    caller = _caller()
    result = MagicMock()
    result.scalars.return_value.all.return_value = [
        Artifact(
            id=uuid4(), created_by_user_id=caller.ctx.user_id,
            organization_id=uuid4(), s3_key="_artifact_workspaces/a/one",
            filename="one.txt", content_type="text/plain", size_bytes=1,
        ),
        Artifact(
            id=uuid4(), created_by_user_id=caller.ctx.user_id,
            organization_id=uuid4(), s3_key="_artifact_workspaces/a/two",
            filename="two.txt", content_type="text/plain", size_bytes=1,
        ),
    ]
    db.execute = AsyncMock(return_value=result)
    storage = AsyncMock()

    with patch(
        "src.services.artifacts.get_file_storage_service",
        return_value=storage,
    ), patch("src.services.artifacts.refresh_artifact_caller", AsyncMock(return_value=caller)), pytest.raises(
        ArtifactAccessError, match="workspace is not accessible"
    ):
        await ArtifactService(db).store_for_caller(
            caller,
            filename="Portrait.png",
            content_type="image/png",
            content=b"png-data",
            workspace_id=uuid4(),
            logical_path="images/Portrait.png",
        )
    storage.write_raw_to_s3.assert_not_awaited()
