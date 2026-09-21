"""Agent-wide test CRUD over the default collection (Phase 4b)."""

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
    AgentEvaluationSuite,
)
from src.models.orm.agent_findings import AgentFinding

logger = logging.getLogger(__name__)

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def crud_agent(e2e_client, platform_admin, org1) -> AsyncGenerator[dict, None]:
    resp = e2e_client.post(
        "/api/agents",
        json={
            "name": f"CRUD Agent {uuid4().hex[:8]}",
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


async def _cleanup_agent_tests(
    db_session: AsyncSession, *, agent_id: UUID
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
    await db_session.commit()


def _test_body(name: str = "Agent-wide test", **overrides) -> dict:
    body = {"name": name}
    body.update(overrides)
    return body


async def test_agent_test_create_get_list_edit_journey(
    e2e_client, org1_user, crud_agent, db_session: AsyncSession
):
    agent_id = crud_agent["id"]
    try:
        created = e2e_client.post(
            f"/api/agent-evaluations/agents/{agent_id}/tests",
            json=_test_body(),
            headers=org1_user.headers,
        )
        assert created.status_code == 201, created.text
        body = created.json()
        logical_id = body["logical_test_id"]
        assert body["version"] == 1
        assert body["origin_is_default"] is True
        assert body["origin_suite_name"] == "Default Tests"

        fetched = e2e_client.get(
            f"/api/agent-evaluations/agents/{agent_id}/tests/{logical_id}",
            headers=org1_user.headers,
        )
        assert fetched.status_code == 200, fetched.text
        assert fetched.json()["case_id"] == body["case_id"]

        listed = e2e_client.get(
            f"/api/agent-evaluations/agents/{agent_id}/tests",
            headers=org1_user.headers,
        )
        assert listed.status_code == 200, listed.text
        page = listed.json()
        assert page["total"] == 1
        assert page["items"][0]["logical_test_id"] == logical_id

        edited = e2e_client.patch(
            f"/api/agent-evaluations/agents/{agent_id}/tests/{logical_id}",
            json={"name": "Renamed test", "expected_version": 1},
            headers=org1_user.headers,
        )
        assert edited.status_code == 200, edited.text
        assert edited.json()["version"] == 2
        assert edited.json()["name"] == "Renamed test"
        assert edited.json()["origin_is_default"] is True

        # Old version row is preserved.
        current = e2e_client.get(
            f"/api/agent-evaluations/agents/{agent_id}/tests/{logical_id}",
            headers=org1_user.headers,
        )
        assert current.json()["version"] == 2

        # Stale guard rejects.
        stale = e2e_client.patch(
            f"/api/agent-evaluations/agents/{agent_id}/tests/{logical_id}",
            json={"name": "Stale", "expected_version": 1},
            headers=org1_user.headers,
        )
        assert stale.status_code == 409, stale.text
    finally:
        await _cleanup_agent_tests(db_session, agent_id=UUID(agent_id))


async def test_agent_test_tenant_isolation(
    e2e_client, org1_user, org2_user, crud_agent, db_session: AsyncSession
):
    agent_id = crud_agent["id"]
    try:
        created = e2e_client.post(
            f"/api/agent-evaluations/agents/{agent_id}/tests",
            json=_test_body(),
            headers=org1_user.headers,
        )
        assert created.status_code == 201, created.text
        logical_id = created.json()["logical_test_id"]

        assert (
            e2e_client.get(
                f"/api/agent-evaluations/agents/{agent_id}/tests/{logical_id}",
                headers=org2_user.headers,
            ).status_code
            == 404
        )
        listed = e2e_client.get(
            f"/api/agent-evaluations/agents/{agent_id}/tests",
            headers=org2_user.headers,
        )
        assert listed.status_code == 404, listed.text
        denied = e2e_client.post(
            f"/api/agent-evaluations/agents/{agent_id}/tests",
            json=_test_body(),
            headers=org2_user.headers,
        )
        assert denied.status_code == 404, denied.text
    finally:
        await _cleanup_agent_tests(db_session, agent_id=UUID(agent_id))


async def test_agent_test_edit_copies_named_version_to_default(
    e2e_client, platform_admin, org1_user, crud_agent, db_session: AsyncSession
):
    agent_id = crud_agent["id"]
    try:
        suite_resp = e2e_client.post(
            "/api/agent-evaluations/suites",
            json={
                "name": f"Named Origin {uuid4().hex[:8]}",
                "agent_id": agent_id,
                "organization_id": str(org1_user.organization_id),
            },
            headers=platform_admin.headers,
        )
        assert suite_resp.status_code == 200, suite_resp.text
        suite = suite_resp.json()
        case_resp = e2e_client.post(
            f"/api/agent-evaluations/suites/{suite['id']}/cases",
            json=_test_body("Named test"),
            headers=platform_admin.headers,
        )
        assert case_resp.status_code == 200, case_resp.text
        origin_case = case_resp.json()

        rows = (
            (
                await db_session.execute(
                    select(AgentEvaluationCase).where(
                        AgentEvaluationCase.id == UUID(origin_case["id"])
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(rows) == 1
        logical_id = str(rows[0].logical_test_id)

        edited = e2e_client.patch(
            f"/api/agent-evaluations/agents/{agent_id}/tests/{logical_id}",
            json={"name": "Edited from agent-wide"},
            headers=org1_user.headers,
        )
        assert edited.status_code == 200, edited.text
        assert edited.json()["version"] == 2
        assert edited.json()["origin_is_default"] is True
        assert edited.json()["logical_test_id"] == logical_id
        # Named origin row untouched.
        origin = e2e_client.get(
            f"/api/agent-evaluations/suites/{suite['id']}/cases",
            headers=platform_admin.headers,
        )
        assert origin.status_code == 200, origin.text
        assert len(origin.json()) == 1
        assert origin.json()[0]["version"] == 1
    finally:
        await _cleanup_agent_tests(db_session, agent_id=UUID(agent_id))


async def test_agent_test_finding_link_and_invalid_content(
    e2e_client, platform_admin, org1_user, crud_agent, db_session: AsyncSession
):
    agent_id = crud_agent["id"]
    finding_id = None
    try:
        finding = e2e_client.post(
            "/api/agent-findings",
            json={"agent_id": agent_id, "description": "Repro me."},
            headers=org1_user.headers,
        )
        assert finding.status_code == 201, finding.text
        finding_id = finding.json()["id"]

        linked = e2e_client.post(
            f"/api/agent-evaluations/agents/{agent_id}/tests",
            json=_test_body("Finding repro", finding_id=finding_id),
            headers=org1_user.headers,
        )
        assert linked.status_code == 201, linked.text
        assert linked.json()["finding_id"] == finding_id
        assert linked.json()["provenance"] == "finding"

        bad = e2e_client.post(
            f"/api/agent-evaluations/agents/{agent_id}/tests",
            json=_test_body("Bad assertion", assertions=[{"type": "bogus"}]),
            headers=org1_user.headers,
        )
        assert bad.status_code == 422, bad.text
    finally:
        await _cleanup_agent_tests(db_session, agent_id=UUID(agent_id))
        if finding_id is not None:
            from src.models.orm.agent_findings import AgentFinding

            await db_session.execute(
                delete(AgentFinding).where(AgentFinding.id == UUID(finding_id))
            )
            await db_session.commit()


async def test_agent_test_rejects_review_finding_with_broken_provenance(
    e2e_client, org1_user, crud_agent, db_session: AsyncSession
):
    agent_id = UUID(crud_agent["id"])
    finding = AgentFinding(
        id=uuid4(),
        agent_id=agent_id,
        org_id=org1_user.organization_id,
        status="open",
        description="Derived issue with unavailable review evidence.",
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
    finding_id = finding.id

    try:
        response = e2e_client.post(
            f"/api/agent-evaluations/agents/{agent_id}/tests",
            json=_test_body("Broken review finding", finding_id=str(finding_id)),
            headers=org1_user.headers,
        )
        assert response.status_code == 404, response.text
    finally:
        await _cleanup_agent_tests(db_session, agent_id=agent_id)
        await db_session.execute(
            delete(AgentFinding).where(AgentFinding.id == finding_id)
        )
        await db_session.commit()


async def test_agent_test_edit_enforces_quotas(
    e2e_client, org1_user, crud_agent, db_session: AsyncSession
):
    agent_id = crud_agent["id"]
    try:
        created = e2e_client.post(
            f"/api/agent-evaluations/agents/{agent_id}/tests",
            json=_test_body("Quota test"),
            headers=org1_user.headers,
        )
        assert created.status_code == 201, created.text
        logical_id = created.json()["logical_test_id"]

        oversized = e2e_client.patch(
            f"/api/agent-evaluations/agents/{agent_id}/tests/{logical_id}",
            json={"fixture": {"blob": "x" * (300 * 1024)}},
            headers=org1_user.headers,
        )
        assert oversized.status_code == 429, oversized.text
        assert "Fixture" in oversized.json()["detail"]
    finally:
        await _cleanup_agent_tests(db_session, agent_id=UUID(agent_id))


async def test_agent_test_edit_preserves_frozen_assertions(
    e2e_client, org1_user, crud_agent, db_session: AsyncSession
):
    agent_id = crud_agent["id"]
    try:
        created = e2e_client.post(
            f"/api/agent-evaluations/agents/{agent_id}/tests",
            json=_test_body("Semantic test"),
            headers=org1_user.headers,
        )
        assert created.status_code == 201, created.text
        logical_id = created.json()["logical_test_id"]
        case_id = UUID(created.json()["case_id"])

        frozen = [
            {
                "type": "llm_judge",
                "params": {
                    "judge_snapshot": {
                        "profile_id": str(uuid4()),
                        "provider": "openai",
                        "model": "judge-model",
                    }
                },
            }
        ]
        row = await db_session.get(AgentEvaluationCase, case_id)
        assert row is not None
        row.assertions = frozen
        await db_session.commit()

        edited = e2e_client.patch(
            f"/api/agent-evaluations/agents/{agent_id}/tests/{logical_id}",
            json={"name": "Renamed semantic test"},
            headers=org1_user.headers,
        )
        assert edited.status_code == 200, edited.text
        assert edited.json()["version"] == 2
        assert edited.json()["assertions"] == frozen
    finally:
        await _cleanup_agent_tests(db_session, agent_id=UUID(agent_id))


async def test_agent_test_edit_explicit_null_clears_nullable_fields(
    e2e_client, org1_user, crud_agent, db_session: AsyncSession
):
    agent_id = crud_agent["id"]
    try:
        created = e2e_client.post(
            f"/api/agent-evaluations/agents/{agent_id}/tests",
            json=_test_body(
                "Nullable test",
                input={"message": "hi"},
                output_schema={"type": "object"},
            ),
            headers=org1_user.headers,
        )
        assert created.status_code == 201, created.text
        logical_id = created.json()["logical_test_id"]
        assert created.json()["input"] == {"message": "hi"}

        cleared = e2e_client.patch(
            f"/api/agent-evaluations/agents/{agent_id}/tests/{logical_id}",
            json={"name": "Still set"},
            headers=org1_user.headers,
        )
        assert cleared.status_code == 200, cleared.text
        # Omission preserves the non-null values; only explicit null clears.
        assert cleared.json()["input"] == {"message": "hi"}
        assert cleared.json()["output_schema"] == {"type": "object"}
        assert cleared.json()["name"] == "Still set"

        nulled = e2e_client.patch(
            f"/api/agent-evaluations/agents/{agent_id}/tests/{logical_id}",
            json={"input": None, "output_schema": None},
            headers=org1_user.headers,
        )
        assert nulled.status_code == 200, nulled.text
        assert nulled.json()["input"] is None
        assert nulled.json()["output_schema"] is None
    finally:
        await _cleanup_agent_tests(db_session, agent_id=UUID(agent_id))
