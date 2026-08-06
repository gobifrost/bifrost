"""POST /api/agent-runs/backfill-summaries — e2e tests.

Covers the T103/T104 bulk backfill endpoint and orchestration job rows:
  - admin-only auth
  - dry_run returns eligible count + cost estimate without enqueuing
  - real run creates a SummaryBackfillJob row, resets runs to 'pending'
  - GET /backfill-jobs/{id} returns progress
  - GET /backfill-jobs?active=true surfaces running jobs
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from decimal import Decimal
from typing import AsyncGenerator
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.orm.agent_runs import AgentRun
from src.models.orm.platform_jobs import PlatformJob
from src.models.orm.summary_backfill_job import SummaryBackfillJob


logger = logging.getLogger(__name__)

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def backfill_agent(e2e_client, platform_admin) -> AsyncGenerator[dict, None]:
    resp = e2e_client.post(
        "/api/agents",
        json={
            "name": f"Backfill Test Agent {uuid4().hex[:8]}",
            "description": "backfill test",
            "system_prompt": "test",
            "channels": [],
            "access_level": "authenticated",
        },
        headers=platform_admin.headers,
    )
    assert resp.status_code == 201, resp.text
    agent = resp.json()
    yield agent
    try:
        e2e_client.delete(
            f"/api/agents/{agent['id']}", headers=platform_admin.headers
        )
    except Exception as e:
        # Best-effort fixture cleanup; teardown shouldn't fail the test
        logger.debug(f"fixture cleanup error: {e}")


@pytest_asyncio.fixture
async def mixed_runs(
    backfill_agent, db_session: AsyncSession
) -> AsyncGenerator[list[AgentRun], None]:
    """5 runs: 2 pending, 2 failed, 1 completed (the last should be skipped)."""
    now = datetime.now(timezone.utc)
    runs = []
    seed = [
        ("pending", None),
        ("pending", None),
        ("failed", "prior failure"),
        ("failed", "prior failure"),
        ("completed", None),
    ]
    for idx, (summary_status, summary_error) in enumerate(seed):
        r = AgentRun(
            id=uuid4(),
            agent_id=UUID(backfill_agent["id"]),
            trigger_type="api",
            status="completed",
            iterations_used=1,
            tokens_used=100,
            completed_at=now,
            summary_status=summary_status,
            summary_error=summary_error,
            asked="asked" if summary_status == "completed" else None,
            did="did" if summary_status == "completed" else None,
            created_at=now,
        )
        _ = idx
        db_session.add(r)
        runs.append(r)
    await db_session.commit()
    yield runs
    for r in runs:
        await db_session.execute(delete(AgentRun).where(AgentRun.id == r.id))
    await db_session.execute(
        delete(SummaryBackfillJob).where(
            SummaryBackfillJob.agent_id == UUID(backfill_agent["id"])
        )
    )
    await db_session.commit()


class TestBackfillSummaries:
    async def test_dry_run_returns_eligible_without_enqueuing(
        self,
        e2e_client,
        platform_admin,
        backfill_agent,
        mixed_runs,
        db_session: AsyncSession,
    ):
        res = e2e_client.post(
            "/api/agent-runs/backfill-summaries",
            json={
                "agent_id": backfill_agent["id"],
                "statuses": ["pending", "failed"],
                "limit": 500,
                "dry_run": True,
            },
            headers=platform_admin.headers,
        )
        assert res.status_code == 200, res.text
        body = res.json()
        assert body["eligible"] == 4  # 2 pending + 2 failed, not the 1 completed
        assert body["queued"] == 0
        assert body["job_id"] is None
        assert body["cost_basis"] in ("history", "fallback")
        # Dry run must not have created a job row.
        jobs = (
            await db_session.execute(
                select(SummaryBackfillJob).where(
                    SummaryBackfillJob.agent_id
                    == UUID(backfill_agent["id"])
                )
            )
        ).scalars().all()
        assert len(jobs) == 0

    async def test_real_run_creates_job_and_resets_runs_for_processing(
        self,
        e2e_client,
        platform_admin,
        backfill_agent,
        mixed_runs,
        db_session: AsyncSession,
    ):
        # Queue-routing assertion lives in the unit test
        # ``test_backfill_publishes_to_backfill_queue`` — the e2e client runs
        # the API in a separate process, so a host-side patch on
        # ``publish_message`` never fires. Here we verify the DB side-effects.
        res = e2e_client.post(
            "/api/agent-runs/backfill-summaries",
            json={
                "agent_id": backfill_agent["id"],
                "statuses": ["pending", "failed"],
                "limit": 500,
                "dry_run": False,
            },
            headers=platform_admin.headers,
        )
        assert res.status_code == 200, res.text
        body = res.json()
        assert body["eligible"] == 4
        assert body["queued"] == 4
        assert body["job_id"] is not None

        # Job row should exist with total=4.
        job = (
            await db_session.execute(
                select(SummaryBackfillJob).where(
                    SummaryBackfillJob.id == UUID(body["job_id"])
                )
            )
        ).scalar_one()
        assert job.total == 4
        assert job.status == "running"
        assert job.agent_id == UUID(backfill_agent["id"])
        assert job.estimated_cost_usd >= Decimal("0")

        platform_job = await db_session.get(PlatformJob, UUID(body["job_id"]))
        assert platform_job is not None
        assert platform_job.job_type == "agent.summary_backfill"
        assert platform_job.status in {"queued", "running", "waiting", "succeeded"}
        assert platform_job.payload == {"protected": True}
        assert platform_job.encrypted_payload is not None
        assert backfill_agent["id"] not in platform_job.encrypted_payload

        # The live worker may advance a queued run before this separate test
        # process observes it. The durable proof of the reset is that the
        # previous failure marker has been cleared; exact in-flight status is
        # intentionally not asserted.
        for r in mixed_runs:
            await db_session.refresh(r)
        targeted = [
            r for r in mixed_runs if r.summary_status != "completed"
        ]
        assert all(r.summary_error != "prior failure" for r in targeted)
        # The completed run should remain untouched.
        untouched = next(r for r in mixed_runs if r.asked == "asked")
        assert untouched.summary_status == "completed"

    async def test_non_admin_cannot_trigger(
        self, e2e_client, org1_user, backfill_agent
    ):
        res = e2e_client.post(
            "/api/agent-runs/backfill-summaries",
            json={"agent_id": backfill_agent["id"], "dry_run": True},
            headers=org1_user.headers,
        )
        assert res.status_code == 403, res.text

    async def test_dry_run_zero_eligible_returns_cleanly(
        self,
        e2e_client,
        platform_admin,
        backfill_agent,
    ):
        """When no matching runs exist, dry_run returns eligible=0 with no error."""
        res = e2e_client.post(
            "/api/agent-runs/backfill-summaries",
            json={
                "agent_id": backfill_agent["id"],
                "statuses": ["pending", "failed"],
                "limit": 500,
                "dry_run": True,
            },
            headers=platform_admin.headers,
        )
        assert res.status_code == 200, res.text
        body = res.json()
        assert body["eligible"] == 0
        assert body["queued"] == 0


class TestBackfillJobEndpoints:
    async def test_get_job_by_id(
        self,
        e2e_client,
        platform_admin,
        backfill_agent,
        mixed_runs,
        db_session: AsyncSession,
    ):
        # Kick off a backfill to get a job row.
        res = e2e_client.post(
            "/api/agent-runs/backfill-summaries",
            json={
                "agent_id": backfill_agent["id"],
                "statuses": ["pending", "failed"],
                "limit": 500,
                "dry_run": False,
            },
            headers=platform_admin.headers,
        )
        job_id = res.json()["job_id"]

        res = e2e_client.get(
            f"/api/agent-runs/backfill-jobs/{job_id}",
            headers=platform_admin.headers,
        )
        assert res.status_code == 200, res.text
        body = res.json()
        assert body["id"] == job_id
        assert body["total"] == 4
        assert body["agent_id"] == backfill_agent["id"]

    async def test_list_active_jobs(
        self,
        e2e_client,
        platform_admin,
        backfill_agent,
        mixed_runs,
    ):
        e2e_client.post(
            "/api/agent-runs/backfill-summaries",
            json={
                "agent_id": backfill_agent["id"],
                "statuses": ["pending", "failed"],
                "limit": 500,
                "dry_run": False,
            },
            headers=platform_admin.headers,
        )
        res = e2e_client.get(
            "/api/agent-runs/backfill-jobs?active=true",
            headers=platform_admin.headers,
        )
        assert res.status_code == 200, res.text
        body = res.json()
        assert any(
            j["agent_id"] == backfill_agent["id"] and j["status"] == "running"
            for j in body["items"]
        )

    async def test_non_admin_cannot_view_jobs(
        self, e2e_client, org1_user
    ):
        res = e2e_client.get(
            "/api/agent-runs/backfill-jobs",
            headers=org1_user.headers,
        )
        assert res.status_code == 403, res.text


class TestBackfillEligible:
    async def test_returns_zero_when_nothing_eligible(
        self, e2e_client, platform_admin, backfill_agent
    ):
        """UI uses this to hide the Backfill button — zero eligible → zero cost."""
        res = e2e_client.get(
            f"/api/agent-runs/backfill-eligible?agent_id={backfill_agent['id']}",
            headers=platform_admin.headers,
        )
        assert res.status_code == 200, res.text
        body = res.json()
        assert body["eligible"] == 0
        assert float(body["estimated_cost_usd"]) == 0.0

    async def test_returns_pending_plus_failed_counts(
        self, e2e_client, platform_admin, backfill_agent, mixed_runs
    ):
        """mixed_runs seeds 2 pending + 2 failed + 1 completed — expect 4."""
        res = e2e_client.get(
            f"/api/agent-runs/backfill-eligible?agent_id={backfill_agent['id']}",
            headers=platform_admin.headers,
        )
        assert res.status_code == 200, res.text
        body = res.json()
        assert body["eligible"] == 4
        _ = mixed_runs  # fixture activates seed

    async def test_non_admin_cannot_preview(
        self, e2e_client, org1_user, backfill_agent
    ):
        res = e2e_client.get(
            f"/api/agent-runs/backfill-eligible?agent_id={backfill_agent['id']}",
            headers=org1_user.headers,
        )
        assert res.status_code == 403, res.text

    async def test_include_completed_counts_every_completed_summary(
        self,
        e2e_client,
        platform_admin,
        backfill_agent,
        mixed_runs,
        db_session: AsyncSession,
    ):
        """include_completed=true sweeps all completed-summary runs in
        addition to pending/failed, regardless of prompt version. Drives the
        "All completed runs" scope on the Resummarize dialog."""
        # mixed_runs seeds 2 pending + 2 failed + 1 completed = 5 total.
        res = e2e_client.get(
            f"/api/agent-runs/backfill-eligible?agent_id={backfill_agent['id']}"
            f"&include_completed=true",
            headers=platform_admin.headers,
        )
        assert res.status_code == 200, res.text
        assert res.json()["eligible"] == 5
        _ = mixed_runs
        _ = db_session


class TestBackfillPromptVersion:
    """Roll-forward flow: re-summarize runs tagged with an older prompt version."""

    @pytest_asyncio.fixture
    async def versioned_runs(
        self, backfill_agent, db_session: AsyncSession
    ) -> AsyncGenerator[list[AgentRun], None]:
        """4 completed-summary runs across prompt versions: v1, v1, v2, NULL."""
        now = datetime.now(timezone.utc)
        agent_uuid = UUID(backfill_agent["id"])
        seed: list[tuple[str | None, str]] = [
            ("v1", "old1"),
            ("v1", "old2"),
            ("v2", "current"),
            (None, "legacy-unversioned"),
        ]
        runs: list[AgentRun] = []
        for version, tag in seed:
            r = AgentRun(
                id=uuid4(),
                agent_id=agent_uuid,
                trigger_type="api",
                status="completed",
                iterations_used=1,
                tokens_used=100,
                completed_at=now,
                summary_status="completed",
                summary_prompt_version=version,
                asked=tag,
                did=tag,
                created_at=now,
            )
            db_session.add(r)
            runs.append(r)
        await db_session.commit()
        yield runs
        for r in runs:
            await db_session.execute(delete(AgentRun).where(AgentRun.id == r.id))
        await db_session.execute(
            delete(SummaryBackfillJob).where(
                SummaryBackfillJob.agent_id == agent_uuid
            )
        )
        await db_session.commit()

    async def test_eligible_below_v2_matches_v1_and_null(
        self,
        e2e_client,
        platform_admin,
        backfill_agent,
        versioned_runs,
    ):
        """GET /backfill-eligible with prompt_version_below=v2 counts v1 + NULL,
        not the already-current v2 run."""
        res = e2e_client.get(
            f"/api/agent-runs/backfill-eligible?agent_id={backfill_agent['id']}"
            f"&prompt_version_below=v2",
            headers=platform_admin.headers,
        )
        assert res.status_code == 200, res.text
        assert res.json()["eligible"] == 3  # 2x v1 + 1x NULL
        _ = versioned_runs

    async def test_post_below_v2_reenqueues_old_versions(
        self,
        e2e_client,
        platform_admin,
        backfill_agent,
        versioned_runs,
        db_session: AsyncSession,
    ):
        """POST /backfill-summaries with prompt_version_below=v2 + statuses=[completed]
        flips old-version runs out of completed and skips the v2 run."""
        res = e2e_client.post(
            "/api/agent-runs/backfill-summaries",
            json={
                "agent_id": backfill_agent["id"],
                "statuses": ["completed"],
                "prompt_version_below": "v2",
                "limit": 500,
                "dry_run": False,
            },
            headers=platform_admin.headers,
        )
        assert res.status_code == 200, res.text
        body = res.json()
        assert body["eligible"] == 3
        assert body["queued"] == 3

        for r in versioned_runs:
            await db_session.refresh(r)
        # Targeted runs are moved out of the 'completed' summary_status (they
        # go to 'pending' on POST; the backfill worker may advance them to
        # 'failed' before we observe — either is fine, we just need proof
        # they were taken off the completed list for re-processing).
        targeted = [r for r in versioned_runs if r.asked != "current"]
        assert all(r.summary_status != "completed" for r in targeted)
        # The v2 run (already current) must be untouched.
        current = next(r for r in versioned_runs if r.asked == "current")
        assert current.summary_status == "completed"
        assert current.summary_prompt_version == "v2"

    async def test_without_filter_ignores_completed_runs(
        self,
        e2e_client,
        platform_admin,
        backfill_agent,
        versioned_runs,
    ):
        """Default statuses=[pending,failed] does NOT sweep completed runs even
        if they have an old version — the version filter is opt-in."""
        res = e2e_client.post(
            "/api/agent-runs/backfill-summaries",
            json={
                "agent_id": backfill_agent["id"],
                "limit": 500,
                "dry_run": True,
            },
            headers=platform_admin.headers,
        )
        assert res.status_code == 200, res.text
        assert res.json()["eligible"] == 0
        _ = versioned_runs


class TestBackfillJobCancel:
    async def test_cancel_running_job_flips_status(
        self,
        e2e_client,
        platform_admin,
        backfill_agent,
        mixed_runs,
        db_session: AsyncSession,
    ):
        """Admin can cancel a running job; status flips to 'cancelled' and
        completed_at is set so it stops appearing in ?active=true queries."""
        res = e2e_client.post(
            "/api/agent-runs/backfill-summaries",
            json={
                "agent_id": backfill_agent["id"],
                "statuses": ["pending", "failed"],
                "limit": 500,
                "dry_run": False,
            },
            headers=platform_admin.headers,
        )
        job_id = res.json()["job_id"]

        res = e2e_client.post(
            f"/api/agent-runs/backfill-jobs/{job_id}/cancel",
            headers=platform_admin.headers,
        )
        assert res.status_code == 200, res.text
        body = res.json()
        assert body["status"] == "cancelled"
        assert body["completed_at"] is not None

        # Re-fetch via db_session to confirm it's not in "active" queries.
        active_res = e2e_client.get(
            "/api/agent-runs/backfill-jobs?active=true",
            headers=platform_admin.headers,
        )
        active_ids = [j["id"] for j in active_res.json()["items"]]
        assert job_id not in active_ids

    async def test_cancel_is_idempotent_on_terminal_jobs(
        self,
        e2e_client,
        platform_admin,
        backfill_agent,
        mixed_runs,
    ):
        """Cancelling an already-cancelled job returns 200 without re-broadcasting."""
        res = e2e_client.post(
            "/api/agent-runs/backfill-summaries",
            json={"agent_id": backfill_agent["id"], "dry_run": False},
            headers=platform_admin.headers,
        )
        job_id = res.json()["job_id"]

        # First cancel — flips running → cancelled.
        r1 = e2e_client.post(
            f"/api/agent-runs/backfill-jobs/{job_id}/cancel",
            headers=platform_admin.headers,
        )
        assert r1.status_code == 200
        # Second cancel — no-op, still 200, still cancelled.
        r2 = e2e_client.post(
            f"/api/agent-runs/backfill-jobs/{job_id}/cancel",
            headers=platform_admin.headers,
        )
        assert r2.status_code == 200
        assert r2.json()["status"] == "cancelled"

    async def test_non_admin_cannot_cancel(
        self,
        e2e_client,
        platform_admin,
        org1_user,
        backfill_agent,
        mixed_runs,
    ):
        res = e2e_client.post(
            "/api/agent-runs/backfill-summaries",
            json={"agent_id": backfill_agent["id"], "dry_run": False},
            headers=platform_admin.headers,
        )
        job_id = res.json()["job_id"]
        res = e2e_client.post(
            f"/api/agent-runs/backfill-jobs/{job_id}/cancel",
            headers=org1_user.headers,
        )
        assert res.status_code == 403, res.text

    async def test_cancel_unknown_job_returns_404(
        self, e2e_client, platform_admin
    ):
        res = e2e_client.post(
            f"/api/agent-runs/backfill-jobs/{uuid4()}/cancel",
            headers=platform_admin.headers,
        )
        assert res.status_code == 404, res.text
