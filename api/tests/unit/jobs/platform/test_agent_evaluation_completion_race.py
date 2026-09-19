"""Recover completion that precedes the shared job's deferred transition."""

from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy import delete

from src.jobs.platform.agent_evaluation import reconcile_agent_evaluation_jobs
from src.models.orm.agent_evaluations import (
    AgentEvaluationCase,
    AgentEvaluationExecution,
    AgentEvaluationResult,
    AgentEvaluationSuite,
)
from src.models.orm.platform_jobs import PlatformJob
from src.models.orm.agent_runs import AgentRun
from src.services import platform_jobs


@pytest.mark.asyncio
@pytest.mark.parametrize("missing_evidence", [False, True])
async def test_completed_evaluation_finishes_job_that_defers_after_completion(db_session, monkeypatch, missing_evidence):
    monkeypatch.setattr(platform_jobs, "publish_platform_job_update", AsyncMock())
    suite = AgentEvaluationSuite(name=f"early-completion-{uuid4().hex}", status="published", version=1)
    db_session.add(suite)
    await db_session.flush()
    case = AgentEvaluationCase(suite_id=suite.id, name="completed case", assertions=[])
    job = PlatformJob(
        job_type="agent.evaluation_suite", payload_version=1, payload={},
        requested_by_user_id=str(uuid4()), requested_by_email="test@example.com",
        requested_by_name="Test", title="Early completion", status="running",
    )
    db_session.add_all([case, job])
    await db_session.flush()
    run = None
    if missing_evidence:
        run = AgentRun(trigger_type="evaluation_synthetic", status="completed")
        db_session.add(run)
        await db_session.flush()
    execution = AgentEvaluationExecution(
        suite_id=suite.id, suite_version=1, platform_job_id=job.id,
        status="running" if missing_evidence else "succeeded", total_cases=1,
        completed_cases=0 if missing_evidence else 1,
        passed_cases=0 if missing_evidence else 1,
    )
    db_session.add(execution)
    await db_session.flush()
    result = AgentEvaluationResult(
        execution_id=execution.id, case_id=case.id, case_version=1,
        repetition_index=0, status="running" if missing_evidence else "passed",
        baseline_run_id=run.id if run else None, assertion_results=[],
    )
    db_session.add(result)
    await db_session.commit()
    try:
        # The terminal result arrives while the handler still owns its lease.
        assert not await platform_jobs.finish_deferred_platform_job(job.id, status="succeeded")
        # The shared runner commits its deferred transition afterward.
        job.status = "waiting"
        await db_session.commit()
        await reconcile_agent_evaluation_jobs()
        await db_session.refresh(job)
        assert job.status == ("failed" if missing_evidence else "succeeded")
        assert job.result["execution_id"] == str(execution.id)
        if missing_evidence:
            await db_session.refresh(result)
            assert result.status == "error"
            assert result.error == "Evaluation evidence could not be loaded."
            assert result.comparison["evidence_errors"]["baseline"] == "Synthetic simulation session is missing."
    finally:
        await db_session.execute(delete(AgentEvaluationExecution).where(AgentEvaluationExecution.id == execution.id))
        if run is not None:
            await db_session.delete(run)
        await db_session.execute(delete(AgentEvaluationCase).where(AgentEvaluationCase.id == case.id))
        await db_session.execute(delete(AgentEvaluationSuite).where(AgentEvaluationSuite.id == suite.id))
        await db_session.execute(delete(PlatformJob).where(PlatformJob.id == job.id))
        await db_session.commit()
