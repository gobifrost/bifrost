"""Artifact workspace tombstones hide paths without deleting artifacts."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from src.models.orm import Artifact, ArtifactWorkspaceTombstone
from src.services.artifacts import ArtifactAccessError, ArtifactService


class _Result:
    def __init__(self, rows=None, scalar=None) -> None:
        self._rows = rows or []
        self._scalar = scalar

    def scalars(self):
        return SimpleNamespace(all=lambda: self._rows)

    def scalar_one_or_none(self):
        return self._scalar


def _artifact(*, workspace_id, path, created_at) -> Artifact:
    return Artifact(
        id=uuid4(),
        workspace_id=workspace_id,
        logical_path=path,
        organization_id=None,
        created_by_user_id=uuid4(),
        s3_key=f"_artifact_workspaces/{workspace_id}/{uuid4()}/{path}",
        filename=path.rsplit("/", 1)[-1],
        content_type="text/plain",
        size_bytes=4,
        created_at=created_at,
    )


def _tombstone(*, workspace_id, path, created_at) -> ArtifactWorkspaceTombstone:
    return ArtifactWorkspaceTombstone(
        id=uuid4(),
        workspace_id=workspace_id,
        logical_path=path,
        created_by_user_id=uuid4(),
        organization_id=None,
        created_at=created_at,
    )


@pytest.mark.asyncio
async def test_list_workspace_hides_path_when_tombstone_is_newer() -> None:
    workspace_id = uuid4()
    user_id = uuid4()
    now = datetime(2026, 8, 20, tzinfo=UTC)
    old = _artifact(workspace_id=workspace_id, path="draft.txt", created_at=now)
    old.created_by_user_id = user_id
    visible = _artifact(
        workspace_id=workspace_id,
        path="kept.txt",
        created_at=now,
    )
    visible.created_by_user_id = user_id
    tombstone = _tombstone(
        workspace_id=workspace_id,
        path="draft.txt",
        created_at=now + timedelta(seconds=1),
    )
    db = MagicMock()
    db.execute = AsyncMock(
        side_effect=[
            _Result(rows=[old, visible]),
            _Result(rows=[tombstone]),
        ]
    )

    listed = await ArtifactService(db).list_workspace(
        workspace_id,
        user_id=user_id,
        organization_id=None,
    )

    assert listed == [visible]


@pytest.mark.asyncio
async def test_list_workspace_restores_path_when_artifact_is_newer_than_tombstone() -> None:
    workspace_id = uuid4()
    user_id = uuid4()
    now = datetime(2026, 8, 20, tzinfo=UTC)
    restored = _artifact(
        workspace_id=workspace_id,
        path="draft.txt",
        created_at=now + timedelta(seconds=2),
    )
    restored.created_by_user_id = user_id
    tombstone = _tombstone(
        workspace_id=workspace_id,
        path="draft.txt",
        created_at=now + timedelta(seconds=1),
    )
    db = MagicMock()
    db.execute = AsyncMock(
        side_effect=[
            _Result(rows=[restored]),
            _Result(rows=[tombstone]),
        ]
    )

    listed = await ArtifactService(db).list_workspace(
        workspace_id,
        user_id=user_id,
        organization_id=None,
    )

    assert listed == [restored]


@pytest.mark.asyncio
async def test_resolve_workspace_path_rejects_tombstoned_latest_artifact() -> None:
    workspace_id = uuid4()
    user_id = uuid4()
    now = datetime(2026, 8, 20, tzinfo=UTC)
    artifact = _artifact(workspace_id=workspace_id, path="draft.txt", created_at=now)
    artifact.created_by_user_id = user_id
    tombstone = _tombstone(
        workspace_id=workspace_id,
        path="draft.txt",
        created_at=now + timedelta(seconds=1),
    )
    db = MagicMock()
    db.execute = AsyncMock(
        side_effect=[
            _Result(scalar=artifact),
            _Result(scalar=tombstone),
        ]
    )

    with pytest.raises(ArtifactAccessError, match="draft.txt"):
        await ArtifactService(db).resolve_workspace_path(
            workspace_id,
            "draft.txt",
            user_id=user_id,
            organization_id=None,
        )


@pytest.mark.asyncio
async def test_old_artifact_ref_remains_authorized_and_readable_after_tombstone() -> None:
    workspace_id = uuid4()
    user_id = uuid4()
    artifact = _artifact(
        workspace_id=workspace_id,
        path="draft.txt",
        created_at=datetime(2026, 8, 20, tzinfo=UTC),
    )
    artifact.created_by_user_id = user_id
    db = MagicMock()
    db.execute = AsyncMock(return_value=_Result(scalar=artifact))
    storage = AsyncMock()
    storage.read_uploaded_file = AsyncMock(return_value=b"old")

    authorized = await ArtifactService(db).get_authorized(
        artifact.id,
        user_id=user_id,
        organization_id=None,
    )
    with patch(
        "src.services.artifacts.get_file_storage_service",
        return_value=storage,
    ):
        content = await ArtifactService(db).read(authorized)

    assert authorized is artifact
    assert content == b"old"
    storage.read_uploaded_file.assert_awaited_once_with(artifact.s3_key)
