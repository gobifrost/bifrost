"""Atomic canonical checkpoint/entity commits; no worker jobs are launched."""
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from sqlalchemy import delete, select

from src.jobs.platform.base import PlatformJobContext, PlatformJobCancelled
from src.models.orm.config import SystemConfig
from src.models.orm.platform_jobs import PlatformJob

pytestmark = [pytest.mark.e2e, pytest.mark.asyncio]


@pytest.mark.parametrize("commit", [False, True])
async def test_checkpoint_and_entity_changes_share_one_transaction(async_session_factory, commit):
    job_id, token, user_id = uuid4(), uuid4(), uuid4()
    key = str(job_id)
    context = PlatformJobContext(job_id, token, None, str(user_id), "fixture@example.test", "Fixture")
    async with async_session_factory() as db:
        db.add(PlatformJob(id=job_id, job_type="workspace.bundle_import", payload={}, status="running",
            attempt=1, lease_token=token, lease_expires_at=datetime.now(timezone.utc)+timedelta(minutes=5),
            title="Fixture", requested_by_user_id=str(user_id), requested_by_email="fixture@example.test",
            requested_by_name="Fixture"))
        await db.commit()
    try:
        async with async_session_factory() as db:
            db.add(SystemConfig(category="checkpoint-fixture", key=key, value_json={"applied": True}))
            await context.checkpoint_in_transaction(db, {"db_applied": True}, phase="Entity commit")
            if commit:
                await db.commit()
            else:
                await db.rollback()
        async with async_session_factory() as db:
            row = await db.get(PlatformJob, job_id)
            config = await db.scalar(select(SystemConfig).where(SystemConfig.key == key))
            assert (row.result == {"db_applied": True}) == commit
            assert (config is not None) == commit
            row.status = "cancel_requested"
            await db.commit()
        async with async_session_factory() as db:
            with pytest.raises(PlatformJobCancelled):
                await context.checkpoint_in_transaction(db, {"must_not_publish": True}, phase="Refused")
            await db.rollback()
        async with async_session_factory() as db:
            row = await db.get(PlatformJob, job_id)
            assert row.result == ({"db_applied": True} if commit else None)
    finally:
        async with async_session_factory() as db:
            await db.execute(delete(SystemConfig).where(SystemConfig.category == "checkpoint-fixture", SystemConfig.key == key))
            await db.execute(delete(PlatformJob).where(PlatformJob.id == job_id))
            await db.commit()


@pytest.mark.parametrize("current_owner", [False, True])
@pytest.mark.parametrize("mutation", ["progress", "checkpoint", "finish", "retry", "defer"])
async def test_attempt_mutations_require_current_remote_owner(async_session_factory, current_owner, mutation):
    from types import SimpleNamespace
    from src.models.orm.execution_workers import ExecutionWorker
    from src.services import platform_jobs as service
    from src.worker.remote.fencing import LeaseRef, remote_owner_context
    from src.worker.remote.ownership import RemoteOwnerLease

    job_id, worker_id, owner_id, launcher_id, token = (uuid4() for _ in range(5))
    now = datetime.now(timezone.utc)
    async with async_session_factory() as db:
        db.add(ExecutionWorker(id=worker_id, form="static", state="ready", supported_handlers=[],
            remote_owner_id=owner_id, remote_launcher_id=launcher_id, remote_session_generation=2,
            remote_owner_lease_expires_at=now+timedelta(minutes=5), heartbeat_at=now))
        # A direct service-row test: no executor may claim this synthetic type.
        db.add(PlatformJob(id=job_id, job_type="fixture.owner.fence", payload={}, status="running",
            phase="Original", attempt=1, max_attempts=2, lease_token=token,
            lease_expires_at=now+timedelta(minutes=5), title="Fixture",
            requested_by_user_id=str(uuid4()), requested_by_email="fixture@example.test", requested_by_name="Fixture"))
        await db.commit()
    try:
        ref = LeaseRef(RemoteOwnerLease(worker_id, owner_id, launcher_id, 2 if current_owner else 1))
        with remote_owner_context(ref):
            if mutation == "progress":
                changed = await service.update_platform_job_progress(job_id, token, phase="Updated", current=1, total=2, percent=50)
            elif mutation == "checkpoint":
                changed = await service.checkpoint_platform_job(job_id, token, result={"ok": True}, phase="Updated")
            elif mutation == "finish":
                changed = await service.finish_platform_job(job_id, token, status="succeeded", result={"ok": True})
            elif mutation == "retry":
                changed = await service.retry_platform_job_failure(job_id, token,
                    SimpleNamespace(retryable=True, code="fixture", message="Fixture", result={"ok": True}), enabled=True)
            else:
                changed = await service.defer_platform_job(job_id, token, phase="Updated", result={"ok": True})
        assert changed is current_owner
        async with async_session_factory() as db:
            row = await db.get(PlatformJob, job_id)
            if not current_owner:
                assert row.status == "running" and row.phase == "Original" and row.revision == 1
                assert row.result is None and row.lease_token == token
            else:
                assert row.revision == 2
                assert row.status == {"finish": "succeeded", "retry": "queued", "defer": "waiting"}.get(mutation, "running")
    finally:
        async with async_session_factory() as db:
            await db.execute(delete(PlatformJob).where(PlatformJob.id == job_id))
            await db.execute(delete(ExecutionWorker).where(ExecutionWorker.id == worker_id))
            await db.commit()
