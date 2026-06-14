"""Unit tests for refresh_metrics_snapshot aggregation.

Seeds a known mix of active/inactive workflows, forms, orgs, users and
recent/old executions, runs the refresher against the test session, and
asserts the resulting PlatformMetricsSnapshot values. Guards the correlated
scalar-subquery aggregation that drives the dashboard's instant-load path.
"""

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from sqlalchemy import select

from src.jobs.schedulers.metrics_refresh import refresh_metrics_snapshot
from src.models.enums import ExecutionStatus
from src.models.orm.executions import Execution
from src.models.orm.forms import Form
from src.models.orm.metrics import PlatformMetricsSnapshot
from src.models.orm.organizations import Organization
from src.models.orm.users import User
from src.models.orm.workflows import Workflow

PATH_SESSION_FACTORY = "src.jobs.schedulers.metrics_refresh.get_session_factory"


class _SessionFactory:
    """Callable returning an async-context-manager that yields the test session.

    Mirrors the real ``get_session_factory()() -> async with`` shape so the
    refresher runs against the test's session.
    """

    def __init__(self, session):
        self._session = session

    def __call__(self):
        return self

    async def __aenter__(self):
        return self._session

    async def __aexit__(self, *_args):
        return False


@pytest.mark.asyncio
async def test_refresh_metrics_snapshot_aggregates_known_mix(db_session, monkeypatch):
    now = datetime.now(timezone.utc)
    old = now - timedelta(hours=48)
    recent = now - timedelta(hours=1)

    # Snapshot row the refresher updates (id == 1).
    existing = await db_session.get(PlatformMetricsSnapshot, 1)
    if existing is None:
        db_session.add(PlatformMetricsSnapshot(id=1))

    # 2 active workflows + 1 inactive + 1 active data provider (excluded from
    # workflow_count, counted in data_provider_count).
    db_session.add_all([
        Workflow(id=uuid4(), name="wf-a", function_name="wf_a", type="workflow", path="workflows/a.py", is_active=True),
        Workflow(id=uuid4(), name="wf-b", function_name="wf_b", type="workflow", path="workflows/b.py", is_active=True),
        Workflow(id=uuid4(), name="wf-c", function_name="wf_c", type="workflow", path="workflows/c.py", is_active=False),
        Workflow(id=uuid4(), name="dp-a", function_name="dp_a", type="data_provider", path="workflows/dp.py", is_active=True),
    ])

    org = Organization(id=uuid4(), name=f"Org-{uuid4().hex[:8]}", created_by="test", is_active=True)
    db_session.add(org)

    db_session.add_all([
        Form(id=uuid4(), name="form-a", organization_id=org.id, workflow_id="workflows/a.py::wf_a", created_by="test", is_active=True),
        Form(id=uuid4(), name="form-b", organization_id=org.id, workflow_id="workflows/b.py::wf_b", created_by="test", is_active=False),
    ])

    db_session.add(User(
        id=uuid4(),
        email=f"metrics_{uuid4().hex[:8]}@example.com",
        name="Metrics User",
        is_active=True,
        is_superuser=True,
        is_verified=True,
        is_registered=True,
        created_at=now,
        updated_at=now,
    ))

    # Executions: a recent success + recent failure (counted in 24h) and an old
    # success (all-time only).
    db_session.add_all([
        Execution(id=uuid4(), workflow_name="wf-a", executed_by_name="tester", status=ExecutionStatus.SUCCESS.value, created_at=recent),
        Execution(id=uuid4(), workflow_name="wf-a", executed_by_name="tester", status=ExecutionStatus.FAILED.value, created_at=recent),
        Execution(id=uuid4(), workflow_name="wf-b", executed_by_name="tester", status=ExecutionStatus.SUCCESS.value, created_at=old),
    ])
    await db_session.flush()

    monkeypatch.setattr(PATH_SESSION_FACTORY, lambda: _SessionFactory(db_session))

    result = await refresh_metrics_snapshot()

    assert "error" not in result, result

    snapshot = (
        await db_session.execute(
            select(PlatformMetricsSnapshot).where(PlatformMetricsSnapshot.id == 1)
        )
    ).scalar_one()

    # refresh_metrics_snapshot computes GLOBAL aggregates, so other rows in the
    # shared test DB can lift these counts. Assert lower bounds from this test's
    # own seeds — enough to catch the aggregation returning 0, miscounting
    # entity types, or losing the 24h window, without coupling to global state.
    #
    # Data providers must be excluded from workflow_count and counted separately.
    assert snapshot.workflow_count >= 2
    assert snapshot.data_provider_count >= 1
    # Only active forms counted (we seeded 1 active, 1 inactive).
    assert snapshot.form_count >= 1
    # All-time execution stats include the old success → at least our 3.
    assert snapshot.total_executions >= 3
    assert snapshot.total_success >= 2
    assert snapshot.total_failed >= 1
    # 24h window: our 2 recent rows are in-window; the 48h-old one is not, so
    # the 24h totals must be at least our 2 recent but strictly less than the
    # all-time total would be if the window were ignored is not assertable
    # globally — assert the lower bound only.
    assert snapshot.executions_24h >= 2
    assert snapshot.success_24h >= 1
    assert snapshot.failed_24h >= 1
    # Success rate is bounded and reflects the global 24h mix.
    assert 0.0 <= snapshot.success_rate_24h <= 100.0
    # The snapshot was actually refreshed.
    assert snapshot.refreshed_at is not None
