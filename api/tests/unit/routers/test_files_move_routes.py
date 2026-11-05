"""files.move routes: request bounds, commit-before-202, Location, error mapping."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from fastapi import HTTPException, Response
from pydantic import ValidationError

from shared.file_access import FileServiceError
from src.routers import files


def test_batch_request_bounds() -> None:
    item = {"source": "a", "destination": "b"}
    with pytest.raises(ValidationError):
        files.FileMoveBatchRequest(items=[], location="docs")
    with pytest.raises(ValidationError):
        files.FileMoveBatchRequest(items=[item] * 501, location="docs")
    with pytest.raises(ValidationError):
        files.FileMoveBatchRequest(
            items=[{"source": "a" * 100_000, "destination": "b" * 100_001}], location="docs"
        )
    assert files.FileMoveBatchRequest(items=[item] * 500, location="docs").on_conflict == "error"
    assert "mode" not in files.FileMoveBatchRequest.model_fields
    assert "mode" not in files.FileMoveRequest.model_fields


@pytest.fixture
def ctx():
    context = MagicMock()
    context.db = AsyncMock()
    return context


async def test_move_many_finalizes_before_returning_202(monkeypatch, ctx) -> None:
    events: list[str] = []
    job = SimpleNamespace(id=uuid4(), notification_id=uuid4(), status="queued")
    enqueue = AsyncMock(side_effect=lambda *_a, **_k: events.append("enqueue") or (job, False))
    finalize = AsyncMock(side_effect=lambda *_a, **_k: events.append("finalize"))
    monkeypatch.setattr("shared.sdk_files.sdk_enqueue_file_moves", enqueue)
    monkeypatch.setattr("src.services.platform_jobs.finalize_enqueued_platform_job", finalize)
    response = Response()

    accepted = await files.move_files(
        files.FileMoveBatchRequest(
            items=[{"source": "a/1.pdf", "destination": "b/1.pdf"}], location="docs", on_conflict="rename"
        ),
        ctx, MagicMock(), response, ctx.db,
    )

    assert events == ["enqueue", "finalize"]
    assert response.headers["Location"] == f"/api/platform-jobs/{job.id}"
    assert (accepted.job_id, accepted.status, accepted.reused) == (job.id, "queued", False)
    assert enqueue.await_args.kwargs == {
        "items": [("a/1.pdf", "b/1.pdf")], "location": "docs", "scope": None, "on_conflict": "rename",
    }


async def test_single_move_enqueues_one_item(monkeypatch, ctx) -> None:
    job = SimpleNamespace(id=uuid4(), notification_id=None, status="queued")
    enqueue = AsyncMock(return_value=(job, True))
    monkeypatch.setattr("shared.sdk_files.sdk_enqueue_file_moves", enqueue)
    monkeypatch.setattr("src.services.platform_jobs.finalize_enqueued_platform_job", AsyncMock())
    accepted = await files.move_file(
        files.FileMoveRequest(source="a", destination="b", location="docs"),
        ctx, MagicMock(), Response(), ctx.db,
    )
    assert enqueue.await_args.kwargs["items"] == [("a", "b")]
    assert accepted.reused is True


async def test_service_errors_map_to_http(monkeypatch, ctx) -> None:
    monkeypatch.setattr(
        "shared.sdk_files.sdk_enqueue_file_moves",
        AsyncMock(side_effect=FileServiceError(404, "File location 'docs' not found")),
    )
    with pytest.raises(HTTPException) as caught:
        await files.move_file(
            files.FileMoveRequest(source="a", destination="b", location="docs"),
            ctx, MagicMock(), Response(), ctx.db,
        )
    assert caught.value.status_code == 404
