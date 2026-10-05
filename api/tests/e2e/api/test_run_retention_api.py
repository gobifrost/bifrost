"""End-to-end coverage for run retention settings, preview, the retention job and removed-run 404s."""

import time
from datetime import UTC, datetime
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.enums import EventSourceType, ExecutionStatus
from src.models.orm import Event, EventSource, Execution, WorkflowRunDaily

SETTINGS = "/api/maintenance/run-retention/settings"
RUN = "/api/maintenance/run-retention/run"
PREVIEW = "/api/maintenance/run-retention/preview"
PUBLIC = "/api/run-retention"
DEFAULTS = {"days": 30}
SUFFIX = "Finished runs are removed after 30 days."
PLAN_KEYS = {
    "cutoff",
    "workflow_runs",
    "agent_runs",
    "events",
    "oldest_workflow_run",
    "oldest_agent_run",
    "oldest_event",
}


@pytest.fixture
def restore_defaults(e2e_client, platform_admin):
    yield
    restored = e2e_client.put(SETTINGS, json=DEFAULTS, headers=platform_admin.headers)
    assert restored.status_code == 200, restored.text


@pytest_asyncio.fixture
async def finished_2001(db_session: AsyncSession):
    """One successful run and one event, both finished in 2001, under a unique name."""
    name = f"rr-api-{uuid4().hex[:12]}"
    source = EventSource(
        id=uuid4(),
        name=name,
        source_type=EventSourceType.TOPIC,
        event_type=name,
        organization_id=None,
        is_active=True,
        created_by="test@example.com",
    )
    old = datetime(2001, 1, 1, 10, 0, tzinfo=UTC)
    execution = Execution(
        id=uuid4(),
        workflow_name=name,
        workflow_id=None,
        organization_id=None,
        status=ExecutionStatus.SUCCESS,
        executed_by_name="Retention Test",
        started_at=old,
        completed_at=old,
        duration_ms=10,
    )
    db_session.add_all([source, execution])
    await db_session.flush()
    event = Event(id=uuid4(), event_source_id=source.id, data={}, created_at=old)
    db_session.add(event)
    await db_session.commit()
    ids = {"name": name, "execution": execution.id, "event": event.id, "source": source.id}
    db_session.expunge_all()
    yield ids
    await db_session.rollback()
    await db_session.execute(delete(Event).where(Event.id == ids["event"]))
    await db_session.execute(delete(EventSource).where(EventSource.id == ids["source"]))
    await db_session.execute(delete(Execution).where(Execution.id == ids["execution"]))
    await db_session.execute(delete(WorkflowRunDaily).where(WorkflowRunDaily.workflow_name == name))
    await db_session.commit()


def _start_and_wait(e2e_client, headers, *, dry_run: bool) -> dict:
    accepted = e2e_client.post(RUN, json={"dry_run": dry_run}, headers=headers)
    assert accepted.status_code == 202, accepted.text
    body = accepted.json()
    assert accepted.headers["location"] == f"/api/platform-jobs/{body['job_id']}"
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        job = e2e_client.get(f"/api/platform-jobs/{body['job_id']}", headers=headers)
        assert job.status_code == 200, job.text
        if job.json()["status"] == "succeeded":
            return job.json()
        assert job.json()["status"] not in {"failed", "cancelled"}, job.text
        time.sleep(0.25)
    raise AssertionError("Run retention job did not finish within 15 seconds")


@pytest.mark.e2e
class TestRunRetentionApi:
    def test_settings_default(self, e2e_client, platform_admin, org1_user):
        response = e2e_client.get(SETTINGS, headers=platform_admin.headers)

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["settings"] == DEFAULTS
        assert body["info"]["days"] == 30
        assert body["info"]["rolled_up_runs"] >= 0
        assert "last_run" in body
        public = e2e_client.get(PUBLIC, headers=org1_user.headers)
        assert public.status_code == 200, public.text
        assert public.json() == DEFAULTS

    def test_update_validates_and_is_audited(
        self, e2e_client, platform_admin, org1_user, restore_defaults
    ):
        too_short = e2e_client.put(SETTINGS, json={"days": 29}, headers=platform_admin.headers)
        assert too_short.status_code == 422, too_short.text

        forever = e2e_client.put(SETTINGS, json={"days": None}, headers=platform_admin.headers)
        assert forever.status_code == 200, forever.text
        assert forever.json()["settings"] == {"days": None}
        assert forever.json()["info"]["days"] is None

        public = e2e_client.get(PUBLIC, headers=org1_user.headers)
        assert public.status_code == 200, public.text
        assert public.json() == {"days": None}

        events = e2e_client.get(
            "/api/audit?action=settings.run_retention.update", headers=platform_admin.headers
        )
        assert events.status_code == 200, events.text
        newest = events.json()["entries"][0]
        assert newest["action"] == "settings.run_retention.update"
        assert newest["resource_type"] == "system_config"
        assert newest["details"]["before"] == {"days": 30}
        assert newest["details"]["after"] == {"days": None}

    def test_preview_counts(self, e2e_client, platform_admin):
        response = e2e_client.get(f"{PREVIEW}?days=30", headers=platform_admin.headers)

        assert response.status_code == 200, response.text
        preview = response.json()
        assert preview["days"] == 30
        assert preview["cutoff"] is not None
        for key in ("workflow_runs", "agent_runs", "events"):
            assert isinstance(preview[key], int) and preview[key] >= 0

        assert e2e_client.get(f"{PREVIEW}?days=29", headers=platform_admin.headers).status_code == 422
        forever = e2e_client.get(PREVIEW, headers=platform_admin.headers)
        assert forever.status_code == 200, forever.text
        assert forever.json() == {
            "days": None,
            "cutoff": None,
            "workflow_runs": 0,
            "agent_runs": 0,
            "events": 0,
        }

    def test_dry_run_job_reports_plan(self, e2e_client, platform_admin):
        job = _start_and_wait(e2e_client, platform_admin.headers, dry_run=True)

        result = job["result"]
        assert result["dry_run"] is True
        assert PLAN_KEYS <= set(result)
        status = e2e_client.get(SETTINGS, headers=platform_admin.headers).json()
        assert status["last_run"]["id"] == job["id"]

    async def test_run_deletes_expired_runs_and_rolls_them_up(
        self, e2e_client, platform_admin, finished_2001, db_session
    ):
        before = e2e_client.get(SETTINGS, headers=platform_admin.headers).json()["info"]
        assert before["oldest_finished_run"] is not None
        assert datetime.fromisoformat(before["oldest_finished_run"]) <= datetime(2001, 1, 1, 10, tzinfo=UTC)

        job = _start_and_wait(e2e_client, platform_admin.headers, dry_run=False)

        result = job["result"]
        assert result["dry_run"] is False
        assert result["workflow_runs_deleted"] >= 1
        assert result["events_deleted"] >= 1
        assert result["continues"] is False
        assert await db_session.scalar(
            select(func.count()).select_from(Execution).where(Execution.id == finished_2001["execution"])
        ) == 0
        assert await db_session.scalar(
            select(func.count()).select_from(Event).where(Event.id == finished_2001["event"])
        ) == 0
        assert await db_session.scalar(
            select(func.sum(WorkflowRunDaily.run_count)).where(
                WorkflowRunDaily.workflow_name == finished_2001["name"]
            )
        ) == 1
        status = e2e_client.get(SETTINGS, headers=platform_admin.headers).json()
        assert status["last_run"]["id"] == job["id"]
        assert status["info"]["rolled_up_runs"] >= before["rolled_up_runs"] + 1
        assert status["info"]["rolled_up_through"] >= "2001-01-01"

    def test_missing_run_404_names_the_window(self, e2e_client, platform_admin, restore_defaults):
        execution_id = uuid4()
        run_id = uuid4()

        missing = e2e_client.get(f"/api/executions/{execution_id}", headers=platform_admin.headers)
        assert missing.status_code == 404, missing.text
        assert missing.json()["detail"] == f"Execution {execution_id} not found. {SUFFIX}"
        for tail in ("result", "logs", "variables"):
            sub = e2e_client.get(f"/api/executions/{execution_id}/{tail}", headers=platform_admin.headers)
            assert sub.status_code == 404, sub.text
            assert sub.json()["detail"].endswith(SUFFIX)
        agent_run = e2e_client.get(f"/api/agent-runs/{run_id}", headers=platform_admin.headers)
        assert agent_run.status_code == 404, agent_run.text
        assert agent_run.json()["detail"] == f"Agent run {run_id} not found. {SUFFIX}"

        forever = e2e_client.put(SETTINGS, json={"days": None}, headers=platform_admin.headers)
        assert forever.status_code == 200, forever.text
        kept = e2e_client.get(f"/api/executions/{execution_id}", headers=platform_admin.headers)
        assert kept.status_code == 404, kept.text
        assert kept.json()["detail"] == f"Execution {execution_id} not found."

    def test_non_admin_is_forbidden(self, e2e_client, org1_user):
        headers = org1_user.headers
        assert e2e_client.get(SETTINGS, headers=headers).status_code == 403
        assert e2e_client.put(SETTINGS, json=DEFAULTS, headers=headers).status_code == 403
        assert e2e_client.post(RUN, json={"dry_run": True}, headers=headers).status_code == 403
        assert e2e_client.get(f"{PREVIEW}?days=30", headers=headers).status_code == 403
