"""
Audit archiver against real Postgres and object storage.

Every test passes a 2002 cutoff and seeds rows dated 2001, so only the rows a
test seeded are eligible, whatever else the shared stack holds.
"""

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import get_settings
from src.models.contracts.audit_retention import AuditRetentionSettings
from src.models.orm import AuditArchiveSegment, AuditLog, Organization, PlatformJob
from src.services.audit_retention.archiver import (
    DeleteMismatch,
    LeaseLost,
    archive_segment,
    expire_segments,
    plan_archive,
    retention_info,
    select_batch,
)
from src.services.audit_retention.format import (
    ArchiveVerifyError,
    Segment,
    build_segments,
    verify_segment,
)
from src.services.audit_retention.store import (
    ArchiveStorageUnavailable,
    AuditArchiveStore,
)

CUTOFF = datetime(2002, 1, 1, tzinfo=UTC)


@dataclass
class Seeded:
    action: str
    org_id: UUID
    ids: list[UUID]
    keys: set[str] = field(default_factory=set)


@dataclass
class Lease:
    job_id: UUID
    token: UUID


@pytest_asyncio.fixture
async def store() -> AuditArchiveStore:
    return AuditArchiveStore(get_settings())


@pytest_asyncio.fixture
async def old_rows(db_session: AsyncSession, store: AuditArchiveStore):
    action = f"test.archive.{uuid4().hex}"
    org = Organization(id=uuid4(), name=f"Archive Org {uuid4().hex[:6]}", created_by="test")
    db_session.add(org)
    await db_session.flush()
    rows = [
        AuditLog(id=uuid4(), organization_id=org.id, action=action, details={"n": 1},
                 created_at=datetime(2001, 1, 1, 0, 0, 1, tzinfo=UTC)),
        AuditLog(id=uuid4(), organization_id=org.id, action=action, details={"n": 2},
                 created_at=datetime(2001, 1, 1, 12, 0, tzinfo=UTC)),
        AuditLog(id=uuid4(), organization_id=None, action=action, details={"n": 3},
                 created_at=datetime(2001, 1, 2, 8, 0, tzinfo=UTC)),
    ]
    db_session.add_all(rows)
    await db_session.commit()
    seeded = Seeded(action=action, org_id=org.id, ids=[r.id for r in rows])
    # Tests delete these rows from other sessions; keep no stale objects here.
    db_session.expunge_all()

    yield seeded

    await db_session.rollback()
    await db_session.execute(delete(AuditLog).where(AuditLog.action == action))
    if seeded.keys:
        await db_session.execute(
            delete(AuditArchiveSegment).where(AuditArchiveSegment.object_key.in_(seeded.keys))
        )
    await db_session.execute(delete(Organization).where(Organization.id == seeded.org_id))
    await db_session.commit()
    for key in seeded.keys:
        await store.delete(key)


@pytest_asyncio.fixture
async def lease(db_session: AsyncSession):
    now = datetime.now(UTC)
    job = PlatformJob(
        id=uuid4(),
        job_type="audit.archive",
        payload_version=1,
        payload={"dry_run": False},
        priority=100,
        requested_by_user_id=str(uuid4()),
        requested_by_email="archiver@example.com",
        requested_by_name="Archiver Test",
        title="Archive audit events",
        status="running",
        phase="Archiving",
        progress_percent=0,
        max_attempts=3,
        timeout_seconds=3600,
        execution_backend="local",
        memory_required_bytes=256 * 1024 * 1024,
        retry_on_runner_loss=True,
        attempt=1,
        lease_owner="test-archiver",
        lease_token=uuid4(),
        heartbeat_at=now,
        # Far enough out that scheduler lease recovery never reclaims it mid-test.
        lease_expires_at=now + timedelta(hours=1),
    )
    db_session.add(job)
    await db_session.commit()
    held = Lease(job_id=job.id, token=job.lease_token)
    db_session.expunge_all()

    yield held

    await db_session.rollback()
    await db_session.execute(delete(PlatformJob).where(PlatformJob.id == held.job_id))
    await db_session.commit()


async def _segments(db: AsyncSession, seeded: Seeded) -> list[Segment]:
    rows = await select_batch(db, cutoff=CUTOFF)
    await db.commit()
    assert {r.action for r in rows} == {seeded.action}, "rows outside this test were eligible"
    segments = build_segments(rows)
    seeded.keys.update(s.key for s in segments)
    return segments


async def _remaining(db: AsyncSession, seeded: Seeded) -> set[UUID]:
    result = await db.execute(select(AuditLog.id).where(AuditLog.action == seeded.action))
    ids = set(result.scalars().all())
    await db.commit()
    return ids


async def _assert_stored(store: AuditArchiveStore, segment: Segment) -> None:
    verify_segment(await store.get(segment.key), sha256=segment.sha256, ids=segment.ids)


async def _catalog(db: AsyncSession, seeded: Seeded) -> list[AuditArchiveSegment]:
    result = await db.execute(
        select(AuditArchiveSegment).where(AuditArchiveSegment.object_key.in_(seeded.keys))
    )
    segments = list(result.scalars().all())
    await db.commit()
    return segments


@pytest.mark.e2e
@pytest.mark.asyncio
class TestAuditArchiver:
    async def test_archive_moves_rows_to_verified_segments(self, db_session, store, old_rows, lease):
        segments = await _segments(db_session, old_rows)
        assert len(segments) == 2

        for segment in segments:
            await archive_segment(store, segment, job_id=lease.job_id, lease_token=lease.token)

        assert await _remaining(db_session, old_rows) == set()
        catalog = await _catalog(db_session, old_rows)
        assert {(c.organization_id, c.day.isoformat()) for c in catalog} == {
            (old_rows.org_id, "2001-01-01"),
            (None, "2001-01-02"),
        }
        assert sum(c.row_count for c in catalog) == 3
        assert {c.platform_job_id for c in catalog} == {lease.job_id}
        for segment in segments:
            await _assert_stored(store, segment)

        info = await retention_info(db_session, AuditRetentionSettings(hot_days=30, archive_days=None))
        await db_session.commit()
        assert (info["hot_days"], info["archive_days"]) == (30, None)
        assert info["archived_segments"] == 2
        assert info["archived_rows"] == 3
        assert info["archived_through"] == datetime(2001, 1, 2, 8, 0, tzinfo=UTC)

    async def test_rerun_after_upload_before_commit_is_idempotent(self, db_session, store, old_rows, lease):
        first = (await _segments(db_session, old_rows))[0]
        await store.put(first.key, first.body)

        segments = await _segments(db_session, old_rows)
        assert segments[0].key == first.key
        for segment in segments:
            await archive_segment(store, segment, job_id=lease.job_id, lease_token=lease.token)

        assert await _remaining(db_session, old_rows) == set()
        catalog = await _catalog(db_session, old_rows)
        assert sorted(c.object_key for c in catalog) == sorted(s.key for s in segments)
        assert sum(c.row_count for c in catalog) == 3
        await _assert_stored(store, first)

    async def test_corrupt_object_deletes_nothing(self, db_session, store, old_rows, lease, monkeypatch):
        segment = (await _segments(db_session, old_rows))[0]
        real_get = store.get

        async def corrupt_get(key: str) -> bytes:
            blob = bytearray(await real_get(key))
            blob[len(blob) // 2] ^= 0xFF
            return bytes(blob)

        monkeypatch.setattr(store, "get", corrupt_get)

        with pytest.raises(ArchiveVerifyError):
            await archive_segment(store, segment, job_id=lease.job_id, lease_token=lease.token)
        monkeypatch.undo()

        assert await _remaining(db_session, old_rows) == set(old_rows.ids)
        assert await _catalog(db_session, old_rows) == []
        # The uncataloged upload stays behind; the fixture removes it.
        await _assert_stored(store, segment)

    async def test_stale_lease_cannot_delete(self, db_session, store, old_rows, lease):
        segment = (await _segments(db_session, old_rows))[0]
        await db_session.execute(
            update(PlatformJob).where(PlatformJob.id == lease.job_id).values(lease_token=uuid4())
        )
        await db_session.commit()

        with pytest.raises(LeaseLost):
            await archive_segment(store, segment, job_id=lease.job_id, lease_token=lease.token)

        assert await _remaining(db_session, old_rows) == set(old_rows.ids)
        assert await _catalog(db_session, old_rows) == []
        await _assert_stored(store, segment)

    async def test_delete_count_mismatch_rolls_back(self, db_session, store, old_rows, lease):
        segment = next(s for s in await _segments(db_session, old_rows) if len(s.rows) == 2)
        gone = segment.ids[0]
        await db_session.execute(delete(AuditLog).where(AuditLog.id == gone))
        await db_session.commit()

        with pytest.raises(DeleteMismatch):
            await archive_segment(store, segment, job_id=lease.job_id, lease_token=lease.token)

        assert await _remaining(db_session, old_rows) == set(old_rows.ids) - {gone}
        assert await _catalog(db_session, old_rows) == []
        await _assert_stored(store, segment)

    async def test_expiry_removes_only_old_segments(self, db_session, store, old_rows, lease):
        segments = await _segments(db_session, old_rows)
        for segment in segments:
            await archive_segment(store, segment, job_id=lease.job_id, lease_token=lease.token)
        old = next(s for s in segments if s.day.isoformat() == "2001-01-01")
        kept = next(s for s in segments if s.day.isoformat() == "2001-01-02")

        expired = await expire_segments(
            store, expiry=datetime(2001, 1, 2, tzinfo=UTC), job_id=lease.job_id, lease_token=lease.token
        )

        assert expired == (1, 2)
        assert [c.object_key for c in await _catalog(db_session, old_rows)] == [kept.key]
        with pytest.raises(FileNotFoundError):
            await store.get(old.key)
        await _assert_stored(store, kept)

    async def test_stale_lease_cannot_expire(self, db_session, store, old_rows, lease):
        segments = await _segments(db_session, old_rows)
        for segment in segments:
            await archive_segment(store, segment, job_id=lease.job_id, lease_token=lease.token)

        with pytest.raises(LeaseLost):
            await expire_segments(store, expiry=CUTOFF, job_id=lease.job_id, lease_token=uuid4())

        assert sorted(c.object_key for c in await _catalog(db_session, old_rows)) == sorted(
            s.key for s in segments
        )
        for segment in segments:
            await _assert_stored(store, segment)

    async def test_plan_counts_without_writing(self, db_session, old_rows):
        plan = await plan_archive(db_session, cutoff=CUTOFF, expiry=None)
        await db_session.commit()

        assert plan["eligible_rows"] == 3
        assert [(d["day"], d["rows"]) for d in plan["days"]] == [("2001-01-01", 2), ("2001-01-02", 1)]
        assert plan["estimated_bytes"] > 0
        assert plan["expiring_segments"] == 0
        assert await _remaining(db_session, old_rows) == set(old_rows.ids)
        count = await db_session.scalar(
            select(func.count()).select_from(AuditArchiveSegment).where(
                AuditArchiveSegment.organization_id == old_rows.org_id
            )
        )
        assert count == 0

    async def test_store_requires_object_storage(self):
        with pytest.raises(ArchiveStorageUnavailable):
            AuditArchiveStore(get_settings().model_copy(update={"s3_bucket": None}))
