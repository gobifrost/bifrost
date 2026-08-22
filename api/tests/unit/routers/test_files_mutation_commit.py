"""Transaction-order contracts for direct file mutations."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock
from uuid import UUID

import pytest

from src.routers import files
from src.services import file_policy_service


SOLUTION_ID = UUID("11111111-1111-1111-1111-111111111111")
ORG_ID = UUID("22222222-2222-2222-2222-222222222222")


@pytest.fixture
def mutation_context(monkeypatch):
    """Bypass authorization plumbing while preserving mutation ordering."""
    ctx = MagicMock()
    ctx.user.email = "admin@example.com"
    ctx.user.user_id = UUID("33333333-3333-3333-3333-333333333333")

    monkeypatch.setattr(files, "_resolve_effective_scope", lambda *_args: str(SOLUTION_ID))
    monkeypatch.setattr(files, "_ctx_solution_id", lambda *_args: SOLUTION_ID)
    monkeypatch.setattr(
        files,
        "_require_declared_solution_file_location",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(files, "_require_file_policy", AsyncMock(return_value=None))
    monkeypatch.setattr(files, "_lock_file_mutation", AsyncMock(return_value=None))
    monkeypatch.setattr(files, "_install_org_id", AsyncMock(return_value=ORG_ID))
    return ctx


@pytest.mark.asyncio
async def test_cloud_write_commits_metadata_before_publishing(monkeypatch, mutation_context):
    events: list[str] = []
    backend = MagicMock()
    backend.write = AsyncMock(side_effect=lambda *_args, **_kwargs: events.append("write"))
    storage = MagicMock()
    storage.record_file_write_metadata = AsyncMock(
        side_effect=lambda **_kwargs: events.append("metadata")
    )
    db = AsyncMock()
    db.commit = AsyncMock(side_effect=lambda: events.append("commit"))
    publish = AsyncMock(side_effect=lambda **_kwargs: events.append("publish"))

    monkeypatch.setattr(files, "get_backend", lambda *_args: backend)
    monkeypatch.setattr(files, "FileStorageService", lambda _db: storage)
    monkeypatch.setattr("src.core.pubsub.publish_file_change", publish)

    await files.write_file(
        files.FileWriteRequest(
            path="report.txt",
            content="ready",
            location="solutions",
        ),
        mutation_context,
        MagicMock(),
        db,
    )

    assert events == ["write", "metadata", "commit", "publish"]


@pytest.mark.asyncio
async def test_cloud_delete_commits_metadata_before_publishing(monkeypatch, mutation_context):
    events: list[str] = []
    backend = MagicMock()
    backend.delete = AsyncMock(side_effect=lambda *_args, **_kwargs: events.append("delete"))
    policy_service = MagicMock()
    policy_service.delete_metadata = AsyncMock(
        side_effect=lambda **_kwargs: events.append("metadata")
    )
    db = AsyncMock()
    db.commit = AsyncMock(side_effect=lambda: events.append("commit"))
    publish = AsyncMock(side_effect=lambda **_kwargs: events.append("publish"))

    monkeypatch.setattr(files, "get_backend", lambda *_args: backend)
    monkeypatch.setattr(file_policy_service, "FilePolicyService", lambda _db: policy_service)
    monkeypatch.setattr("src.core.pubsub.publish_file_change", publish)

    await files.delete_file(
        files.FileDeleteRequest(path="report.txt", location="solutions"),
        mutation_context,
        MagicMock(),
        db,
    )

    assert events == ["delete", "metadata", "commit", "publish"]
