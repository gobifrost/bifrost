"""Seed and execute deterministic scheduler workloads in debug/test stacks."""

from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Awaitable, Callable
from uuid import NAMESPACE_URL, UUID, uuid5

from sqlalchemy import delete, select

from src.config import get_settings
from src.core.database import get_db_context
from src.core.security import decrypt_secret, encrypt_secret
from src.jobs.platform.summary_backfill import reconcile_summary_backfill_jobs
from src.jobs.platform.system_maintenance import (
    enqueue_automatic_file_index_reconciliation,
    enqueue_automatic_oauth_refresh,
    enqueue_automatic_solution_update_check,
    enqueue_automatic_webhook_renewal,
)
from src.models.enums import EventSourceType
from src.models.orm.events import EventSource, WebhookSource
from src.models.orm.file_index import FileIndex
from src.models.orm.oauth import OAuthProvider, OAuthToken
from src.models.orm.platform_jobs import PlatformJob
from src.models.orm.solutions import Solution
from src.models.orm.summary_backfill_job import SummaryBackfillJob
from src.models.orm.users import User
from src.scheduler.registry import ScheduledTaskOutcome
from src.services.repo_storage import RepoStorage
from src.services.scheduler_diagnostics import (
    finish_scheduler_run,
    start_scheduler_run,
)


FIXTURE_NAMESPACE = "https://gobifrost.com/debug/scheduler-fixtures/"
OAUTH_PROVIDER_ID = uuid5(NAMESPACE_URL, FIXTURE_NAMESPACE + "oauth-provider")
OAUTH_TOKEN_ID = uuid5(NAMESPACE_URL, FIXTURE_NAMESPACE + "oauth-token")
EVENT_SOURCE_ID = uuid5(NAMESPACE_URL, FIXTURE_NAMESPACE + "event-source")
WEBHOOK_SOURCE_ID = uuid5(NAMESPACE_URL, FIXTURE_NAMESPACE + "webhook-source")
SOLUTION_ID = uuid5(NAMESPACE_URL, FIXTURE_NAMESPACE + "solution")
SUMMARY_JOB_ID = uuid5(NAMESPACE_URL, FIXTURE_NAMESPACE + "summary-job")
FILE_PATH = "diagnostics/scheduler-fixture.txt"
FIXTURE_OWNER = "scheduler-fixture-runner"
TERMINAL_STATUSES = {"succeeded", "failed", "cancelled"}


def _fixture_base_url() -> str:
    return os.getenv(
        "BIFROST_SCHEDULER_FIXTURE_URL", "http://scheduler-fixtures:8080"
    ).rstrip("/")


def _platform_parent(job_id: UUID, job_type: str, title: str) -> PlatformJob:
    return PlatformJob(
        id=job_id,
        job_type=job_type,
        payload_version=1,
        payload={},
        priority=1000,
        requested_by_user_id="system",
        requested_by_email="system@gobifrost.local",
        requested_by_name="Bifrost Scheduler Fixture",
        resource_type="system",
        resource_id=job_type,
        title=title,
        action_url="/diagnostics",
        status="waiting",
        phase="Waiting for fixture child",
        progress_percent=50,
        max_attempts=1,
        timeout_seconds=60,
    )


async def seed_scheduler_fixtures() -> UUID:
    """Reset only deterministic fixture records and create real pending work."""
    if get_settings().environment == "production":
        raise RuntimeError("Scheduler fixtures cannot run in production")

    async with get_db_context() as db:
        user = (
            await db.execute(
                select(User).where(User.email == "dev@gobifrost.com").limit(1)
            )
        ).scalar_one_or_none()
        if user is None:
            user = (await db.execute(select(User).limit(1))).scalar_one_or_none()
        if user is None:
            raise RuntimeError("Scheduler fixtures require one seeded user")
        user_id = user.id

        # Child rows reference the fixture Solution/parent jobs, so remove those
        # first. No non-fixture record is touched.
        await db.execute(
            delete(SummaryBackfillJob).where(SummaryBackfillJob.id == SUMMARY_JOB_ID)
        )
        await db.execute(delete(PlatformJob).where(PlatformJob.id == SUMMARY_JOB_ID))
        await db.execute(delete(OAuthToken).where(OAuthToken.id == OAUTH_TOKEN_ID))
        await db.execute(
            delete(OAuthProvider).where(OAuthProvider.id == OAUTH_PROVIDER_ID)
        )
        await db.execute(delete(EventSource).where(EventSource.id == EVENT_SOURCE_ID))
        await db.execute(delete(Solution).where(Solution.id == SOLUTION_ID))
        await db.execute(delete(FileIndex).where(FileIndex.path == FILE_PATH))
        await db.commit()

        now = datetime.now(timezone.utc)
        db.add(
            OAuthProvider(
                id=OAUTH_PROVIDER_ID,
                provider_name="scheduler_fixture",
                display_name="Scheduler OAuth Fixture",
                description="Local-only provider for scheduler diagnostics",
                oauth_flow_type="authorization_code",
                client_id="scheduler-fixture-client",
                encrypted_client_secret=encrypt_secret(
                    "scheduler-fixture-secret"
                ).encode(),
                token_url=f"{_fixture_base_url()}/oauth/token",
                scopes=["fixture.read"],
                status="connected",
                created_by="scheduler-fixtures",
            )
        )
        source = EventSource(
            id=EVENT_SOURCE_ID,
            name="Scheduler Webhook Fixture",
            source_type=EventSourceType.WEBHOOK,
            is_active=True,
            created_by="scheduler-fixtures",
        )
        source.webhook_source = WebhookSource(
            id=WEBHOOK_SOURCE_ID,
            adapter_name="local_fixture",
            config={},
            external_id="local-scheduler-fixture",
            state={"renewal_count": 0},
            expires_at=now + timedelta(minutes=1),
        )
        db.add(source)

        db.add(
            Solution(
                id=SOLUTION_ID,
                slug="scheduler-update-fixture",
                name="Scheduler Update Fixture",
                version="1.0.0",
                git_connected=True,
                git_repo_url="git://scheduler-fixtures:9418/solution-update.git",
                git_ref="main",
                update_available_version=None,
            )
        )

        db.add(
            _platform_parent(
                SUMMARY_JOB_ID,
                "agent.summary_backfill",
                "Reconcile fixture summary backfill",
            )
        )
        db.add(
            SummaryBackfillJob(
                id=SUMMARY_JOB_ID,
                requested_by=user_id,
                status="complete",
                total=2,
                succeeded=2,
                failed=0,
                estimated_cost_usd=Decimal("0"),
                actual_cost_usd=Decimal("0"),
                completed_at=now,
            )
        )
        # These rows are referenced by explicit foreign keys below. Flush the
        # independent fixture graph first because the child models intentionally
        # do not declare ORM relationships to their orchestration parents.
        await db.flush()
        db.add(
            OAuthToken(
                id=OAUTH_TOKEN_ID,
                provider_id=OAUTH_PROVIDER_ID,
                encrypted_access_token=encrypt_secret("expired-access").encode(),
                encrypted_refresh_token=encrypt_secret(
                    "scheduler-fixture-refresh"
                ).encode(),
                expires_at=now - timedelta(minutes=1),
                scopes=["fixture.read"],
                status="completed",
            )
        )
        await db.commit()

    await RepoStorage().write(
        FILE_PATH,
        b"This object deliberately starts outside file_index.\n",
    )
    return user_id


async def _record_enqueue(
    task_id: str, callback: Callable[[], Awaitable[ScheduledTaskOutcome]]
) -> UUID:
    run_id = await start_scheduler_run(task_id, FIXTURE_OWNER)
    try:
        outcome = await callback()
    except Exception as exc:
        await finish_scheduler_run(
            run_id,
            status="failed",
            error_message=f"{type(exc).__name__}: {exc}",
        )
        raise
    await finish_scheduler_run(
        run_id,
        status="enqueued",
        summary=outcome.summary,
        platform_job_id=outcome.platform_job_id,
    )
    if outcome.platform_job_id is None:
        raise RuntimeError(f"{task_id} did not enqueue a durable job")
    return outcome.platform_job_id


async def _record_reconcile(
    task_id: str, callback: Callable[[], Awaitable[int]]
) -> int:
    run_id = await start_scheduler_run(task_id, FIXTURE_OWNER)
    try:
        count = await callback()
    except Exception as exc:
        await finish_scheduler_run(
            run_id,
            status="failed",
            error_message=f"{type(exc).__name__}: {exc}",
        )
        raise
    await finish_scheduler_run(
        run_id,
        status="succeeded",
        summary=f"Reconciled {count} completed fixture job(s)",
    )
    return count


async def _wait_for_platform_jobs(job_ids: list[UUID]) -> dict[UUID, PlatformJob]:
    deadline = asyncio.get_running_loop().time() + 60
    while asyncio.get_running_loop().time() < deadline:
        async with get_db_context() as db:
            jobs = (
                (
                    await db.execute(
                        select(PlatformJob).where(PlatformJob.id.in_(job_ids))
                    )
                )
                .scalars()
                .all()
            )
            by_id = {job.id: job for job in jobs}
            if len(by_id) == len(job_ids) and all(
                job.status in TERMINAL_STATUSES for job in jobs
            ):
                return by_id
        await asyncio.sleep(0.25)
    raise TimeoutError(
        "Scheduler fixture platform jobs did not finish within 60 seconds"
    )


async def run_scheduler_fixture_suite() -> dict[str, object]:
    """Run real scheduled work and verify every fixture changed as expected."""
    await seed_scheduler_fixtures()
    maintenance_jobs = {
        "oauth": await _record_enqueue(
            "oauth_token_refresh", enqueue_automatic_oauth_refresh
        ),
        "webhook": await _record_enqueue(
            "webhook_renewal", enqueue_automatic_webhook_renewal
        ),
        "solution": await _record_enqueue(
            "solution_update_check", enqueue_automatic_solution_update_check
        ),
        "file_index": await _record_enqueue(
            "file_index_reconciliation",
            enqueue_automatic_file_index_reconciliation,
        ),
    }
    jobs = await _wait_for_platform_jobs(list(maintenance_jobs.values()))
    failed = [str(job.id) for job in jobs.values() if job.status != "succeeded"]
    if failed:
        raise RuntimeError(f"Scheduler fixture jobs failed: {', '.join(failed)}")

    await _record_reconcile(
        "summary_backfill_reconciliation", reconcile_summary_backfill_jobs
    )
    async with get_db_context() as db:
        token = await db.get(OAuthToken, OAUTH_TOKEN_ID)
        webhook = await db.get(WebhookSource, WEBHOOK_SOURCE_ID)
        solution = await db.get(Solution, SOLUTION_ID)
        indexed = await db.get(FileIndex, FILE_PATH)
        summary_parent = await db.get(PlatformJob, SUMMARY_JOB_ID)
        missing_records = [
            name
            for name, record in (
                ("OAuth token", token),
                ("webhook", webhook),
                ("Solution", solution),
                ("file index", indexed),
                ("summary parent", summary_parent),
            )
            if record is None
        ]
        if missing_records:
            raise RuntimeError(
                f"Scheduler fixture records missing: {', '.join(missing_records)}"
            )
        assert token is not None
        assert webhook is not None
        assert solution is not None
        assert indexed is not None
        assert summary_parent is not None
        access_token = decrypt_secret(token.encrypted_access_token.decode())
        checks = {
            "oauth_refreshed": access_token == "scheduler-fixture-access-refreshed",
            "webhook_renewed": int(webhook.state.get("renewal_count", 0)) == 1,
            "solution_update_found": solution.update_available_version == "2.0.0",
            "file_index_repaired": indexed.content is not None
            and "deliberately starts outside" in indexed.content,
            "summary_parent_reconciled": summary_parent.status == "succeeded",
        }
    if not all(checks.values()):
        raise RuntimeError(f"Scheduler fixture verification failed: {checks}")
    return {
        "checks": checks,
        "platform_jobs": {
            name: str(job_id) for name, job_id in maintenance_jobs.items()
        },
        "diagnostics": "/diagnostics?tab=scheduler",
    }


def main() -> None:
    logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)
    print(json.dumps(asyncio.run(run_scheduler_fixture_suite()), indent=2))


if __name__ == "__main__":
    main()
