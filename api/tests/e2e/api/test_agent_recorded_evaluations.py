"""Recorded evaluation API contracts."""

from __future__ import annotations

from datetime import timezone, datetime
import time
from uuid import uuid4

import pytest

from src.models.enums import AgentAccessLevel
from sqlalchemy import delete

from src.models.orm.agent_evaluations import AgentEvaluationCase, AgentEvaluationSuite
from src.models.orm.agent_runs import AgentRun, AgentRunJournalEntry
from src.models.orm.agents import Agent
from src.models.orm.agent_recorded_evaluations import AgentRecordedEvaluation
from src.models.orm.platform_jobs import PlatformJob
from src.services.agent_runtime import types as runtime_types

pytestmark = pytest.mark.e2e


async def _seed_recorded_eval_inputs(db_session, *, org_id):
    agent = Agent(
        id=uuid4(),
        name=f"Recorded API {uuid4().hex[:8]}",
        system_prompt="Test only.",
        access_level=AgentAccessLevel.AUTHENTICATED,
        organization_id=org_id,
        created_by="test",
    )
    db_session.add(agent)
    await db_session.flush()

    suite = AgentEvaluationSuite(
        id=uuid4(),
        org_id=org_id,
        agent_id=agent.id,
        name=f"Recorded API Suite {uuid4().hex[:8]}",
        status="published",
        version=1,
    )
    case = AgentEvaluationCase(
        id=uuid4(),
        suite_id=suite.id,
        name="Terminal completed",
        enabled=True,
        accepted=True,
        version=1,
        assertions=[{"type": "terminal_status", "params": {"status": "completed"}}],
    )
    run_id = uuid4()
    run = AgentRun(
        id=run_id,
        org_id=org_id,
        agent_id=agent.id,
        trigger_type="api",
        status="completed",
        root_run_id=run_id,
        output={"ok": True},
    )
    journal = AgentRunJournalEntry(
        run_id=run_id,
        sequence=1,
        kind=runtime_types.JOURNAL_COMPLETION,
        data={"status": "completed", "completed_at": datetime.now(timezone.utc).isoformat()},
    )
    db_session.add_all([suite, case, run, journal])
    await db_session.commit()
    return agent.id, suite.id, case.id, run_id


@pytest.mark.asyncio
async def test_recorded_evaluation_create_returns_shared_job_contract(
    e2e_client, platform_admin, org1, db_session
):
    agent_id, suite_id, case_id, run_id = await _seed_recorded_eval_inputs(
        db_session, org_id=org1["id"]
    )
    job_id = None
    evaluation_id = None
    try:
        response = e2e_client.post(
            "/api/agent-evaluations/recorded-evaluations",
            headers=platform_admin.headers,
            json={
                "agent_id": str(agent_id),
                "case_ids": [str(case_id)],
                "run_ids": [str(run_id)],
                "applicability": "applicable",
            },
        )

        assert response.status_code == 202, response.text
        body = response.json()
        job_id = body["job_id"]
        evaluation_id = response.headers["X-Recorded-Evaluation-Id"]
        assert response.headers["Location"] == f"/api/platform-jobs/{job_id}"
        assert "evaluation_id" not in body
        assert evaluation_id

        terminal = None
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            status_response = e2e_client.get(
                f"/api/platform-jobs/{job_id}", headers=platform_admin.headers
            )
            assert status_response.status_code == 200, status_response.text
            terminal = status_response.json()
            if terminal["status"] in {"succeeded", "failed", "cancelled"}:
                break
            time.sleep(0.25)
        assert terminal is not None
        assert terminal["status"] == "succeeded", terminal

        results_response = e2e_client.get(
            f"/api/agent-evaluations/recorded-evaluations/{evaluation_id}/results",
            headers=platform_admin.headers,
        )
        assert results_response.status_code == 200, results_response.text
        results = results_response.json()
        assert results["aggregate"]["gate_passed"] is True
        assert results["results"][0]["outcome"] == "passed"
    finally:
        if evaluation_id is not None:
            await db_session.execute(
                delete(AgentRecordedEvaluation).where(
                    AgentRecordedEvaluation.id == evaluation_id
                )
            )
        if job_id is not None:
            await db_session.execute(delete(PlatformJob).where(PlatformJob.id == job_id))
        await db_session.execute(
            delete(AgentRunJournalEntry).where(AgentRunJournalEntry.run_id == run_id)
        )
        await db_session.execute(delete(AgentRun).where(AgentRun.id == run_id))
        await db_session.execute(
            delete(AgentEvaluationCase).where(AgentEvaluationCase.id == case_id)
        )
        await db_session.execute(
            delete(AgentEvaluationSuite).where(AgentEvaluationSuite.id == suite_id)
        )
        await db_session.execute(delete(Agent).where(Agent.id == agent_id))
        await db_session.commit()


@pytest.mark.asyncio
async def test_recorded_evaluation_rejects_out_of_selection_applicability_override(
    e2e_client, platform_admin, org1, db_session
):
    agent_id, suite_id, case_id, run_id = await _seed_recorded_eval_inputs(
        db_session, org_id=org1["id"]
    )

    response = e2e_client.post(
        "/api/agent-evaluations/recorded-evaluations",
        headers=platform_admin.headers,
        json={
            "agent_id": str(agent_id),
            "case_ids": [str(case_id)],
            "run_ids": [str(run_id)],
            "applicability_overrides": [
                {
                    "case_id": str(case_id),
                    "run_id": str(uuid4()),
                    "applicability": "applicable",
                }
            ],
        },
    )

    assert response.status_code == 422
    assert "unselected case/run" in response.text

    await db_session.execute(
        delete(AgentRunJournalEntry).where(AgentRunJournalEntry.run_id == run_id)
    )
    await db_session.execute(delete(AgentRun).where(AgentRun.id == run_id))
    await db_session.execute(
        delete(AgentEvaluationCase).where(AgentEvaluationCase.id == case_id)
    )
    await db_session.execute(
        delete(AgentEvaluationSuite).where(AgentEvaluationSuite.id == suite_id)
    )
    await db_session.execute(delete(Agent).where(Agent.id == agent_id))
    await db_session.commit()
