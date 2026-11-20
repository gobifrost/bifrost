"""One live physical-stage journey for the canonical audit archive job.

Cost: one ``audit.archive`` PlatformJob.  Archive format/storage semantics are
covered at lower cost by ``test_audit_archiver.py``; this case uniquely proves
the physical ``audit.archive.v1`` compressor stage returns to the API-owned
handler for verified S3 cataloging and fenced deletion.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import delete, select

from shared.worker_compute import AUDIT_ARCHIVE_CAPABILITY_NAME
from src.config import get_settings
from src.jobs.platform.audit_archive import AUDIT_ARCHIVE_DEFINITION, AuditArchivePayload
from src.models.contracts.audit_retention import AuditRetentionSettings
from src.models.orm import AuditArchiveSegment, AuditLog, PlatformJob, SystemConfig
from src.services.audit_retention.format import decode_segment, verify_checksum
from src.services.audit_retention.settings import AuditRetentionSettingsService
from src.services.audit_retention.store import AuditArchiveStore
from src.services.platform_jobs import enqueue_platform_job
from tests.e2e.conftest import poll_until
from tests.e2e.platform.thin_live_helpers import wait_for_job


pytestmark = [pytest.mark.e2e, pytest.mark.asyncio]
_TERMINAL_JOB_STATUSES = {"succeeded", "failed", "cancelled", "requires_action"}


async def _enqueue_archive(async_session_factory, platform_admin) -> UUID:
    async with async_session_factory() as db:
        job, reused = await enqueue_platform_job(
            db,
            AUDIT_ARCHIVE_DEFINITION,
            AuditArchivePayload(),
            dedupe_key=f"thin-audit-archive:{uuid4()}",
            resource_lock_key=AUDIT_ARCHIVE_DEFINITION.job_type,
            organization_id=None,
            requested_by_user_id=platform_admin.user_id,
            requested_by_email=platform_admin.email,
            requested_by_name=platform_admin.name,
            resource_type="thin-audit-archive",
            resource_id="physical-stage",
            title="Thin audit archive physical stage",
            action_url=None,
        )
        assert not reused
        await db.commit()
        return job.id


def _cancel_and_wait_for_terminal(client, headers: dict[str, str], job_id: UUID) -> None:
    """Keep the archive mutex until its canonical job has stopped."""
    current = client.get(f"/api/platform-jobs/{job_id}", headers=headers)
    assert current.status_code == 200, current.text
    if current.json()["status"] in _TERMINAL_JOB_STATUSES:
        return
    cancelled = client.post(f"/api/platform-jobs/{job_id}/cancel", headers=headers)
    assert cancelled.status_code == 200, cancelled.text

    def terminal():
        response = client.get(f"/api/platform-jobs/{job_id}", headers=headers)
        assert response.status_code == 200, response.text
        return response.json() if response.json()["status"] in _TERMINAL_JOB_STATUSES else None

    assert poll_until(terminal, max_wait=120, interval=0.1) is not None


async def test_thin_worker_compresses_audit_archive_before_api_catalogs_it(
    async_session_factory, e2e_client, platform_admin,
) -> None:
    """One job proves physical compression, then API readback/catalog/delete."""
    audit_id = uuid4()
    job_id: UUID | None = None
    segment_key: str | None = None
    previous_policy: dict | None = None
    try:
        async with async_session_factory() as db:
            policy = await db.scalar(
                select(SystemConfig).where(
                    SystemConfig.category == "audit_retention",
                    SystemConfig.key == "policy",
                    SystemConfig.organization_id.is_(None),
                )
            )
            previous_policy = deepcopy(policy.value_json) if policy is not None else None
            await AuditRetentionSettingsService(db).update_settings(
                AuditRetentionSettings(hot_days=1, archive_days=None),
                updated_by="thin-audit-archive",
            )
            db.add(
                AuditLog(
                    id=audit_id,
                    action="thin.audit.physical_archive",
                    source="service",
                    details={"fixture": "thin-audit-archive"},
                    created_at=datetime.now(UTC) - timedelta(days=2),
                )
            )
            await db.commit()

        job_id = await _enqueue_archive(async_session_factory, platform_admin)
        job = wait_for_job(e2e_client, platform_admin.headers, job_id, "audit.archive")
        assert job["result"] is not None and job["result"]["archived_rows"] == 1

        async with async_session_factory() as db:
            platform_job = await db.get(PlatformJob, job_id)
            assert platform_job is not None
            assert platform_job.executor_worker_id is not None
            assert platform_job.executor_compute_capability == AUDIT_ARCHIVE_CAPABILITY_NAME
            assert platform_job.executor_stage_id is not None
            assert platform_job.executor_input_sha256 is not None
            assert await db.get(AuditLog, audit_id) is None
            segments = (
                await db.execute(
                    select(AuditArchiveSegment).where(AuditArchiveSegment.platform_job_id == job_id)
                )
            ).scalars().all()
            assert len(segments) == 1 and segments[0].row_count == 1
            segment = segments[0]
            segment_key = segment.object_key
            catalog_sha256 = segment.sha256

        blob = await AuditArchiveStore(get_settings()).get(segment_key)
        verify_checksum(blob, catalog_sha256)
        [archived] = decode_segment(blob)
        assert archived.id == audit_id
        assert archived.action == "thin.audit.physical_archive"
    finally:
        if job_id is not None:
            _cancel_and_wait_for_terminal(e2e_client, platform_admin.headers, job_id)
        cleanup_keys = {segment_key} if segment_key is not None else set()
        if job_id is not None:
            async with async_session_factory() as db:
                cleanup_keys.update(
                    (
                        await db.execute(
                            select(AuditArchiveSegment.object_key).where(
                                AuditArchiveSegment.platform_job_id == job_id
                            )
                        )
                    ).scalars().all()
                )
        store = AuditArchiveStore(get_settings())
        for key in cleanup_keys:
            await store.delete(key)
        async with async_session_factory() as db:
            if job_id is not None:
                await db.execute(
                    delete(AuditArchiveSegment).where(AuditArchiveSegment.platform_job_id == job_id)
                )
            await db.execute(delete(AuditLog).where(AuditLog.id == audit_id))
            current = await db.scalar(
                select(SystemConfig).where(
                    SystemConfig.category == "audit_retention",
                    SystemConfig.key == "policy",
                    SystemConfig.organization_id.is_(None),
                )
            )
            if previous_policy is None:
                if current is not None:
                    await db.delete(current)
            elif current is not None:
                current.value_json = previous_policy
            if job_id is not None:
                await db.execute(delete(PlatformJob).where(PlatformJob.id == job_id))
            await db.commit()
