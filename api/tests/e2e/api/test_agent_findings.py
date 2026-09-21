"""Agent findings API: create/list/get/dismiss + tenant isolation.

Findings use the evaluation tenant gate: cross-org access reads as 404,
run sources must belong to the finding's agent, and dismissal needs no test.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import AsyncGenerator
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.orm.agent_evaluations import (
    AgentEvaluationCase,
    AgentEvaluationSuite,
)
from src.models.orm.agent_findings import AgentFinding
from src.models.orm.agent_runs import AgentRun

logger = logging.getLogger(__name__)

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def org_agent(e2e_client, platform_admin, org1) -> AsyncGenerator[dict, None]:
    """Org1 agent with authenticated access for permission checks."""
    resp = e2e_client.post(
        "/api/agents",
        json={
            "name": f"Findings Agent {uuid4().hex[:8]}",
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
        e2e_client.delete(
            f"/api/agents/{agent['id']}", headers=platform_admin.headers
        )
    except Exception as e:
        logger.debug(f"fixture cleanup error: {e}")


@pytest_asyncio.fixture
async def org_suite(
    e2e_client, platform_admin, org1, org_agent
) -> AsyncGenerator[dict, None]:
    """Draft evaluation suite targeting the org agent."""
    resp = e2e_client.post(
        "/api/agent-evaluations/suites",
        json={
            "name": f"Findings Suite {uuid4().hex[:8]}",
            "agent_id": org_agent["id"],
            "organization_id": org1["id"],
        },
        headers=platform_admin.headers,
    )
    assert resp.status_code == 200, resp.text
    suite = resp.json()
    yield suite


async def test_case_created_from_finding_freezes_finding_provenance(
    e2e_client, platform_admin, org_agent, org_suite, db_session: AsyncSession
):
    finding = e2e_client.post(
        "/api/agent-findings",
        json={
            "agent_id": org_agent["id"],
            "description": "Reproducible wrong tool call.",
            "expected_behavior": "Look up before answering.",
        },
        headers=platform_admin.headers,
    )
    assert finding.status_code == 201, finding.text
    finding_id = finding.json()["id"]

    case = e2e_client.post(
        f"/api/agent-evaluations/suites/{org_suite['id']}/cases",
        json={"name": "finding-repro", "finding_id": finding_id},
        headers=platform_admin.headers,
    )
    assert case.status_code == 200, case.text
    body = case.json()
    assert body["provenance"] == "finding"
    assert body["finding_id"] == finding_id

    detail = e2e_client.get(
        f"/api/agent-findings/{finding_id}", headers=platform_admin.headers
    )
    assert detail.status_code == 200, detail.text
    assert body["id"] in detail.json()["linked_case_ids"]

    await db_session.execute(
        delete(AgentEvaluationCase).where(
            AgentEvaluationCase.suite_id == UUID(org_suite["id"])
        )
    )
    await db_session.execute(
        delete(AgentEvaluationSuite).where(
            AgentEvaluationSuite.id == UUID(org_suite["id"])
        )
    )
    await db_session.execute(
        delete(AgentFinding).where(AgentFinding.id == UUID(finding_id))
    )
    await db_session.commit()


async def test_case_rejects_foreign_agent_finding(
    e2e_client, platform_admin, org_agent, org_suite, db_session: AsyncSession
):
    other = e2e_client.post(
        "/api/agents",
        json={
            "name": f"Foreign Findings Agent {uuid4().hex[:8]}",
            "system_prompt": "Reply only with ok.",
            "channels": ["chat"],
            "access_level": "authenticated",
        },
        headers=platform_admin.headers,
    )
    assert other.status_code == 201, other.text
    foreign = e2e_client.post(
        "/api/agent-findings",
        json={
            "agent_id": other.json()["id"],
            "description": "Elsewhere.",
        },
        headers=platform_admin.headers,
    )
    assert foreign.status_code == 201, foreign.text
    try:
        case = e2e_client.post(
            f"/api/agent-evaluations/suites/{org_suite['id']}/cases",
            json={"name": "foreign-repro", "finding_id": foreign.json()["id"]},
            headers=platform_admin.headers,
        )
        assert case.status_code == 422, case.text
    finally:
        await db_session.execute(
            delete(AgentEvaluationCase).where(
                AgentEvaluationCase.suite_id == UUID(org_suite["id"])
            )
        )
        await db_session.execute(
            delete(AgentEvaluationSuite).where(
                AgentEvaluationSuite.id == UUID(org_suite["id"])
            )
        )
        await db_session.execute(
            delete(AgentFinding).where(
                AgentFinding.id == UUID(foreign.json()["id"])
            )
        )
        await db_session.commit()
        e2e_client.delete(
            f"/api/agents/{other.json()['id']}", headers=platform_admin.headers
        )


async def test_case_rejects_review_finding_with_broken_provenance(
    e2e_client, org1_user, org_agent, org_suite, db_session: AsyncSession
):
    agent_id = UUID(org_agent["id"])
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
            f"/api/agent-evaluations/suites/{org_suite['id']}/cases",
            json={"name": "broken-review-repro", "finding_id": str(finding_id)},
            headers=org1_user.headers,
        )
        assert response.status_code == 404, response.text
    finally:
        await db_session.execute(
            delete(AgentEvaluationCase).where(
                AgentEvaluationCase.suite_id == UUID(org_suite["id"])
            )
        )
        await db_session.execute(
            delete(AgentEvaluationSuite).where(
                AgentEvaluationSuite.id == UUID(org_suite["id"])
            )
        )
        await db_session.execute(
            delete(AgentFinding).where(AgentFinding.id == finding_id)
        )
        await db_session.commit()


@pytest_asyncio.fixture
async def org_run(org_agent, db_session: AsyncSession) -> AsyncGenerator[AgentRun, None]:
    """Completed run owned by the org agent (DB-seeded, no LLM)."""
    run = AgentRun(
        id=uuid4(),
        agent_id=UUID(org_agent["id"]),
        trigger_type="test",
        status="completed",
        iterations_used=1,
        tokens_used=50,
        input={"message": "hi"},
        output={"text": "ok"},
        completed_at=datetime.now(timezone.utc),
    )
    db_session.add(run)
    await db_session.commit()
    yield run
    await db_session.execute(delete(AgentRun).where(AgentRun.id == run.id))
    await db_session.execute(
        delete(AgentFinding).where(AgentFinding.source_run_id == run.id)
    )
    await db_session.commit()


async def test_create_and_read_manual_finding(e2e_client, platform_admin, org_agent):
    created = e2e_client.post(
        "/api/agent-findings",
        json={
            "agent_id": org_agent["id"],
            "description": "Routes without confirming.",
            "expected_behavior": "Ask one clarifying question first.",
        },
        headers=platform_admin.headers,
    )
    assert created.status_code == 201, created.text
    finding = created.json()
    assert finding["status"] == "open"
    assert finding["source_kind"] == "manual"
    assert finding["linked_case_ids"] == []

    fetched = e2e_client.get(
        f"/api/agent-findings/{finding['id']}", headers=platform_admin.headers
    )
    assert fetched.status_code == 200, fetched.text
    assert fetched.json()["description"] == "Routes without confirming."

    listed = e2e_client.get(
        "/api/agent-findings",
        params={"agent_id": org_agent["id"]},
        headers=platform_admin.headers,
    )
    assert listed.status_code == 200, listed.text
    assert any(item["id"] == finding["id"] for item in listed.json())


async def test_dismiss_finding_without_test(e2e_client, platform_admin, org_agent):
    created = e2e_client.post(
        "/api/agent-findings",
        json={"agent_id": org_agent["id"], "description": "Stale note."},
        headers=platform_admin.headers,
    )
    assert created.status_code == 201, created.text
    finding_id = created.json()["id"]

    dismissed = e2e_client.patch(
        f"/api/agent-findings/{finding_id}",
        json={"status": "dismissed"},
        headers=platform_admin.headers,
    )
    assert dismissed.status_code == 200, dismissed.text
    assert dismissed.json()["status"] == "dismissed"

    listed = e2e_client.get(
        "/api/agent-findings",
        params={"agent_id": org_agent["id"], "status": "open"},
        headers=platform_admin.headers,
    )
    assert listed.status_code == 200, listed.text
    assert all(item["id"] != finding_id for item in listed.json())


async def test_run_sourced_finding_links_same_agent_run(
    e2e_client, platform_admin, org_agent, org_run
):
    created = e2e_client.post(
        "/api/agent-findings",
        json={
            "agent_id": org_agent["id"],
            "description": "Wrong tool call.",
            "expected_behavior": "Call lookup before answering.",
            "source_kind": "run",
            "source_run_id": str(org_run.id),
            "source_sequence": 3,
        },
        headers=platform_admin.headers,
    )
    assert created.status_code == 201, created.text
    finding = created.json()
    assert finding["source_run_id"] == str(org_run.id)
    assert finding["source_sequence"] == 3


async def test_run_sourced_finding_create_returns_existing_for_same_run(
    e2e_client, platform_admin, org_agent, org_run, db_session: AsyncSession
):
    body = {
        "agent_id": org_agent["id"],
        "description": "Wrong tool call.",
        "expected_behavior": "Call lookup before answering.",
        "source_kind": "run",
        "source_run_id": str(org_run.id),
    }
    first = e2e_client.post(
        "/api/agent-findings",
        json=body,
        headers=platform_admin.headers,
    )
    assert first.status_code == 201, first.text

    second = e2e_client.post(
        "/api/agent-findings",
        json={**body, "description": "Second tab duplicate."},
        headers=platform_admin.headers,
    )
    assert second.status_code == 200, second.text
    assert second.json()["id"] == first.json()["id"]
    assert second.json()["description"] == "Wrong tool call."

    count = (
        await db_session.execute(
            select(func.count())
            .select_from(AgentFinding)
            .where(AgentFinding.source_run_id == org_run.id)
        )
    ).scalar_one()
    assert count == 1


async def test_create_finding_openapi_documents_existing_200_response(e2e_client):
    spec = e2e_client.get("/openapi.json")
    assert spec.status_code == 200, spec.text

    responses = spec.json()["paths"]["/api/agent-findings"]["post"]["responses"]
    assert responses["200"]["description"] == "Existing run-sourced finding"
    assert (
        responses["200"]["content"]["application/json"]["schema"]["$ref"]
        == "#/components/schemas/FindingPublic"
    )
    assert (
        responses["201"]["content"]["application/json"]["schema"]["$ref"]
        == "#/components/schemas/FindingPublic"
    )


async def test_run_source_must_belong_to_agent(
    e2e_client, platform_admin, org_agent, org_run, db_session: AsyncSession
):
    other = e2e_client.post(
        "/api/agents",
        json={
            "name": f"Other Findings Agent {uuid4().hex[:8]}",
            "system_prompt": "Reply only with ok.",
            "channels": ["chat"],
            "access_level": "authenticated",
        },
        headers=platform_admin.headers,
    )
    assert other.status_code == 201, other.text
    other_agent = other.json()
    other_run = AgentRun(
        id=uuid4(),
        agent_id=UUID(other_agent["id"]),
        trigger_type="test",
        status="completed",
        iterations_used=1,
        tokens_used=10,
        input={},
        output={},
        completed_at=datetime.now(timezone.utc),
    )
    db_session.add(other_run)
    await db_session.commit()
    try:
        resp = e2e_client.post(
            "/api/agent-findings",
            json={
                "agent_id": org_agent["id"],
                "description": "Cross-agent link.",
                "source_kind": "run",
                "source_run_id": str(other_run.id),
            },
            headers=platform_admin.headers,
        )
        assert resp.status_code == 422, resp.text
    finally:
        await db_session.execute(
            delete(AgentRun).where(AgentRun.id == other_run.id)
        )
        await db_session.commit()
        e2e_client.delete(
            f"/api/agents/{other_agent['id']}", headers=platform_admin.headers
        )


async def test_cross_org_finding_access_reads_as_not_found(
    e2e_client, platform_admin, org2_user, org_agent
):
    listed = e2e_client.get(
        "/api/agent-findings",
        params={"agent_id": org_agent["id"]},
        headers=org2_user.headers,
    )
    assert listed.status_code == 404, listed.text

    created = e2e_client.post(
        "/api/agent-findings",
        json={"agent_id": org_agent["id"], "description": "Hidden."},
        headers=platform_admin.headers,
    )
    assert created.status_code == 201, created.text

    fetched = e2e_client.get(
        f"/api/agent-findings/{created.json()['id']}",
        headers=org2_user.headers,
    )
    assert fetched.status_code == 404, fetched.text

    denied = e2e_client.post(
        "/api/agent-findings",
        json={"agent_id": org_agent["id"], "description": "Nope."},
        headers=org2_user.headers,
    )
    assert denied.status_code == 404, denied.text


@pytest_asyncio.fixture
async def global_agent(e2e_client, platform_admin) -> AsyncGenerator[dict, None]:
    """Shared global agent (no org): both tenants can see the agent itself."""
    resp = e2e_client.post(
        "/api/agents",
        json={
            "name": f"Global Findings Agent {uuid4().hex[:8]}",
            "system_prompt": "Reply only with ok.",
            "channels": ["chat"],
            "access_level": "authenticated",
            "organization_id": None,
        },
        headers=platform_admin.headers,
    )
    assert resp.status_code == 201, resp.text
    agent = resp.json()
    assert agent["organization_id"] is None
    yield agent
    try:
        e2e_client.delete(
            f"/api/agents/{agent['id']}", headers=platform_admin.headers
        )
    except Exception as e:
        logger.debug(f"fixture cleanup error: {e}")


async def test_global_agent_findings_isolated_by_caller_tenant(
    e2e_client, org1_user, org2_user, global_agent, db_session: AsyncSession
):
    """Same shared agent: org2 sees neither list nor read nor mutate."""
    created = e2e_client.post(
        "/api/agent-findings",
        json={
            "agent_id": global_agent["id"],
            "description": "Org1 private note on shared agent.",
        },
        headers=org1_user.headers,
    )
    assert created.status_code == 201, created.text
    finding_id = created.json()["id"]
    try:
        assert created.json()["org_id"] == str(org1_user.organization_id)

        listed = e2e_client.get(
            "/api/agent-findings",
            params={"agent_id": global_agent["id"]},
            headers=org2_user.headers,
        )
        assert listed.status_code == 200, listed.text
        assert listed.json() == []

        assert (
            e2e_client.get(
                f"/api/agent-findings/{finding_id}", headers=org2_user.headers
            ).status_code
            == 404
        )
        assert (
            e2e_client.patch(
                f"/api/agent-findings/{finding_id}",
                json={"status": "dismissed"},
                headers=org2_user.headers,
            ).status_code
            == 404
        )
        # Owner tenant still reads and dismisses.
        assert (
            e2e_client.get(
                f"/api/agent-findings/{finding_id}", headers=org1_user.headers
            ).status_code
            == 200
        )
        dismissed = e2e_client.patch(
            f"/api/agent-findings/{finding_id}",
            json={"status": "dismissed"},
            headers=org1_user.headers,
        )
        assert dismissed.status_code == 200, dismissed.text
    finally:
        await db_session.execute(
            delete(AgentFinding).where(AgentFinding.id == UUID(finding_id))
        )
        await db_session.commit()


async def test_cross_tenant_source_run_rejected(
    e2e_client, platform_admin, org1_user, org_agent, org_run, global_agent,
    db_session: AsyncSession,
):
    """An invisible run reads as not found; manual/external run refs validate."""
    # Admin sees the run but it belongs to another agent -> 422.
    mismatch = e2e_client.post(
        "/api/agent-findings",
        json={
            "agent_id": global_agent["id"],
            "description": "Wrong-agent run link.",
            "source_kind": "run",
            "source_run_id": str(org_run.id),
        },
        headers=platform_admin.headers,
    )
    assert mismatch.status_code == 422, mismatch.text

    # The DB-seeded run carries no org, so canonical run access hides it
    # from org users entirely -> 404 (not a same-agent 422).
    hidden = e2e_client.post(
        "/api/agent-findings",
        json={
            "agent_id": org_agent["id"],
            "description": "Hidden run link.",
            "source_kind": "run",
            "source_run_id": str(org_run.id),
        },
        headers=org1_user.headers,
    )
    assert hidden.status_code == 404, hidden.text

    # External refs carrying another agent's run are validated too.
    external = e2e_client.post(
        "/api/agent-findings",
        json={
            "agent_id": global_agent["id"],
            "description": "External with run ref.",
            "source_kind": "external",
            "external_ref": "ticket-123",
            "source_run_id": str(org_run.id),
        },
        headers=platform_admin.headers,
    )
    assert external.status_code == 422, external.text

    # External refs without runs stay allowed.
    clean = e2e_client.post(
        "/api/agent-findings",
        json={
            "agent_id": global_agent["id"],
            "description": "Pure external.",
            "source_kind": "external",
            "external_ref": "ticket-123",
        },
        headers=platform_admin.headers,
    )
    assert clean.status_code == 201, clean.text
    await db_session.execute(
        delete(AgentFinding).where(
            AgentFinding.id == UUID(clean.json()["id"])
        )
    )
    await db_session.commit()


async def test_case_link_respects_finding_tenant(
    e2e_client, platform_admin, org1_user, org2_user, org1, org2, global_agent,
    db_session: AsyncSession,
):
    """org2 cannot freeze org1's finding into its suite, even on a global agent."""
    finding = e2e_client.post(
        "/api/agent-findings",
        json={
            "agent_id": global_agent["id"],
            "description": "Org1 finding for link test.",
        },
        headers=org1_user.headers,
    )
    assert finding.status_code == 201, finding.text
    finding_id = finding.json()["id"]
    suite = e2e_client.post(
        "/api/agent-evaluations/suites",
        json={
            "name": f"Org2 Link Suite {uuid4().hex[:8]}",
            "agent_id": global_agent["id"],
            "organization_id": org2["id"],
        },
        headers=platform_admin.headers,
    )
    assert suite.status_code == 200, suite.text
    suite_id = suite.json()["id"]
    try:
        denied = e2e_client.post(
            f"/api/agent-evaluations/suites/{suite_id}/cases",
            json={"name": "cross-link", "finding_id": finding_id},
            headers=org2_user.headers,
        )
        assert denied.status_code == 404, denied.text

        # Admins link explicitly; the foreign-suite case stays hidden from org1.
        from src.models.orm.agent_evaluations import (
            AgentEvaluationCase,
            AgentEvaluationSuite,
        )

        allowed = e2e_client.post(
            f"/api/agent-evaluations/suites/{suite_id}/cases",
            json={"name": "cross-link", "finding_id": finding_id},
            headers=platform_admin.headers,
        )
        assert allowed.status_code == 200, allowed.text
        detail = e2e_client.get(
            f"/api/agent-findings/{finding_id}", headers=org1_user.headers
        )
        assert detail.status_code == 200, detail.text
        assert detail.json()["linked_case_ids"] == []
        admin_detail = e2e_client.get(
            f"/api/agent-findings/{finding_id}", headers=platform_admin.headers
        )
        assert admin_detail.status_code == 200, admin_detail.text
        assert allowed.json()["id"] in admin_detail.json()["linked_case_ids"]
        await db_session.execute(
            delete(AgentEvaluationCase).where(
                AgentEvaluationCase.suite_id == UUID(suite_id)
            )
        )
        await db_session.execute(
            delete(AgentEvaluationSuite).where(
                AgentEvaluationSuite.id == UUID(suite_id)
            )
        )
        await db_session.commit()
    finally:
        await db_session.execute(
            delete(AgentFinding).where(AgentFinding.id == UUID(finding_id))
        )
        await db_session.commit()
