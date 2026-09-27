"""Transaction-order contracts for direct file mutations.

The write/delete business logic lives in ``shared.sdk_files``; these tests
drive the real HTTP handlers end to end (through the thin adapter into the
service) and assert cloud metadata is committed before publishing, for both
``files.write`` and ``files.delete``. Delegation and error mapping are
covered alongside.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock
from uuid import UUID

import pytest
from fastapi import HTTPException

from shared.file_access import FileServiceError
from src.routers import files


SOLUTION_ID = UUID("11111111-1111-1111-1111-111111111111")
ORG_ID = UUID("22222222-2222-2222-2222-222222222222")


@pytest.fixture
def mutation_context(monkeypatch):
    """Bypass service authorization plumbing while preserving mutation ordering.

    The HTTP handler builds its own ``FileCaller`` from ``ctx`` — point
    ``ctx.db`` at the same AsyncMock session the test observes so commit
    ordering is visible.
    """
    db = AsyncMock()
    ctx = MagicMock()
    ctx.db = db
    ctx.user.email = "admin@example.com"
    ctx.user.user_id = UUID("33333333-3333-3333-3333-333333333333")

    monkeypatch.setattr(
        "shared.sdk_files.resolve_effective_scope",
        lambda *_args: str(SOLUTION_ID),
    )
    monkeypatch.setattr(
        "shared.sdk_files.ctx_solution_id", lambda *_args: SOLUTION_ID
    )
    monkeypatch.setattr(
        "shared.sdk_files.require_declared_solution_file_location",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(
        "shared.sdk_files.require_file_policy", AsyncMock(return_value=None)
    )
    monkeypatch.setattr(
        "shared.sdk_files.install_org_id", AsyncMock(return_value=ORG_ID)
    )
    return ctx, db


@pytest.mark.asyncio
async def test_cloud_write_commits_metadata_before_publishing(
    monkeypatch, mutation_context
):
    from shared import sdk_files as sdk_files_module

    events: list[str] = []
    backend = MagicMock()
    backend.write = AsyncMock(side_effect=lambda *_args, **_kwargs: events.append("write"))
    storage = MagicMock()
    storage.record_file_write_metadata = AsyncMock(
        side_effect=lambda **_kwargs: events.append("metadata")
    )
    ctx, db = mutation_context
    db.commit = AsyncMock(side_effect=lambda: events.append("commit"))
    publish = AsyncMock(side_effect=lambda **_kwargs: events.append("publish"))

    monkeypatch.setattr(
        "src.services.file_backend.get_backend", lambda *_args: backend
    )
    monkeypatch.setattr(
        "src.services.file_storage.FileStorageService", lambda _db: storage
    )
    monkeypatch.setattr("src.core.pubsub.publish_file_change", publish)
    monkeypatch.setattr(
        sdk_files_module, "lock_file_mutation", AsyncMock(return_value=None)
    )

    await files.write_file(
        files.FileWriteRequest(
            path="report.txt",
            content="ready",
            location="solutions",
        ),
        ctx,
        MagicMock(),
        db,
    )

    assert events == ["write", "metadata", "commit", "publish"]


@pytest.mark.asyncio
async def test_cloud_delete_commits_metadata_before_publishing(
    monkeypatch, mutation_context
):
    from shared import sdk_files as sdk_files_module
    from src.services import file_policy_service

    events: list[str] = []
    backend = MagicMock()
    backend.delete = AsyncMock(
        side_effect=lambda *_args, **_kwargs: events.append("delete")
    )
    policy_service = MagicMock()
    policy_service.delete_metadata = AsyncMock(
        side_effect=lambda **_kwargs: events.append("metadata")
    )
    ctx, db = mutation_context
    db.commit = AsyncMock(side_effect=lambda: events.append("commit"))
    publish = AsyncMock(side_effect=lambda **_kwargs: events.append("publish"))

    monkeypatch.setattr(
        "src.services.file_backend.get_backend", lambda *_args: backend
    )
    monkeypatch.setattr(
        file_policy_service, "FilePolicyService", lambda _db: policy_service
    )
    monkeypatch.setattr("src.core.pubsub.publish_file_change", publish)
    monkeypatch.setattr(
        sdk_files_module, "lock_file_mutation", AsyncMock(return_value=None)
    )

    await files.delete_file(
        files.FileDeleteRequest(path="report.txt", location="solutions"),
        ctx,
        MagicMock(),
        db,
    )

    assert events == ["delete", "metadata", "commit", "publish"]


@pytest.mark.asyncio
async def test_write_adapter_delegates_and_maps_errors(monkeypatch) -> None:
    """The HTTP route stays a thin adapter: DTO fields passed through,
    service errors mapped to HTTP status."""
    from shared import sdk_files as sdk_files_module

    seen: dict = {}
    service = AsyncMock(
        side_effect=lambda caller, **kwargs: seen.update(kwargs)
    )
    monkeypatch.setattr(sdk_files_module, "sdk_write_file", service)
    ctx = MagicMock()

    await files.write_file(
        files.FileWriteRequest(
            path="a.txt",
            content="hi",
            binary=False,
            location="reports",
            scope="scope-1",
            mode="local",
            expected_version="v1",
            create_only=False,
        ),
        ctx,
        MagicMock(),
        MagicMock(),
    )

    assert seen == {
        "path": "a.txt",
        "content": "hi",
        "binary": False,
        "location": "reports",
        "scope": "scope-1",
        "mode": "local",
        "expected_version": "v1",
        "create_only": False,
    }

    async def _conflict(*args, **kwargs):
        raise FileServiceError(409, {"reason": "version_conflict"})

    monkeypatch.setattr(sdk_files_module, "sdk_write_file", _conflict)
    with pytest.raises(HTTPException) as exc:
        await files.write_file(
            files.FileWriteRequest(path="a.txt", content="hi", location="reports"),
            MagicMock(),
            MagicMock(),
            MagicMock(),
        )
    assert exc.value.status_code == 409
    assert exc.value.detail == {"reason": "version_conflict"}


@pytest.mark.asyncio
async def test_delete_adapter_delegates_and_maps_errors(monkeypatch) -> None:
    from shared import sdk_files as sdk_files_module

    seen: dict = {}
    service = AsyncMock(
        side_effect=lambda caller, **kwargs: seen.update(kwargs)
    )
    monkeypatch.setattr(sdk_files_module, "sdk_delete_file", service)

    await files.delete_file(
        files.FileDeleteRequest(
            path="a.txt", location="reports", scope="scope-1", mode="local"
        ),
        MagicMock(),
        MagicMock(),
        MagicMock(),
    )

    assert seen == {
        "path": "a.txt",
        "location": "reports",
        "scope": "scope-1",
        "mode": "local",
        "expected_version": None,
    }

    async def _missing(*args, **kwargs):
        raise FileServiceError(404, "File not found: a.txt")

    monkeypatch.setattr(sdk_files_module, "sdk_delete_file", _missing)
    with pytest.raises(HTTPException) as exc:
        await files.delete_file(
            files.FileDeleteRequest(path="a.txt", location="reports"),
            MagicMock(),
            MagicMock(),
            MagicMock(),
        )
    assert exc.value.status_code == 404
