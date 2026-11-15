"""Fencing contracts for destructive audit archive commits."""

from __future__ import annotations

import threading
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import AsyncGenerator
from uuid import uuid4

import pytest

from src.services.audit_retention import archiver
from src.services.audit_retention.format import ArchiveRow, build_segments
from src.services.platform_job_lease import LeaseLost


class _NoDeleteSession:
    def add(self, _value: object) -> None:
        raise AssertionError("a stale owner must not catalog or delete a segment")


@pytest.mark.asyncio
async def test_stale_remote_owner_cannot_reach_audit_delete() -> None:
    row = ArchiveRow(
        id=uuid4(), created_at=datetime(2020, 1, 2, tzinfo=UTC), organization_id=None,
        user_id=None, impersonated_by_user_id=None, impersonation_session_id=None,
        action="audit.archive", resource_type=None, resource_id=None, outcome="success",
        source="test", operation_id=None, surface=None, execution_id=None, ip_address=None,
        user_agent=None, details=None, actor_email=None, actor_name=None,
        impersonated_by_email=None, impersonated_by_name=None, organization_name=None,
    )

    async def stale_owner(_db: object) -> bool:
        return False

    with pytest.raises(LeaseLost):
        await archiver.commit_segment(
            _NoDeleteSession(),  # type: ignore[arg-type]
            build_segments((row,))[0],
            job_id=uuid4(),
            lease_token=uuid4(),
            authorize_effect=stale_owner,
        )


@pytest.mark.asyncio
async def test_post_readback_verification_runs_off_the_event_loop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    segment = build_segments((_row(),))[0]
    stored: dict[str, bytes] = {}
    committed: list[object] = []
    verification_threads: list[int] = []
    main_thread = threading.get_ident()

    class Store:
        async def put(self, key: str, body: bytes) -> None:
            stored[key] = body

        async def get(self, key: str) -> bytes:
            return stored[key]

    @asynccontextmanager
    async def fake_db_context() -> AsyncGenerator[object, None]:
        yield object()

    async def record_commit(*args: object, **kwargs: object) -> None:
        committed.append((args, kwargs))

    def verify_on_worker_thread(*_args: object, **_kwargs: object) -> None:
        verification_threads.append(threading.get_ident())

    monkeypatch.setattr(archiver, "get_db_context", fake_db_context)
    monkeypatch.setattr(archiver, "commit_segment", record_commit)
    monkeypatch.setattr(archiver, "verify_segment", verify_on_worker_thread)

    await archiver.archive_segment(Store(), segment, job_id=uuid4(), lease_token=uuid4())

    assert verification_threads and verification_threads != [main_thread]
    assert len(committed) == 1


def _row() -> ArchiveRow:
    return ArchiveRow(
        id=uuid4(), created_at=datetime(2020, 1, 2, tzinfo=UTC), organization_id=None,
        user_id=None, impersonated_by_user_id=None, impersonation_session_id=None,
        action="audit.archive", resource_type=None, resource_id=None, outcome="success",
        source="test", operation_id=None, surface=None, execution_id=None, ip_address=None,
        user_agent=None, details=None, actor_email=None, actor_name=None,
        impersonated_by_email=None, impersonated_by_name=None, organization_name=None,
    )
