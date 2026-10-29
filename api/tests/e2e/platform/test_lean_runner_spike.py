"""One real job proves the spike's disposable-process boundary.

Cost: one OAuth-refresh job against the existing local fixture server. The
ordinary scheduler fixture suite covers scheduler dispatch; this case covers
the spike launcher entering the canonical runner with an existing fenced lease.
"""

import asyncio
import json
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from sqlalchemy import delete

from scripts.lean_runner_spike import launch_probe_child
from src.core.security import encrypt_secret
from src.models.orm.oauth import OAuthProvider, OAuthToken
from src.models.orm.platform_jobs import PlatformJob
from src.services.platform_jobs import update_platform_job_progress


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_disposable_child_executes_real_fenced_platform_job(async_session_factory):
    job_id, lease_token, provider_id, token_id = (uuid4() for _ in range(4))
    now = datetime.now(timezone.utc)
    try:
        async with async_session_factory() as db:
            db.add(
                OAuthProvider(
                    id=provider_id,
                    provider_name=f"lean-spike-{provider_id}",
                    client_id="scheduler-fixture-client",
                    encrypted_client_secret=encrypt_secret("scheduler-fixture-secret").encode(),
                    token_url="http://scheduler-fixtures:8080/oauth/token",
                    scopes=["fixture.read"],
                    status="connected",
                    created_by="lean-runner-spike",
                )
            )
            await db.flush()
            db.add(
                OAuthToken(
                    id=token_id,
                    provider_id=provider_id,
                    encrypted_access_token=encrypt_secret("expired-access").encode(),
                    encrypted_refresh_token=encrypt_secret("scheduler-fixture-refresh").encode(),
                    expires_at=now - timedelta(minutes=1),
                    scopes=["fixture.read"],
                    status="completed",
                )
            )
            # Commit an already claimed attempt: the background worker must not
            # race this fixture by claiming an intermediate queued row.
            db.add(
                PlatformJob(
                    id=job_id,
                    job_type="oauth.refresh",
                    payload={"trigger_type": "manual", "refresh_threshold_minutes": 20},
                    requested_by_user_id="lean-runner-spike",
                    requested_by_email="spike@example.test",
                    requested_by_name="Lean Runner Spike",
                    title="Refresh local fixture token",
                    status="running",
                    attempt=1,
                    max_attempts=1,
                    timeout_seconds=30,
                    lease_owner="lean-runner-spike",
                    lease_token=lease_token,
                    heartbeat_at=now,
                    lease_expires_at=now + timedelta(seconds=30),
                    started_at=now,
                )
            )
            await db.commit()

        assert not await update_platform_job_progress(
            job_id, uuid4(), phase="Stale attempt", current=1, total=1, percent=100
        )
        report = await asyncio.to_thread(
            launch_probe_child,
            "platform-runner",
            metadata={"job_id": str(job_id), "lease_token": str(lease_token)},
        )
        assert report["ok"], report
        assert report["returncode"] == 0
        assert report["process_group_reaped"]
        assert str(lease_token) not in json.dumps(report)

        async with async_session_factory() as db:
            job = await db.get(PlatformJob, job_id)
            token = await db.get(OAuthToken, token_id)
            assert job is not None and token is not None
            assert job.status == "succeeded", job.error_code
            assert job.progress_percent == 100
            assert job.result is not None
            assert job.result["refreshed_successfully"] == 1
            assert job.result["refresh_failed"] == 0
            assert job.lease_token is None
            assert token.last_refresh_at is not None
            assert token.expires_at is not None
            assert token.expires_at > now
    finally:
        async with async_session_factory() as db:
            await db.execute(delete(PlatformJob).where(PlatformJob.id == job_id))
            await db.execute(delete(OAuthToken).where(OAuthToken.id == token_id))
            await db.execute(delete(OAuthProvider).where(OAuthProvider.id == provider_id))
            await db.commit()
