"""Generate tests from authorized findings (Phase 4f)."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import AsyncGenerator
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from shared.agent_test_collection import get_or_create_default_suite
from src.models.orm.agent_evaluations import (
    AgentEvaluationCase,
    AgentEvaluationSuite,
)
from src.models.orm.agent_findings import AgentFinding
from src.models.orm.agent_runs import AgentRun
from src.models.orm.ai_models import (
    AIModelAssignment,
    AIModelProfile,
    AIProviderConnection,
)
from src.services.ai_model_service import AIModelService
from src.services.agent_evaluations.test_designer import materialize_designer_drafts

logger = logging.getLogger(__name__)

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def gen_agent(e2e_client, platform_admin, org1) -> AsyncGenerator[dict, None]:
    resp = e2e_client.post(
        "/api/agents",
        json={
            "name": f"Generate Agent {uuid4().hex[:8]}",
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
async def designer_profile(db_session: AsyncSession):
    previous = {}
    for key in ("testing", "primary"):
        previous[key] = await db_session.scalar(
            select(AIModelAssignment.profile_id).where(
                AIModelAssignment.assignment_key == key
            )
        )
    connection = AIProviderConnection(
        id=uuid4(),
        name=f"Generate Conn {uuid4().hex[:8]}",
        provider="openai_compatible",
        endpoint="http://scheduler-fixtures:8080/v1",
        encrypted_api_key=AIModelService(db_session).encrypt_api_key("fixture-key"),
    )
    db_session.add(connection)
    await db_session.flush()
    profile = AIModelProfile(
        id=uuid4(),
        name=f"Generate Profile {uuid4().hex[:8]}",
        connection_id=connection.id,
        model="fixture-agent",
        enabled_for_chat=False,
    )
    db_session.add(profile)
    await db_session.flush()
    await AIModelService(db_session).set_assignment("testing", profile.id)
    await AIModelService(db_session).set_assignment("primary", profile.id)
    await db_session.commit()
    profile_id, connection_id = profile.id, connection.id
    try:
        yield profile_id
    finally:
        await db_session.rollback()
        for key in ("testing", "primary"):
            if previous.get(key) is not None:
                assignment = await db_session.get(AIModelAssignment, key)
                if assignment is not None:
                    assignment.profile_id = previous[key]
                else:
                    db_session.add(
                        AIModelAssignment(assignment_key=key, profile_id=previous[key])
                    )
            else:
                await db_session.execute(
                    delete(AIModelAssignment).where(AIModelAssignment.assignment_key == key)
                )
        await db_session.flush()
        await db_session.execute(
            delete(AIModelProfile).where(AIModelProfile.id == profile_id)
        )
        await db_session.execute(
            delete(AIProviderConnection).where(
                AIProviderConnection.id == connection_id
            )
        )
        await db_session.commit()


async def _make_finding(
    e2e_client, headers, agent_id: str, description: str = "Slow approval path."
) -> dict:
    resp = e2e_client.post(
        "/api/agent-findings",
        json={
            "agent_id": agent_id,
            "description": description,
            "expected_behavior": "Approve within one step.",
            "finding_kind": "opportunity",
            "evidence_markdown": "## Evidence\n- took 9s\n",
        },
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _cleanup_generation(
    db_session: AsyncSession, *, agent_id: UUID, finding_ids: list[UUID]
) -> None:
    await db_session.rollback()
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
    if finding_ids:
        await db_session.execute(
            delete(AgentFinding).where(AgentFinding.id.in_(finding_ids))
        )
    await db_session.commit()


async def test_generate_rejects_bad_selection(
    e2e_client, platform_admin, org1_user, org2_user, gen_agent, db_session: AsyncSession
):
    agent_id = gen_agent["id"]
    finding_ids: list[UUID] = []
    try:
        finding = await _make_finding(e2e_client, org1_user.headers, agent_id)
        finding_ids.append(UUID(finding["id"]))

        assert (
            e2e_client.post(
                f"/api/agent-evaluations/agents/{agent_id}/tests/generate",
                json={"finding_ids": [str(uuid4())]},
                headers=org1_user.headers,
            ).status_code
            == 404
        )
        assert (
            e2e_client.post(
                f"/api/agent-evaluations/agents/{agent_id}/tests/generate",
                json={"finding_ids": [finding["id"]]},
                headers=org2_user.headers,
            ).status_code
            == 404
        )
        assert (
            e2e_client.post(
                f"/api/agent-evaluations/agents/{agent_id}/tests/generate",
                json={"finding_ids": []},
                headers=org1_user.headers,
            ).status_code
            == 422
        )
        assert (
            e2e_client.post(
                f"/api/agent-evaluations/agents/{agent_id}/tests/generate",
                json={"finding_ids": [str(uuid4()) for _ in range(11)]},
                headers=org1_user.headers,
            ).status_code
            == 422
        )

        other = e2e_client.post(
            "/api/agents",
            json={
                "name": f"Other Gen Agent {uuid4().hex[:8]}",
                "system_prompt": "Reply only with ok.",
                "channels": ["chat"],
                "access_level": "authenticated",
                "organization_id": str(org1_user.organization_id),
            },
            headers=platform_admin.headers,
        )
        assert other.status_code == 201, other.text
        other_finding = await _make_finding(
            e2e_client, org1_user.headers, other.json()["id"], "Elsewhere."
        )
        try:
            wrong_agent = e2e_client.post(
                f"/api/agent-evaluations/agents/{agent_id}/tests/generate",
                json={"finding_ids": [other_finding["id"]]},
                headers=org1_user.headers,
            )
            assert wrong_agent.status_code == 422, wrong_agent.text
        finally:
            await db_session.execute(
                delete(AgentFinding).where(
                    AgentFinding.id == UUID(other_finding["id"])
                )
            )
            await db_session.commit()
            e2e_client.delete(
                f"/api/agents/{other.json()['id']}", headers=platform_admin.headers
            )
    finally:
        await _cleanup_generation(
            db_session, agent_id=UUID(agent_id), finding_ids=finding_ids
        )


async def test_generate_revoked_source_fails_closed(
    e2e_client, org1_user, gen_agent, db_session: AsyncSession
):
    agent_id = gen_agent["id"]
    finding_ids: list[UUID] = []
    try:
        run = AgentRun(
            id=uuid4(),
            agent_id=UUID(agent_id),
            org_id=org1_user.organization_id,
            trigger_type="manual",
            status="completed",
            caller_user_id=str(org1_user.user_id),
            input={"message": "review me"},
            output={"text": "ok"},
            completed_at=datetime.now(timezone.utc),
        )
        run.root_run_id = run.id
        db_session.add(run)
        await db_session.commit()
        run_id = run.id

        finding = e2e_client.post(
            "/api/agent-findings",
            json={
                "agent_id": agent_id,
                "description": "Run-linked issue.",
                "source_kind": "run",
                "source_run_id": str(run_id),
            },
            headers=org1_user.headers,
        )
        assert finding.status_code == 201, finding.text
        finding_ids.append(UUID(finding.json()["id"]))
        # Pin review-style refs: the run FK nulls on delete, but JSON refs
        # survive so revocation stays detectable.
        await db_session.execute(
            AgentFinding.__table__.update()
            .where(AgentFinding.id == UUID(finding.json()["id"]))
            .values(
                source_run_refs=[
                    {
                        "run_id": str(run_id),
                        "agent_id": agent_id,
                        "org_id": str(org1_user.organization_id),
                        "root_run_id": str(run_id),
                        "parent_run_id": None,
                        "trigger_type": "manual",
                    }
                ]
            )
        )
        await db_session.commit()

        await db_session.execute(delete(AgentRun).where(AgentRun.id == run_id))
        await db_session.commit()

        resp = e2e_client.post(
            f"/api/agent-evaluations/agents/{agent_id}/tests/generate",
            json={"finding_ids": [finding.json()["id"]]},
            headers=org1_user.headers,
        )
        assert resp.status_code == 404, resp.text
    finally:
        await _cleanup_generation(
            db_session, agent_id=UUID(agent_id), finding_ids=finding_ids
        )


async def test_generate_dismissed_finding_admits_run(
    e2e_client, org1_user, gen_agent, designer_profile, db_session: AsyncSession
):
    agent_id = gen_agent["id"]
    finding_ids: list[UUID] = []
    run_ids: list[UUID] = []
    try:
        finding = await _make_finding(e2e_client, org1_user.headers, agent_id)
        finding_ids.append(UUID(finding["id"]))
        dismissed = e2e_client.patch(
            f"/api/agent-findings/{finding['id']}",
            json={"status": "dismissed"},
            headers=org1_user.headers,
        )
        assert dismissed.status_code == 200, dismissed.text

        resp = e2e_client.post(
            f"/api/agent-evaluations/agents/{agent_id}/tests/generate",
            json={"finding_ids": [finding["id"]], "requested_count": 1},
            headers=org1_user.headers,
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["status"] == "queued"
        run_ids.append(UUID(resp.json()["run_id"]))
    finally:
        for run_id in run_ids:
            try:
                e2e_client.post(
                    f"/api/agent-runs/{run_id}/cancel", headers=org1_user.headers
                )
            except Exception as e:
                logger.debug(f"run cancel error: {e}")
        await _cleanup_generation(
            db_session, agent_id=UUID(agent_id), finding_ids=finding_ids
        )


def _proposal(**over) -> dict:
    base = {
        "name": "slow-approval",
        "input": {"task": "approve quickly"},
        "fixture": {"entities": {}, "allowed_tools": [], "rules": []},
        "simulator_policy": {},
        "assertions": [{"type": "tool_called", "params": {"tool": "approve"}}],
        "expected_tools": ["approve"],
        "forbidden_tools": [],
        "coverage": "edge",
    }
    base.update(over)
    return base


def _designer_run(*, suite_id: UUID, finding_ids: list[str], output: dict) -> AgentRun:
    run = AgentRun(
        id=uuid4(),
        agent_id=None,
        trigger_type="evaluation_synthetic",
        status="completed",
        output=output,
        completed_at=datetime.now(timezone.utc),
        correlation={
            "evaluation_designer": True,
            "designer_suite_id": str(suite_id),
            "designer_tool_schemas": {
                "approve": {"type": "object", "properties": {}},
            },
            "designer_finding_ids": finding_ids,
        },
    )
    run.root_run_id = run.id
    return run


async def test_materialize_into_default_links_single_finding(
    e2e_client, org1_user, gen_agent, db_session: AsyncSession
):
    agent_id = UUID(gen_agent["id"])
    finding = e2e_client.post(
        "/api/agent-findings",
        json={"agent_id": str(agent_id), "description": "Link me."},
        headers=org1_user.headers,
    )
    assert finding.status_code == 201, finding.text
    finding_id = UUID(finding.json()["id"])
    suite_ids: list[UUID] = []
    run_ids: list[UUID] = []
    try:
        suite = await get_or_create_default_suite(
            db_session, agent_id=agent_id, org_id=org1_user.organization_id
        )
        await db_session.commit()
        suite_ids.append(suite.id)
        run = _designer_run(
            suite_id=suite.id,
            finding_ids=[str(finding_id)],
            output={"proposals": [_proposal()]},
        )
        db_session.add(run)
        await db_session.commit()
        run_ids.append(run.id)

        assert await materialize_designer_drafts(db_session, run) == 1
        await db_session.commit()
        drafts = (
            (
                await db_session.execute(
                    select(AgentEvaluationCase).where(
                        AgentEvaluationCase.suite_id == suite.id
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(drafts) == 1
        assert drafts[0].enabled is False
        assert drafts[0].accepted is False
        assert drafts[0].provenance == "finding"
        assert drafts[0].finding_id == finding_id
    finally:
        await db_session.rollback()
        await db_session.execute(
            delete(AgentEvaluationCase).where(
                AgentEvaluationCase.suite_id.in_(suite_ids)
            )
        )
        await db_session.execute(delete(AgentRun).where(AgentRun.id.in_(run_ids)))
        await db_session.execute(
            delete(AgentEvaluationSuite).where(
                AgentEvaluationSuite.id.in_(suite_ids)
            )
        )
        await db_session.execute(
            delete(AgentFinding).where(AgentFinding.id == finding_id)
        )
        await db_session.commit()


async def test_materialize_still_skips_published_named_suite(
    org1_user, gen_agent, db_session: AsyncSession
):
    agent_id = UUID(gen_agent["id"])
    suite_ids: list[UUID] = []
    run_ids: list[UUID] = []
    try:
        suite = AgentEvaluationSuite(
            id=uuid4(),
            org_id=org1_user.organization_id,
            agent_id=agent_id,
            name=f"Published Named {uuid4().hex[:6]}",
            status="published",
            version=1,
        )
        db_session.add(suite)
        await db_session.commit()
        suite_ids.append(suite.id)
        run = _designer_run(
            suite_id=suite.id, finding_ids=[], output={"proposals": [_proposal()]}
        )
        db_session.add(run)
        await db_session.commit()
        run_ids.append(run.id)

        assert await materialize_designer_drafts(db_session, run) == 0
        await db_session.commit()
        count = await db_session.scalar(
            select(func.count())
            .select_from(AgentEvaluationCase)
            .where(AgentEvaluationCase.suite_id == suite.id)
        )
        assert count == 0
    finally:
        await db_session.rollback()
        await db_session.execute(delete(AgentRun).where(AgentRun.id.in_(run_ids)))
        await db_session.execute(
            delete(AgentEvaluationSuite).where(
                AgentEvaluationSuite.id.in_(suite_ids)
            )
        )
        await db_session.commit()


async def test_generate_review_derived_finding_needs_intact_provenance(
    e2e_client, org1_user, gen_agent, db_session: AsyncSession
):
    """Broken review provenance fails closed through the shared predicate."""
    agent_id = UUID(gen_agent["id"])
    finding_ids: list[UUID] = []
    try:
        finding = AgentFinding(
            id=uuid4(),
            agent_id=agent_id,
            org_id=org1_user.organization_id,
            status="open",
            description="Derived issue.",
            source_kind="run",
            finding_kind="problem",
            source_review_id=uuid4(),
            source_review_version_id=uuid4(),
            source_review_run_id=uuid4(),
            source_review_version=1,
            source_run_refs=[
                {
                    "run_id": str(uuid4()),
                    "agent_id": str(agent_id),
                    "org_id": str(org1_user.organization_id),
                    "root_run_id": None,
                    "parent_run_id": None,
                    "trigger_type": "manual",
                }
            ],
            source_ordinal=0,
            created_by=org1_user.user_id,
        )
        db_session.add(finding)
        await db_session.commit()
        finding_ids.append(finding.id)

        resp = e2e_client.post(
            f"/api/agent-evaluations/agents/{agent_id}/tests/generate",
            json={"finding_ids": [str(finding.id)]},
            headers=org1_user.headers,
        )
        assert resp.status_code == 404, resp.text
    finally:
        await _cleanup_generation(
            db_session, agent_id=agent_id, finding_ids=finding_ids
        )


async def test_generate_authorizes_runs_beyond_history_cap(
    e2e_client, org1_user, gen_agent, designer_profile, db_session: AsyncSession
):
    agent_id = UUID(gen_agent["id"])
    finding_ids: list[UUID] = []
    run_ids: list[UUID] = []
    try:
        for i in range(11):
            run = AgentRun(
                id=uuid4(),
                agent_id=agent_id,
                org_id=org1_user.organization_id,
                trigger_type="manual",
                status="completed",
                caller_user_id=str(org1_user.user_id),
                input={"message": f"run {i}"},
                output={"text": "ok"},
                completed_at=datetime.now(timezone.utc),
            )
            run.root_run_id = run.id
            db_session.add(run)
            run_ids.append(run.id)
        await db_session.commit()
        refs = [
            {
                "run_id": str(run_id),
                "agent_id": str(agent_id),
                "org_id": str(org1_user.organization_id),
                "root_run_id": str(run_id),
                "parent_run_id": None,
                "trigger_type": "manual",
            }
            for run_id in run_ids
        ]
        refs.append(
            {
                "run_id": str(uuid4()),
                "agent_id": str(agent_id),
                "org_id": str(org1_user.organization_id),
                "root_run_id": None,
                "parent_run_id": None,
                "trigger_type": "manual",
            }
        )
        finding = AgentFinding(
            id=uuid4(),
            agent_id=agent_id,
            org_id=org1_user.organization_id,
            status="open",
            description="Many-run issue.",
            source_kind="run",
            finding_kind="problem",
            source_run_refs=refs,
            created_by=org1_user.user_id,
        )
        db_session.add(finding)
        await db_session.commit()
        finding_ids.append(finding.id)

        # The 12th (phantom) ref sits past the history cap: still 404.
        resp = e2e_client.post(
            f"/api/agent-evaluations/agents/{agent_id}/tests/generate",
            json={"finding_ids": [str(finding.id)]},
            headers=org1_user.headers,
        )
        assert resp.status_code == 404, resp.text

        # All-valid refs admit with history capped at 10.
        await db_session.execute(
            AgentFinding.__table__.update()
            .where(AgentFinding.id == finding.id)
            .values(source_run_refs=refs[:11])
        )
        await db_session.commit()
        ok = e2e_client.post(
            f"/api/agent-evaluations/agents/{agent_id}/tests/generate",
            json={"finding_ids": [str(finding.id)], "requested_count": 1},
            headers=org1_user.headers,
        )
        assert ok.status_code == 200, ok.text
        try:
            e2e_client.post(
                f"/api/agent-runs/{ok.json()['run_id']}/cancel",
                headers=org1_user.headers,
            )
        except Exception as e:
            logger.debug(f"run cancel error: {e}")
        run_row = await db_session.get(AgentRun, UUID(ok.json()["run_id"]))
        assert run_row is not None
        assert len(run_row.correlation.get("designer_history_ids", [])) == 10
    finally:
        await db_session.rollback()
        await db_session.execute(delete(AgentRun).where(AgentRun.id.in_(run_ids)))
        await _cleanup_generation(
            db_session, agent_id=agent_id, finding_ids=finding_ids
        )
