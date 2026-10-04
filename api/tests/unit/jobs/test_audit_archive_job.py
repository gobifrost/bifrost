"""Business semantics of the audit.archive platform-job handler.

Storage, database and archiver calls are replaced with fakes that keep the
real signatures; the archiver itself is covered against Postgres and object
storage in tests/e2e/platform/test_audit_archiver.py.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any, AsyncGenerator
from uuid import UUID, uuid4

import pytest

from src.config import Settings, get_settings
from src.jobs.platform import audit_archive
from src.jobs.platform.audit_archive import (
    AUDIT_ARCHIVE_DEFINITION,
    AuditArchivePayload,
    run_audit_archive,
)
from src.jobs.platform.base import PlatformJobFailure
from src.models.contracts.audit_retention import AuditRetentionSettings
from src.services.audit_retention.format import ArchiveRow, ArchiveVerifyError, Segment

FROZEN_CUTOFF = datetime(2020, 3, 1, tzinfo=UTC)


class FakeContext:
    def __init__(self, checkpoint: dict[str, Any] | None = None) -> None:
        self.job_id = uuid4()
        self.lease_token = uuid4()
        self.checkpoint = checkpoint
        self.checkpoints: list[tuple[dict[str, Any], str]] = []

    async def report(
        self,
        phase: str,
        current: int = 0,
        total: int | None = None,
        percent: float | None = None,
    ) -> None:
        return None

    async def log(self, level: str, code: str, message: str) -> None:
        return None

    async def save_checkpoint(self, result: dict[str, Any], *, phase: str) -> None:
        self.checkpoints.append((dict(result), phase))


class FakeStore:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings


class FakeSettingsService:
    def __init__(self, session: object) -> None:
        self.session = session

    async def get_settings(self) -> AuditRetentionSettings:
        return AuditRetentionSettings(hot_days=90, archive_days=None)


class Calls:
    def __init__(self, batches: list[list[ArchiveRow]]) -> None:
        self.batches = batches
        self.cutoffs: list[datetime] = []
        self.archived: list[Segment] = []
        self.planned: list[tuple[datetime, datetime | None]] = []

    async def select_batch(
        self,
        db: object,
        *,
        cutoff: datetime,
        limit: int = 5000,
        max_bytes: int = 64 * 1024 * 1024,
    ) -> list[ArchiveRow]:
        self.cutoffs.append(cutoff)
        return self.batches.pop(0) if self.batches else []

    async def archive_segment(
        self,
        store: object,
        segment: Segment,
        *,
        job_id: UUID,
        lease_token: UUID,
    ) -> None:
        self.archived.append(segment)

    async def plan_archive(
        self,
        db: object,
        *,
        cutoff: datetime,
        expiry: datetime | None,
    ) -> dict[str, Any]:
        self.planned.append((cutoff, expiry))
        return {"cutoff": cutoff.isoformat(), "eligible_rows": 1}


def _row(created_at: datetime) -> ArchiveRow:
    return ArchiveRow(
        id=uuid4(),
        created_at=created_at,
        organization_id=None,
        user_id=None,
        action="test.archive.unit",
        resource_type=None,
        resource_id=None,
        outcome="success",
        source="test",
        operation_id=None,
        surface=None,
        execution_id=None,
        ip_address=None,
        user_agent=None,
        details=None,
        actor_email=None,
        actor_name=None,
        organization_name=None,
    )


@pytest.fixture
def calls(monkeypatch: pytest.MonkeyPatch) -> Calls:
    recorded = Calls([[_row(datetime(2019, 6, 1, tzinfo=UTC))]])

    @asynccontextmanager
    async def fake_db_context() -> AsyncGenerator[object, None]:
        yield object()

    monkeypatch.setattr(audit_archive, "get_db_context", fake_db_context)
    monkeypatch.setattr(audit_archive, "AuditArchiveStore", FakeStore)
    monkeypatch.setattr(audit_archive, "AuditRetentionSettingsService", FakeSettingsService)
    monkeypatch.setattr(audit_archive, "select_batch", recorded.select_batch)
    monkeypatch.setattr(audit_archive, "archive_segment", recorded.archive_segment)
    monkeypatch.setattr(audit_archive, "plan_archive", recorded.plan_archive)
    return recorded


def test_definition_policy() -> None:
    policy = AUDIT_ARCHIVE_DEFINITION.policy

    assert AUDIT_ARCHIVE_DEFINITION.job_type == "audit.archive"
    assert AUDIT_ARCHIVE_DEFINITION.payload_model is AuditArchivePayload
    assert policy.timeout_seconds == 2 * 60 * 60
    assert policy.max_attempts == 3
    assert policy.max_concurrency == 1
    assert policy.retry_on_runner_loss is True
    assert policy.retry_on_failure is False
    assert policy.allow_running_cancellation is True


@pytest.mark.asyncio
async def test_resume_uses_frozen_cutoff(calls: Calls, monkeypatch: pytest.MonkeyPatch) -> None:
    class SettingsMustNotBeRead(FakeSettingsService):
        async def get_settings(self) -> AuditRetentionSettings:
            raise AssertionError("a resumed run must keep its frozen windows")

    monkeypatch.setattr(audit_archive, "AuditRetentionSettingsService", SettingsMustNotBeRead)
    context = FakeContext(
        checkpoint={
            "dry_run": False,
            "cutoff": FROZEN_CUTOFF.isoformat(),
            "expiry": None,
            "archived_rows": 7,
            "archived_segments": 2,
            "expired_segments": 0,
            "expired_rows": 0,
        }
    )

    result = await run_audit_archive(context, AuditArchivePayload())

    assert calls.cutoffs and set(calls.cutoffs) == {FROZEN_CUTOFF}
    assert result["cutoff"] == FROZEN_CUTOFF.isoformat()
    assert (result["archived_rows"], result["archived_segments"]) == (8, 3)
    assert "pending" not in result


@pytest.mark.asyncio
async def test_dry_run_writes_nothing(calls: Calls) -> None:
    context = FakeContext()

    result = await run_audit_archive(context, AuditArchivePayload(dry_run=True))

    assert calls.archived == []
    assert calls.cutoffs == []
    assert context.checkpoints == []
    assert len(calls.planned) == 1
    assert calls.planned[0][1] is None
    assert result["dry_run"] is True
    assert result["eligible_rows"] == 1


@pytest.mark.asyncio
async def test_missing_storage_fails_without_deleting(
    calls: Calls, monkeypatch: pytest.MonkeyPatch
) -> None:
    from src.services.audit_retention.store import AuditArchiveStore

    monkeypatch.setattr(audit_archive, "AuditArchiveStore", AuditArchiveStore)
    monkeypatch.setattr(
        audit_archive,
        "get_settings",
        lambda: get_settings().model_copy(update={"s3_bucket": None}),
    )

    with pytest.raises(PlatformJobFailure) as failure:
        await run_audit_archive(FakeContext(), AuditArchivePayload())

    assert failure.value.code == "archive_storage_unavailable"
    assert calls.cutoffs == []
    assert calls.archived == []


@pytest.mark.asyncio
async def test_verify_error_becomes_operator_failure(
    calls: Calls, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def corrupt(
        store: object,
        segment: Segment,
        *,
        job_id: UUID,
        lease_token: UUID,
    ) -> None:
        raise ArchiveVerifyError("sha256 mismatch")

    monkeypatch.setattr(audit_archive, "archive_segment", corrupt)

    with pytest.raises(PlatformJobFailure) as failure:
        await run_audit_archive(FakeContext(), AuditArchivePayload())

    assert failure.value.code == "archive_verify_failed"
    assert "Nothing was deleted" in failure.value.message
