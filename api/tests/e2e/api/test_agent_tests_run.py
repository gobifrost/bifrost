"""Subset simulation admission over explicit test versions (Phase 4d)."""

from __future__ import annotations

import logging
from typing import AsyncGenerator
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.orm.agent_evaluations import (
    AgentEvaluationCase,
    AgentEvaluationExecution,
    AgentEvaluationResult,
    AgentEvaluationSuite,
)
from src.models.orm.ai_models import (
    AIModelAssignment,
    AIModelProfile,
    AIProviderConnection,
)
from src.models.orm.platform_jobs import PlatformJob
from src.services.ai_model_service import AIModelService

logger = logging.getLogger(__name__)

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def run_agent(e2e_client, platform_admin, org1) -> AsyncGenerator[dict, None]:
    resp = e2e_client.post(
        "/api/agents",
        json={
            "name": f"Subset Agent {uuid4().hex[:8]}",
            "system_prompt": "Reply only with ok.",
            "channels": ["chat"],
            "access_level": "authenticated",
            "organization_id": org1["id"],
        },
        headers=platform_admin.headers,
    )
    assert resp.status_code == 201, resp.text
    agent = resp.json()
    yield agent
    try:
        e2e_client.delete(f"/api/agents/{agent['id']}", headers=platform_admin.headers)
    except Exception as e:
        logger.debug(f"fixture cleanup error: {e}")


@pytest_asyncio.fixture
async def subset_profile(db_session: AsyncSession):
    """Primary model assignment so baseline snapshots resolve (no model calls)."""
    previous = await db_session.scalar(
        select(AIModelAssignment.profile_id).where(
            AIModelAssignment.assignment_key == "primary"
        )
    )
    connection = AIProviderConnection(
        id=uuid4(),
        name=f"Subset Conn {uuid4().hex[:8]}",
        provider="openai",
        encrypted_api_key=AIModelService(db_session).encrypt_api_key("test-key"),
    )
    db_session.add(connection)
    await db_session.flush()
    profile = AIModelProfile(
        id=uuid4(),
        name=f"Subset Profile {uuid4().hex[:8]}",
        connection_id=connection.id,
        model="subset-test-model",
        enabled_for_chat=True,
    )
    db_session.add(profile)
    await db_session.flush()
    await AIModelService(db_session).set_assignment("primary", profile.id)
    await db_session.commit()
    profile_id, connection_id = profile.id, connection.id
    try:
        yield profile_id
    finally:
        await db_session.rollback()
        if previous is not None:
            assignment = await db_session.get(AIModelAssignment, "primary")
            if assignment is not None:
                assignment.profile_id = previous
            else:
                db_session.add(
                    AIModelAssignment(assignment_key="primary", profile_id=previous)
                )
        else:
            await db_session.execute(
                delete(AIModelAssignment).where(AIModelAssignment.assignment_key == "primary")
            )
        await db_session.flush()
        await db_session.execute(
            delete(AIModelProfile).where(AIModelProfile.id == profile_id)
        )
        await db_session.execute(
            delete(AIProviderConnection).where(AIProviderConnection.id == connection_id)
        )
        await db_session.commit()


async def _make_test(e2e_client, headers, agent_id: str, name: str) -> dict:
    resp = e2e_client.post(
        f"/api/agent-evaluations/agents/{agent_id}/tests",
        json={"name": name},
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _cancel_execution(e2e_client, platform_admin, execution_id: str) -> None:
    try:
        e2e_client.post(
            f"/api/agent-evaluations/executions/{execution_id}/cancel",
            headers=platform_admin.headers,
        )
    except Exception as e:
        logger.debug(f"execution cancel error: {e}")


async def _cleanup_subset(
    db_session: AsyncSession, *, agent_id: UUID, execution_ids: list[UUID]
) -> None:
    await db_session.rollback()
    for execution_id in execution_ids:
        await db_session.execute(
            delete(AgentEvaluationResult).where(
                AgentEvaluationResult.execution_id == execution_id
            )
        )
    if execution_ids:
        jobs = (
            (
                await db_session.execute(
                    select(AgentEvaluationExecution.platform_job_id).where(
                        AgentEvaluationExecution.id.in_(execution_ids)
                    )
                )
            )
            .scalars()
            .all()
        )
        await db_session.execute(
            delete(AgentEvaluationExecution).where(
                AgentEvaluationExecution.id.in_(execution_ids)
            )
        )
        job_ids = [job_id for job_id in jobs if job_id is not None]
        if job_ids:
            await db_session.execute(
                delete(PlatformJob).where(PlatformJob.id.in_(job_ids))
            )
    suite_ids = (
        (
            await db_session.execute(
                select(AgentEvaluationSuite.id).where(
                    AgentEvaluationSuite.agent_id == agent_id
                )
            )
        )
        .scalars()
        .all()
    )
    if suite_ids:
        await db_session.execute(
            delete(AgentEvaluationCase).where(
                AgentEvaluationCase.suite_id.in_(suite_ids)
            )
        )
        await db_session.execute(
            delete(AgentEvaluationSuite).where(
                AgentEvaluationSuite.id.in_(suite_ids)
            )
        )
    await db_session.commit()


def _selections(*pairs: tuple[str, int]) -> list[dict]:
    return [{"case_id": case_id, "case_version": version} for case_id, version in pairs]


async def test_subset_run_admits_freezes_and_dedupes(
    e2e_client, platform_admin, org1_user, run_agent, subset_profile, db_session: AsyncSession
):
    agent_id = run_agent["id"]
    execution_ids: list[UUID] = []
    try:
        first = await _make_test(e2e_client, org1_user.headers, agent_id, "Subset A")
        second = await _make_test(e2e_client, org1_user.headers, agent_id, "Subset B")
        body = _selections(
            (first["case_id"], 1), (second["case_id"], 1)
        )
        admitted = e2e_client.post(
            f"/api/agent-evaluations/agents/{agent_id}/tests/run",
            json={"selections": body},
            headers=org1_user.headers,
        )
        assert admitted.status_code == 202, admitted.text
        payload = admitted.json()
        assert payload["reused"] is False
        execution_id = admitted.headers.get("X-Evaluation-Execution-Id")
        assert execution_id
        execution_ids.append(UUID(execution_id))

        execution = await db_session.get(
            AgentEvaluationExecution, UUID(execution_id)
        )
        assert execution is not None
        assert len(execution.case_definitions) == 2
        assert execution.dedupe_key is not None
        assert ":subset:" in execution.dedupe_key

        # Same subset reuses; a different subset admits anew.
        again = e2e_client.post(
            f"/api/agent-evaluations/agents/{agent_id}/tests/run",
            json={"selections": body},
            headers=org1_user.headers,
        )
        assert again.status_code == 202, again.text
        assert again.json()["reused"] is True
        assert again.headers.get("X-Evaluation-Execution-Id") == execution_id

        partial = e2e_client.post(
            f"/api/agent-evaluations/agents/{agent_id}/tests/run",
            json={"selections": _selections((first["case_id"], 1))},
            headers=org1_user.headers,
        )
        assert partial.status_code == 202, partial.text
        assert partial.json()["reused"] is False
        partial_execution_id = partial.headers.get("X-Evaluation-Execution-Id")
        assert partial_execution_id != execution_id
        execution_ids.append(UUID(partial_execution_id))
    finally:
        for execution_id in execution_ids:
            await _cancel_execution(e2e_client, platform_admin, str(execution_id))
        await _cleanup_subset(
            db_session, agent_id=UUID(agent_id), execution_ids=execution_ids
        )


async def test_subset_run_rejects_bad_selections(
    e2e_client, platform_admin, org1_user, org2_user, run_agent, subset_profile, db_session: AsyncSession
):
    agent_id = run_agent["id"]
    execution_ids: list[UUID] = []
    try:
        first = await _make_test(e2e_client, org1_user.headers, agent_id, "Subset C")

        wrong_version = e2e_client.post(
            f"/api/agent-evaluations/agents/{agent_id}/tests/run",
            json={"selections": _selections((first["case_id"], 999))},
            headers=org1_user.headers,
        )
        assert wrong_version.status_code == 422, wrong_version.text

        unknown = e2e_client.post(
            f"/api/agent-evaluations/agents/{agent_id}/tests/run",
            json={"selections": _selections((str(uuid4()), 1))},
            headers=org1_user.headers,
        )
        assert unknown.status_code == 404, unknown.text

        other = e2e_client.post(
            "/api/agents",
            json={
                "name": f"Other Subset Agent {uuid4().hex[:8]}",
                "system_prompt": "Reply only with ok.",
                "channels": ["chat"],
                "access_level": "authenticated",
                "organization_id": str(org1_user.organization_id),
            },
            headers=platform_admin.headers,
        )
        assert other.status_code == 201, other.text
        other_agent = other.json()
        foreign = await _make_test(
            e2e_client, org1_user.headers, other_agent["id"], "Foreign test"
        )
        try:
            cross_agent = e2e_client.post(
                f"/api/agent-evaluations/agents/{agent_id}/tests/run",
                json={"selections": _selections((foreign["case_id"], 1))},
                headers=org1_user.headers,
            )
            # Foreign case exists but belongs to another agent (finding-link precedent).
            assert cross_agent.status_code == 422, cross_agent.text

            cross_tenant = e2e_client.post(
                f"/api/agent-evaluations/agents/{agent_id}/tests/run",
                json={"selections": _selections((first["case_id"], 1))},
                headers=org2_user.headers,
            )
            assert cross_tenant.status_code == 404, cross_tenant.text
        finally:
            await _cleanup_subset(
                db_session, agent_id=UUID(other_agent["id"]), execution_ids=[]
            )
            e2e_client.delete(
                f"/api/agents/{other_agent['id']}", headers=platform_admin.headers
            )
    finally:
        await _cleanup_subset(
            db_session, agent_id=UUID(agent_id), execution_ids=execution_ids
        )


async def test_subset_run_requires_published_single_suite(
    e2e_client, platform_admin, org1_user, run_agent, subset_profile, db_session: AsyncSession
):
    agent_id = run_agent["id"]
    try:
        suite_resp = e2e_client.post(
            "/api/agent-evaluations/suites",
            json={
                "name": f"Draft Subset Suite {uuid4().hex[:8]}",
                "agent_id": agent_id,
                "organization_id": str(org1_user.organization_id),
            },
            headers=platform_admin.headers,
        )
        assert suite_resp.status_code == 200, suite_resp.text
        case_resp = e2e_client.post(
            f"/api/agent-evaluations/suites/{suite_resp.json()['id']}/cases",
            json={"name": "draft-case"},
            headers=platform_admin.headers,
        )
        assert case_resp.status_code == 200, case_resp.text

        draft = e2e_client.post(
            f"/api/agent-evaluations/agents/{agent_id}/tests/run",
            json={
                "selections": _selections((case_resp.json()["id"], 1)),
            },
            headers=org1_user.headers,
        )
        assert draft.status_code == 409, draft.text

        published = await _make_test(
            e2e_client, org1_user.headers, agent_id, "Default suite test"
        )
        mixed = e2e_client.post(
            f"/api/agent-evaluations/agents/{agent_id}/tests/run",
            json={
                "selections": _selections(
                    (case_resp.json()["id"], 1), (published["case_id"], 1)
                ),
            },
            headers=org1_user.headers,
        )
        assert mixed.status_code == 422, mixed.text
    finally:
        await _cleanup_subset(
            db_session, agent_id=UUID(agent_id), execution_ids=[]
        )


async def test_subset_run_rejects_disabled_selections(
    e2e_client, org1_user, run_agent, subset_profile, db_session: AsyncSession
):
    agent_id = run_agent["id"]
    try:
        test = await _make_test(e2e_client, org1_user.headers, agent_id, "Disable me")
        disabled = e2e_client.patch(
            f"/api/agent-evaluations/agents/{agent_id}/tests/{test['logical_test_id']}",
            json={"enabled": False},
            headers=org1_user.headers,
        )
        assert disabled.status_code == 200, disabled.text
        resp = e2e_client.post(
            f"/api/agent-evaluations/agents/{agent_id}/tests/run",
            json={
                "selections": _selections((disabled.json()["case_id"], 2)),
            },
            headers=org1_user.headers,
        )
        assert resp.status_code == 422, resp.text
        assert "disabled" in resp.json()["detail"]
    finally:
        await _cleanup_subset(
            db_session, agent_id=UUID(agent_id), execution_ids=[]
        )


async def test_subset_reuse_ignores_quota_for_identical_request(
    e2e_client,
    platform_admin,
    org1_user,
    run_agent,
    subset_profile,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
):
    from src.services.agent_evaluations import quotas as eval_quotas

    agent_id = run_agent["id"]
    execution_ids: list[UUID] = []
    try:
        test = await _make_test(e2e_client, org1_user.headers, agent_id, "Quota test")
        body = {"selections": _selections((test["case_id"], 1))}
        first = e2e_client.post(
            f"/api/agent-evaluations/agents/{agent_id}/tests/run",
            json=body,
            headers=org1_user.headers,
        )
        assert first.status_code == 202, first.text
        execution_ids.append(UUID(first.headers["X-Evaluation-Execution-Id"]))

        monkeypatch.setattr(eval_quotas, "MAX_ACTIVE_EXECUTIONS_PER_ORG", 1)
        again = e2e_client.post(
            f"/api/agent-evaluations/agents/{agent_id}/tests/run",
            json=body,
            headers=org1_user.headers,
        )
        assert again.status_code == 202, again.text
        assert again.json()["reused"] is True
    finally:
        for execution_id in execution_ids:
            await _cancel_execution(e2e_client, platform_admin, str(execution_id))
        await _cleanup_subset(
            db_session, agent_id=UUID(agent_id), execution_ids=execution_ids
        )
