"""Upload limits for workspace-bundle preview staging."""
from __future__ import annotations

import pytest
from fastapi import HTTPException
from starlette.responses import Response
from types import SimpleNamespace
from uuid import uuid4


@pytest.mark.asyncio
async def test_workspace_bundle_spool_rejects_compressed_upload_before_writing_past_cap(monkeypatch) -> None:
    from src.routers import solutions

    class Upload:
        def __init__(self) -> None:
            self.chunks = iter((b"a" * 6, b"b" * 6, b""))

        async def read(self, _size: int) -> bytes:
            return next(self.chunks)

    monkeypatch.setattr(solutions, "MAX_SOLUTION_ARCHIVE_BYTES", 10)

    with pytest.raises(HTTPException) as error:
        await solutions._spool_upload_to_temp(Upload(), prefix="bundle-cap-")

    assert error.value.status_code == 413
    assert "compressed upload limit" in str(error.value.detail)


@pytest.mark.asyncio
async def test_workspace_bundle_rejects_different_decisions_for_an_active_preview(monkeypatch) -> None:
    from src.models.contracts.solutions import (
        WorkspaceBundleImportRequest,
        WorkspaceBundleItem,
        WorkspaceBundlePreview,
    )
    from src.routers import solutions

    preview_id = uuid4()
    preview = WorkspaceBundlePreview(
        preview_token=str(preview_id), package_name="Workspace", package_sha256="a" * 64,
        items=[WorkspaceBundleItem(
            id="entity:workflow:one", kind="workflow", name="one", classification="conflict",
            match_key="('workflows/one.py', 'one')", source_id=uuid4(), target_id=uuid4(),
        )],
    )

    class Storage:
        stage_calls = 0

        async def stage_metadata(self, _metadata) -> None:
            self.stage_calls += 1

    storage = Storage()
    metadata = {"preview": preview.model_dump(mode="json")}

    async def load_preview(*_args, **_kwargs):
        return preview_id, storage, metadata

    async def enqueue(*_args, **_kwargs):
        return SimpleNamespace(id=uuid4(), encrypted_payload=None, notification_id=uuid4()), True

    class Db:
        commits = 0

        async def commit(self) -> None:
            self.commits += 1

    db = Db()
    monkeypatch.setattr(solutions, "_load_workspace_preview_metadata", load_preview)
    monkeypatch.setattr(solutions, "enqueue_platform_job", enqueue)
    body = WorkspaceBundleImportRequest(
        preview_token=str(preview_id),
        decisions=[{"item_id": "entity:workflow:one", "action": "replace"}],
    )

    with pytest.raises(HTTPException, match="different decisions") as error:
        await solutions.enqueue_workspace_import(
            body, Response(), SimpleNamespace(db=db),
            SimpleNamespace(user_id=uuid4(), email="admin@example.com", name="Admin"),
        )

    assert error.value.status_code == 409
    assert storage.stage_calls == 0
    assert db.commits == 0
