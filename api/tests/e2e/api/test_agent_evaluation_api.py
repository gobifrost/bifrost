"""Studio REST API coverage: suites, cases, candidates, drafts, executions.

Exercises the tenant-authorized CRUD/version surface, optimistic version
checks, published immutability, the ``202 PlatformJobAccepted`` + shared
``Location`` execution contract with active dedupe, pagination, and
role/tenant boundaries through HTTP (no mocks: the API container serves
the bind-mounted router).
"""

from __future__ import annotations

from uuid import UUID, uuid4

import asyncio

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import delete, or_, select

from src.models.orm.agents import Agent
from src.models.orm.agent_evaluations import (
    AgentCandidateSnapshot,
    AgentEvaluationExecution,
    AgentEvaluationSuite,
)
from src.models.orm.agent_runs import AgentRun
from src.models.orm.ai_models import (
    AIModelAssignment,
    AIModelProfile,
    AIProviderConnection,
)
from src.models.orm.platform_jobs import PlatformJob
from src.services.ai_model_service import AIModelService

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def studio_llm_profile(db_session, e2e_client, platform_admin):
    """Own and drain Studio's deterministic model configuration."""
    previous_testing_profile_id = await db_session.scalar(
        select(AIModelAssignment.profile_id).where(
            AIModelAssignment.assignment_key == "testing"
        )
    )
    connection = AIProviderConnection(
        name=f"Studio evaluation {uuid4().hex}",
        provider="openai_compatible",
        endpoint="http://scheduler-fixtures:8080/v1",
        encrypted_api_key=AIModelService(db_session).encrypt_api_key("fixture-key"),
    )
    db_session.add(connection)
    await db_session.flush()
    profile = AIModelProfile(
        name=f"Studio evaluation {uuid4().hex}",
        connection_id=connection.id,
        model="fixture-agent",
        enabled_for_chat=False,
    )
    db_session.add(profile)
    await db_session.flush()
    profile_id = profile.id
    connection_id = connection.id
    await AIModelService(db_session).set_assignment("testing", profile_id)
    await db_session.commit()
    try:
        yield profile_id
    finally:
        await _drain_and_remove_studio_resources(
            profile_id, connection_id, e2e_client, platform_admin.headers,
        )
        if previous_testing_profile_id is not None:
            db_session.add(AIModelAssignment(
                assignment_key="testing", profile_id=previous_testing_profile_id,
            ))
            await db_session.commit()


@pytest_asyncio.fixture
async def studio_tenant_suites(db_session):
    """Delete only the explicitly created tenant-boundary suite rows."""
    suite_ids = set()
    try:
        yield suite_ids
    finally:
        if suite_ids:
            await db_session.execute(
                delete(AgentEvaluationSuite).where(
                    AgentEvaluationSuite.id.in_(suite_ids)
                )
            )
            await db_session.commit()


async def _owned_studio_resources(profile_id):
    """Return only rows provably owned by this fixture's unique profile."""
    from src.core.database import get_db_context

    async with get_db_context() as db:
        agent_ids = set(
            await db.scalars(
                select(Agent.id).where(Agent.llm_profile_id == profile_id)
            )
        )
        suite_ids = set()
        execution_ids = set()
        job_ids = set()
        if agent_ids:
            suite_ids = set(
                await db.scalars(
                    select(AgentEvaluationSuite.id).where(
                        AgentEvaluationSuite.agent_id.in_(agent_ids)
                    )
                )
            )
        if suite_ids:
            executions = (
                await db.execute(
                    select(
                        AgentEvaluationExecution.id,
                        AgentEvaluationExecution.platform_job_id,
                    ).where(AgentEvaluationExecution.suite_id.in_(suite_ids))
                )
            ).all()
            execution_ids = {row.id for row in executions}
            job_ids = {row.platform_job_id for row in executions if row.platform_job_id}

        run_conditions = []
        if agent_ids:
            run_conditions.append(AgentRun.agent_id.in_(agent_ids))
        if suite_ids:
            run_conditions.append(
                AgentRun.correlation["designer_suite_id"].as_string().in_(
                    [str(suite_id) for suite_id in suite_ids]
                )
            )
        root_run_ids = set()
        if run_conditions:
            root_run_ids = set(
                await db.scalars(select(AgentRun.id).where(or_(*run_conditions)))
            )
        run_ids = set()
        if root_run_ids:
            run_ids = set(
                await db.scalars(
                    select(AgentRun.id).where(
                        or_(
                            AgentRun.id.in_(root_run_ids),
                            AgentRun.root_run_id.in_(root_run_ids),
                        )
                    )
                )
            )
        return {
            "agent_ids": agent_ids,
            "suite_ids": suite_ids,
            "execution_ids": execution_ids,
            "job_ids": job_ids,
            "root_run_ids": root_run_ids,
            "run_ids": run_ids,
        }


async def _drain_and_remove_studio_resources(profile_id, connection_id, client, headers) -> None:
    """Cancel owned work, wait for terminal runs, then remove exact fixture rows."""
    from src.core.database import get_db_context
    from src.services.agent_runtime import types as runtime_types

    owned = await _owned_studio_resources(profile_id)
    for execution_id in owned["execution_ids"]:
        response = client.post(f"/api/agent-evaluations/executions/{execution_id}/cancel", headers=headers)
        assert response.status_code == 200, response.text
    for job_id in owned["job_ids"]:
        response = client.post(f"/api/platform-jobs/{job_id}/cancel", headers=headers)
        assert response.status_code == 200, response.text

    owned = await _owned_studio_resources(profile_id)
    for run_id in owned["root_run_ids"]:
        # Keep loop-bound production clients in the API process. Calling the
        # runtime signal client in a function-scoped pytest loop poisons the
        # cached client for later in-process workflow tests.
        response = client.post(f"/api/agent-runs/{run_id}/cancel", headers=headers)
        if response.status_code == 400:
            async with get_db_context() as db:
                assert await db.scalar(select(AgentRun.status).where(AgentRun.id == run_id)) in runtime_types.TERMINAL_STATUSES
        else:
            assert response.status_code == 200, response.text

    for _ in range(50):
        owned = await _owned_studio_resources(profile_id)
        async with get_db_context() as db:
            active_runs = set(
                await db.scalars(
                    select(AgentRun.id).where(
                        AgentRun.id.in_(owned["run_ids"]),
                        AgentRun.status.not_in(runtime_types.TERMINAL_STATUSES),
                    )
                )
            ) if owned["run_ids"] else set()
        if not active_runs:
            break
        await asyncio.sleep(0.1)
    else:
        raise AssertionError(f"Studio fixture left active runs: {sorted(active_runs)}")

    owned = await _owned_studio_resources(profile_id)
    async with get_db_context() as db:
        if owned["job_ids"]:
            await db.execute(delete(PlatformJob).where(PlatformJob.id.in_(owned["job_ids"])))
        if owned["run_ids"]:
            await db.execute(delete(AgentRun).where(AgentRun.id.in_(owned["run_ids"])))
        if owned["execution_ids"]:
            await db.execute(
                delete(AgentEvaluationExecution).where(
                    AgentEvaluationExecution.id.in_(owned["execution_ids"])
                )
            )
        if owned["suite_ids"]:
            await db.execute(
                delete(AgentEvaluationSuite).where(
                    AgentEvaluationSuite.id.in_(owned["suite_ids"])
                )
            )
        if owned["agent_ids"]:
            await db.execute(
                delete(AgentCandidateSnapshot).where(
                    AgentCandidateSnapshot.base_agent_id.in_(owned["agent_ids"])
                )
            )
            await db.execute(delete(Agent).where(Agent.id.in_(owned["agent_ids"])))
        await db.execute(
            delete(AIModelAssignment).where(AIModelAssignment.profile_id == profile_id)
        )
        await db.execute(delete(AIModelProfile).where(AIModelProfile.id == profile_id))
        await db.execute(
            delete(AIProviderConnection).where(AIProviderConnection.id == connection_id)
        )


def _agent_payload(name: str, profile_id=None) -> dict:
    return {
        "name": name,
        "description": "Studio API test agent",
        "system_prompt": "Be helpful and brief.",
        "channels": ["chat"],
        "access_level": "authenticated",
        "organization_id": None,
        "llm_profile_id": str(profile_id) if profile_id is not None else None,
        "max_run_timeout": 5,
    }


def _case_payload(name: str = "greeting") -> dict:
    return {
        "name": name,
        "input": {"task": "say hello"},
        "fixture": {
            "entities": {},
            "allowed_tools": ["get_ticket"],
            "rules": [],
        },
        "simulator_policy": {},
        "assertions": [{"type": "no_real_tools", "params": {}}],
        "expected_tools": [],
        "forbidden_tools": [],
        "repetitions": 1,
        "provenance": "manual",
        "tags": ["smoke"],
    }


async def test_studio_suite_case_candidate_flow(
    e2e_client, platform_admin, studio_llm_profile
):
    headers = platform_admin.headers
    agent = e2e_client.post(
        "/api/agents",
        json=_agent_payload(f"Studio Agent {uuid4().hex[:8]}", studio_llm_profile),
        headers=headers,
    )
    assert agent.status_code == 201, agent.text
    agent_id = agent.json()["id"]

    suite = e2e_client.post(
        "/api/agent-evaluations/suites",
        json={"name": f"studio-{uuid4().hex[:8]}", "agent_id": agent_id},
        headers=headers,
    )
    assert suite.status_code == 200, suite.text
    suite_id = suite.json()["id"]

    listed = e2e_client.get("/api/agent-evaluations/suites", headers=headers)
    assert listed.status_code == 200
    assert suite_id in {s["id"] for s in listed.json()}

    case = e2e_client.post(
        f"/api/agent-evaluations/suites/{suite_id}/cases",
        json=_case_payload(),
        headers=headers,
    )
    assert case.status_code == 200, case.text
    assert case.json()["version"] == 1

    bad_case = e2e_client.post(
        f"/api/agent-evaluations/suites/{suite_id}/cases",
        json={**_case_payload("bad"), "assertions": [{"type": "vibes"}]},
        headers=headers,
    )
    assert bad_case.status_code == 422

    frozen_case = e2e_client.put(
        f"/api/agent-evaluations/suites/{suite_id}/cases/{case.json()['id']}",
        json={"enabled": False, "expected_version": 1},
        headers=headers,
    )
    assert frozen_case.status_code == 409

    candidate = e2e_client.post(
        "/api/agent-evaluations/candidates",
        json={
            "base_agent_id": agent_id,
            "name": "brief",
            "overlays": {"system_prompt": "Be extremely brief."},
        },
        headers=headers,
    )
    assert candidate.status_code == 200, candidate.text
    candidate_body = candidate.json()
    assert candidate_body["evaluation_only"] is True
    assert candidate_body["snapshot"]["system_prompt"] == "Be extremely brief."
    candidate_id = candidate_body["id"]

    fetched = e2e_client.get(
        f"/api/agent-evaluations/candidates/{candidate_id}", headers=headers
    )
    assert fetched.status_code == 200

    published = e2e_client.post(
        f"/api/agent-evaluations/suites/{suite_id}/publish", headers=headers
    )
    assert published.status_code == 200
    assert published.json()["status"] == "published"

    frozen = e2e_client.put(
        f"/api/agent-evaluations/suites/{suite_id}",
        json={"description": "late edit"},
        headers=headers,
    )
    assert frozen.status_code == 409


async def test_designer_drafts_queue_server_authorized_generation(
    e2e_client, platform_admin, studio_llm_profile
):
    headers = platform_admin.headers
    agent = e2e_client.post(
        "/api/agents",
        json=_agent_payload(f"Designer Agent {uuid4().hex[:8]}", studio_llm_profile),
        headers=headers,
    )
    assert agent.status_code == 201, agent.text
    suite = e2e_client.post(
        "/api/agent-evaluations/suites",
        json={"name": f"designer-{uuid4().hex[:8]}", "agent_id": agent.json()["id"]},
        headers=headers,
    )
    assert suite.status_code == 200, suite.text
    suite_id = suite.json()["id"]

    drafts = e2e_client.post(
        f"/api/agent-evaluations/suites/{suite_id}/designer/drafts",
        json={"suite_goal": "exercise output-only behavior", "requested_count": 1},
        headers=headers,
    )
    assert drafts.status_code == 200, drafts.text
    assert drafts.json()["status"] == "queued"
    assert drafts.json()["run_id"]

    malformed = e2e_client.post(
        f"/api/agent-evaluations/suites/{suite_id}/designer/drafts",
        json={"suite_goal": "x", "requested_count": 11},
        headers=headers,
    )
    assert malformed.status_code == 422


async def test_admin_semantic_judge_is_frozen_on_case_save(
    e2e_client, platform_admin, studio_llm_profile
):
    headers = platform_admin.headers
    agent = e2e_client.post(
        "/api/agents",
        json=_agent_payload(f"Judge Agent {uuid4().hex[:8]}", studio_llm_profile),
        headers=headers,
    )
    assert agent.status_code == 201, agent.text
    suite = e2e_client.post(
        "/api/agent-evaluations/suites",
        json={"name": f"judge-{uuid4().hex[:8]}", "agent_id": agent.json()["id"]},
        headers=headers,
    )
    assert suite.status_code == 200, suite.text
    case = e2e_client.post(
        f"/api/agent-evaluations/suites/{suite.json()['id']}/cases",
        json={
            **_case_payload("semantic"),
            "assertions": [
                {
                    "type": "llm_judge",
                    "params": {
                        "rubric": "The answer should be helpful.",
                        "prompt_version": "1",
                        "threshold": 0.8,
                        "judge_profile_id": str(studio_llm_profile),
                    },
                }
            ],
        },
        headers=headers,
    )
    assert case.status_code == 200, case.text
    params = case.json()["assertions"][0]["params"]
    assert "judge_profile_id" not in params
    assert params["judge_snapshot"] == {
        "profile_id": str(studio_llm_profile),
        "provider": "openai",
        "model": "fixture-agent",
        "endpoint": "http://scheduler-fixtures:8080/v1",
        "openai_transport": "chat_completions",
        "anthropic_prompt_cache_supported": None,
        "default_max_tokens": None,
        "extra_params": {},
        "prompt_version": "1",
    }


async def test_execution_returns_202_with_location_and_dedupe(
    e2e_client, platform_admin, studio_llm_profile
):
    headers = platform_admin.headers
    agent = e2e_client.post(
        "/api/agents",
        json=_agent_payload(f"Exec Agent {uuid4().hex[:8]}", studio_llm_profile),
        headers=headers,
    )
    assert agent.status_code == 201, agent.text
    suite = e2e_client.post(
        "/api/agent-evaluations/suites",
        json={
            "name": f"exec-{uuid4().hex[:8]}",
            "agent_id": agent.json()["id"],
        },
        headers=headers,
    )
    suite_id = suite.json()["id"]
    case = e2e_client.post(
        f"/api/agent-evaluations/suites/{suite_id}/cases",
        json=_case_payload(),
        headers=headers,
    )
    assert case.status_code == 200, case.text
    published = e2e_client.post(
        f"/api/agent-evaluations/suites/{suite_id}/publish", headers=headers
    )
    assert published.status_code == 200

    first = e2e_client.post(
        "/api/agent-evaluations/executions",
        json={"suite_id": suite_id},
        headers=headers,
    )
    assert first.status_code == 202, first.text
    assert first.headers.get("Location", "").startswith("/api/platform-jobs/")
    first_body = first.json()
    assert first_body["reused"] is False

    second = e2e_client.post(
        "/api/agent-evaluations/executions",
        json={"suite_id": suite_id},
        headers=headers,
    )
    assert second.status_code == 202, second.text
    assert second.json()["reused"] is True
    assert second.json()["job_id"] == first_body["job_id"]

    job_status = e2e_client.get(first.headers["Location"], headers=headers)
    assert job_status.status_code == 200

    cancelled = e2e_client.post(
        f"/api/agent-evaluations/executions/{second.json()['job_id']}/cancel",
        headers=headers,
    )
    # Cancelling with a job ID (not an execution ID) must 404, not cancel.
    assert cancelled.status_code == 404


async def test_concurrent_execution_requests_share_one_active_job(
    e2e_api_url, e2e_client, platform_admin, studio_llm_profile
):
    """Exercise the suite row lock + partial dedupe index with two sessions."""
    headers = platform_admin.headers
    agent = e2e_client.post(
        "/api/agents",
        json=_agent_payload(f"Race Agent {uuid4().hex[:8]}", studio_llm_profile),
        headers=headers,
    )
    assert agent.status_code == 201, agent.text
    suite = e2e_client.post(
        "/api/agent-evaluations/suites",
        json={"name": f"race-{uuid4().hex[:8]}", "agent_id": agent.json()["id"]},
        headers=headers,
    )
    assert suite.status_code == 200, suite.text
    suite_id = suite.json()["id"]
    case = e2e_client.post(
        f"/api/agent-evaluations/suites/{suite_id}/cases",
        json=_case_payload(),
        headers=headers,
    )
    assert case.status_code == 200, case.text
    assert e2e_client.post(
        f"/api/agent-evaluations/suites/{suite_id}/publish", headers=headers
    ).status_code == 200

    async with httpx.AsyncClient(base_url=e2e_api_url, timeout=60.0) as first_client, \
        httpx.AsyncClient(base_url=e2e_api_url, timeout=60.0) as second_client:
        first, second = await asyncio.gather(
            first_client.post(
                "/api/agent-evaluations/executions",
                json={"suite_id": suite_id}, headers=headers,
            ),
            second_client.post(
                "/api/agent-evaluations/executions",
                json={"suite_id": suite_id}, headers=headers,
            ),
        )
    assert {first.status_code, second.status_code} == {202}
    bodies = [first.json(), second.json()]
    assert sum(not body["reused"] for body in bodies) == 1
    assert bodies[0]["job_id"] == bodies[1]["job_id"]


async def test_execution_results_paginate_and_link_debugger(
    e2e_client, platform_admin, studio_llm_profile
):
    headers = platform_admin.headers
    agent = e2e_client.post(
        "/api/agents",
        json=_agent_payload(f"Links Agent {uuid4().hex[:8]}", studio_llm_profile),
        headers=headers,
    )
    suite = e2e_client.post(
        "/api/agent-evaluations/suites",
        json={
            "name": f"links-{uuid4().hex[:8]}",
            "agent_id": agent.json()["id"],
        },
        headers=headers,
    )
    suite_id = suite.json()["id"]
    e2e_client.post(
        f"/api/agent-evaluations/suites/{suite_id}/cases",
        json=_case_payload(),
        headers=headers,
    )
    e2e_client.post(
        f"/api/agent-evaluations/suites/{suite_id}/publish", headers=headers
    )
    run = e2e_client.post(
        "/api/agent-evaluations/executions",
        json={"suite_id": suite_id},
        headers=headers,
    )
    assert run.status_code == 202, run.text
    execution_id = run.headers.get("X-Evaluation-Execution-Id")
    assert execution_id

    execution = e2e_client.get(
        f"/api/agent-evaluations/executions/{execution_id}", headers=headers
    )
    assert execution.status_code == 200
    assert execution.json()["suite_id"] == suite_id

    results = e2e_client.get(
        f"/api/agent-evaluations/executions/{execution_id}/results",
        headers=headers,
    )
    assert results.status_code == 200
    rows = results.json()
    assert len(rows) == 1
    assert rows[0]["status"] in ("pending", "running", "passed", "failed", "error")
    # Debugger navigation travels on run IDs, never duplicated journals.
    assert "baseline_run_id" in rows[0]
    assert "candidate_run_id" in rows[0]
    assert "assertion_results" in rows[0]

    paged = e2e_client.get(
        f"/api/agent-evaluations/executions/{execution_id}/results?limit=1&offset=0",
        headers=headers,
    )
    assert paged.status_code == 200
    assert len(paged.json()) == 1

    cancelled = e2e_client.post(
        f"/api/agent-evaluations/executions/{execution_id}/cancel",
        headers=headers,
    )
    assert cancelled.status_code == 200, cancelled.text
    assert cancelled.json()["status"] == "cancelled"


async def test_tenant_boundaries(
    e2e_client, platform_admin, org2_user, alice_user, studio_tenant_suites
):
    admin_headers = platform_admin.headers
    suite = e2e_client.post(
        "/api/agent-evaluations/suites",
        json={
            "name": f"tenant-{uuid4().hex[:8]}",
            "organization_id": str(alice_user.organization_id),
        },
        headers=admin_headers,
    )
    assert suite.status_code == 200, suite.text
    suite_id = suite.json()["id"]
    studio_tenant_suites.add(UUID(suite_id))

    assert (
        e2e_client.get(
            f"/api/agent-evaluations/suites/{suite_id}",
            headers=alice_user.headers,
        ).status_code
        == 200
    )
    assert (
        e2e_client.get(
            f"/api/agent-evaluations/suites/{suite_id}", headers=org2_user.headers
        ).status_code
        == 404
    )
    draft = e2e_client.post(
        f"/api/agent-evaluations/suites/{suite_id}/cases",
        json=_case_payload(),
        headers=org2_user.headers,
    )
    assert draft.status_code == 404

    global_suite = e2e_client.post(
        "/api/agent-evaluations/suites",
        json={"name": f"global-studio-{uuid4().hex[:8]}"},
        headers=admin_headers,
    )
    assert global_suite.status_code == 200, global_suite.text
    studio_tenant_suites.add(UUID(global_suite.json()["id"]))
    assert (
        e2e_client.put(
            f"/api/agent-evaluations/suites/{global_suite.json()['id']}",
            json={"description": "cross-tenant mutation"},
            headers=alice_user.headers,
        ).status_code
        == 404
    )
