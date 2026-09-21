"""Findings search/evidence API contracts.

Covers the additive Findings slice: Markdown evidence + kind on create/update,
global /search with shared SQL visibility before count/page, explicit-null vs
omission for evidence, provenance spoof rejection, dismissal independence,
and review-results parity. No model jobs are launched.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import AsyncGenerator
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.orm.agent_findings import AgentFinding
from src.models.orm.agent_reviews import (
    AgentReviewDefinition,
    AgentReviewRun,
    AgentReviewVersion,
)
from src.models.orm.agent_runs import AgentRun

logger = logging.getLogger(__name__)

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def search_agent(e2e_client, platform_admin, org1) -> AsyncGenerator[dict, None]:
    resp = e2e_client.post(
        "/api/agents",
        json={
            "name": f"Findings Search Agent {uuid4().hex[:8]}",
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


async def _cleanup_findings(db_session: AsyncSession, ids: list[UUID]) -> None:
    if ids:
        await db_session.execute(delete(AgentFinding).where(AgentFinding.id.in_(ids)))
        await db_session.commit()


async def test_create_search_read_dismiss_journey(
    e2e_client, platform_admin, search_agent, db_session: AsyncSession
):
    created = e2e_client.post(
        "/api/agent-findings",
        json={
            "agent_id": search_agent["id"],
            "description": "Slow approval path.",
            "expected_behavior": "Approve within one step.",
            "finding_kind": "opportunity",
            "evidence_markdown": "# Evidence\n- step took 9s\n",
        },
        headers=platform_admin.headers,
    )
    assert created.status_code == 201, created.text
    body = created.json()
    finding_id = body["id"]
    assert body["finding_kind"] == "opportunity"
    assert body["evidence_markdown"] == "# Evidence\n- step took 9s\n"
    assert body["source_review_id"] is None
    assert body["linked_case_ids"] == []
    try:
        searched = e2e_client.get(
            "/api/agent-findings/search",
            params={"q": "approval path"},
            headers=platform_admin.headers,
        )
        assert searched.status_code == 200, searched.text
        page = searched.json()
        assert page["total"] >= 1
        assert any(item["id"] == finding_id for item in page["items"])
        assert page["limit"] == 50
        assert page["offset"] == 0

        fetched = e2e_client.get(
            f"/api/agent-findings/{finding_id}", headers=platform_admin.headers
        )
        assert fetched.status_code == 200, fetched.text
        assert fetched.json()["evidence_markdown"] == "# Evidence\n- step took 9s\n"

        dismissed = e2e_client.patch(
            f"/api/agent-findings/{finding_id}",
            json={"status": "dismissed"},
            headers=platform_admin.headers,
        )
        assert dismissed.status_code == 200, dismissed.text
        assert dismissed.json()["status"] == "dismissed"
        # Dismissal is independent: evidence and kind survive.
        assert dismissed.json()["finding_kind"] == "opportunity"
        assert dismissed.json()["evidence_markdown"] == "# Evidence\n- step took 9s\n"

        open_page = e2e_client.get(
            "/api/agent-findings/search",
            params={"status": "open", "agent_id": search_agent["id"]},
            headers=platform_admin.headers,
        )
        assert open_page.status_code == 200, open_page.text
        assert all(item["id"] != finding_id for item in open_page.json()["items"])
    finally:
        await _cleanup_findings(db_session, [UUID(finding_id)])


async def test_explicit_null_clears_evidence_vs_omission_preserves(
    e2e_client, platform_admin, search_agent, db_session: AsyncSession
):
    created = e2e_client.post(
        "/api/agent-findings",
        json={
            "agent_id": search_agent["id"],
            "description": "Evidence lifecycle.",
            "evidence_markdown": "initial",
        },
        headers=platform_admin.headers,
    )
    assert created.status_code == 201, created.text
    finding_id = created.json()["id"]
    try:
        omitted = e2e_client.patch(
            f"/api/agent-findings/{finding_id}",
            json={"description": "Evidence lifecycle v2"},
            headers=platform_admin.headers,
        )
        assert omitted.status_code == 200, omitted.text
        assert omitted.json()["evidence_markdown"] == "initial"

        cleared = e2e_client.patch(
            f"/api/agent-findings/{finding_id}",
            json={"evidence_markdown": None},
            headers=platform_admin.headers,
        )
        assert cleared.status_code == 200, cleared.text
        assert cleared.json()["evidence_markdown"] is None
    finally:
        await _cleanup_findings(db_session, [UUID(finding_id)])


async def test_provenance_spoof_rejected(
    e2e_client, platform_admin, search_agent, db_session: AsyncSession
):
    created = e2e_client.post(
        "/api/agent-findings",
        json={
            "agent_id": search_agent["id"],
            "description": "Spoof attempt.",
            "source_review_id": str(uuid4()),
        },
        headers=platform_admin.headers,
    )
    assert created.status_code == 422, created.text

    ok = e2e_client.post(
        "/api/agent-findings",
        json={"agent_id": search_agent["id"], "description": "Spoof target."},
        headers=platform_admin.headers,
    )
    assert ok.status_code == 201, ok.text
    finding_id = ok.json()["id"]
    try:
        spoofed = e2e_client.patch(
            f"/api/agent-findings/{finding_id}",
            json={"source_review_run_id": str(uuid4())},
            headers=platform_admin.headers,
        )
        assert spoofed.status_code == 422, spoofed.text
    finally:
        await _cleanup_findings(db_session, [UUID(finding_id)])


async def test_search_authorization_count_paging(
    e2e_client, platform_admin, org2_user, search_agent, db_session: AsyncSession
):
    ids: list[UUID] = []
    try:
        for i in range(3):
            created = e2e_client.post(
                "/api/agent-findings",
                json={
                    "agent_id": search_agent["id"],
                    "description": f"Paged finding {i} {uuid4().hex[:6]}",
                },
                headers=platform_admin.headers,
            )
            assert created.status_code == 201, created.text
            ids.append(UUID(created.json()["id"]))

        # Foreign tenant sees no items and no leaked total.
        hidden = e2e_client.get(
            "/api/agent-findings/search",
            params={"agent_id": search_agent["id"]},
            headers=org2_user.headers,
        )
        assert hidden.status_code in (200, 404), hidden.text
        if hidden.status_code == 200:
            assert hidden.json()["total"] == 0
            assert hidden.json()["items"] == []

        first = e2e_client.get(
            "/api/agent-findings/search",
            params={"agent_id": search_agent["id"], "limit": 2, "offset": 0},
            headers=platform_admin.headers,
        )
        assert first.status_code == 200, first.text
        assert first.json()["total"] >= 3
        assert len(first.json()["items"]) == 2

        second = e2e_client.get(
            "/api/agent-findings/search",
            params={"agent_id": search_agent["id"], "limit": 2, "offset": 2},
            headers=platform_admin.headers,
        )
        assert second.status_code == 200, second.text
        assert second.json()["total"] == first.json()["total"]
        first_ids = {item["id"] for item in first.json()["items"]}
        second_ids = {item["id"] for item in second.json()["items"]}
        assert first_ids.isdisjoint(second_ids)

        # Nonadmin cannot expand tenant scope.
        forbidden = e2e_client.get(
            "/api/agent-findings/search",
            params={"organization_id": str(uuid4())},
            headers=org2_user.headers,
        )
        assert forbidden.status_code == 403, forbidden.text
    finally:
        await _cleanup_findings(db_session, ids)


async def test_search_filters_and_literal_query(
    e2e_client, platform_admin, search_agent, db_session: AsyncSession
):
    created = e2e_client.post(
        "/api/agent-findings",
        json={
            "agent_id": search_agent["id"],
            "description": "Cache 100% MISS_rate spike.",
            "finding_kind": "problem",
            "evidence_markdown": "unique-token-xyz",
        },
        headers=platform_admin.headers,
    )
    assert created.status_code == 201, created.text
    finding_id = created.json()["id"]
    try:
        # Case-insensitive literal match.
        hit = e2e_client.get(
            "/api/agent-findings/search",
            params={"q": "cache 100% miss_RATE"},
            headers=platform_admin.headers,
        )
        assert hit.status_code == 200, hit.text
        assert any(item["id"] == finding_id for item in hit.json()["items"])

        # Wildcard characters are escaped: literal % does not match everything.
        escaped = e2e_client.get(
            "/api/agent-findings/search",
            params={"q": "100%"},
            headers=platform_admin.headers,
        )
        assert escaped.status_code == 200, escaped.text
        assert any(item["id"] == finding_id for item in escaped.json()["items"])

        miss = e2e_client.get(
            "/api/agent-findings/search",
            params={"q": "no-such-token-abc"},
            headers=platform_admin.headers,
        )
        assert miss.status_code == 200, miss.text
        assert all(item["id"] != finding_id for item in miss.json()["items"])

        kind_miss = e2e_client.get(
            "/api/agent-findings/search",
            params={"finding_kind": "opportunity", "q": "unique-token-xyz"},
            headers=platform_admin.headers,
        )
        assert kind_miss.status_code == 200, kind_miss.text
        assert all(item["id"] != finding_id for item in kind_miss.json()["items"])
    finally:
        await _cleanup_findings(db_session, [UUID(finding_id)])


def _ref(run: AgentRun) -> dict[str, str | None]:
    return {
        "run_id": str(run.id),
        "agent_id": str(run.agent_id) if run.agent_id else None,
        "org_id": str(run.org_id) if run.org_id else None,
        "root_run_id": str(run.root_run_id) if run.root_run_id else None,
        "parent_run_id": str(run.parent_run_id) if run.parent_run_id else None,
        "trigger_type": run.trigger_type,
    }


async def _seed_review_finding(
    e2e_client, platform_admin, org1_user, db_session: AsyncSession
) -> SimpleNamespace:
    agent_resp = e2e_client.post(
        "/api/agents",
        json={
            "name": f"Findings Parity {uuid4().hex[:8]}",
            "system_prompt": "Reply only with ok.",
            "channels": ["chat"],
            "access_level": "authenticated",
            "organization_id": str(org1_user.organization_id),
        },
        headers=platform_admin.headers,
    )
    assert agent_resp.status_code == 201, agent_resp.text
    agent_id = UUID(agent_resp.json()["id"])
    run = AgentRun(
        id=uuid4(),
        agent_id=agent_id,
        org_id=org1_user.organization_id,
        trigger_type="manual",
        status="completed",
        caller_user_id=str(org1_user.user_id),
        iterations_used=1,
        tokens_used=10,
        input={"message": "review me"},
        output={"text": "ok"},
        completed_at=datetime.now(timezone.utc),
    )
    review = AgentReviewDefinition(
        id=uuid4(),
        agent_id=agent_id,
        org_id=org1_user.organization_id,
        name="Parity review",
        latest_version=1,
        created_by=org1_user.user_id,
    )
    version = AgentReviewVersion(
        id=uuid4(),
        review_id=review.id,
        version=1,
        review_statement="Find problems.",
        created_by=org1_user.user_id,
    )
    refs = [_ref(run)]
    source_evidence = {"selected_runs": [{"run_id": str(run.id)}]}
    import json as _json

    input_bytes = len(
        _json.dumps(source_evidence, sort_keys=True, separators=(",", ":"), default=str).encode(
            "utf-8"
        )
    )
    review_run = AgentReviewRun(
        id=uuid4(),
        review_id=review.id,
        review_version_id=version.id,
        review_version=1,
        agent_id=agent_id,
        org_id=org1_user.organization_id,
        requested_by_user_id=org1_user.user_id,
        requested_run_ids=[run.id],
        selected_run_ids=[run.id],
        source_evidence=source_evidence,
        source_refs=refs,
        profile_snapshot={"provider": "test"},
        profile_fingerprint="profile-fingerprint",
        request_fingerprint="request-fingerprint",
        input_bytes=input_bytes,
    )
    finding = AgentFinding(
        id=uuid4(),
        agent_id=agent_id,
        org_id=org1_user.organization_id,
        status="open",
        description="Parity issue.",
        expected_behavior="Do the right thing.",
        source_kind="run",
        source_run_id=run.id,
        finding_kind="opportunity",
        evidence_markdown="## Proof\n- cited run\n",
        source_review_id=review.id,
        source_review_version_id=version.id,
        source_review_run_id=review_run.id,
        source_review_version=1,
        source_run_refs=refs,
        source_ordinal=0,
        created_by=org1_user.user_id,
    )
    db_session.add(run)
    await db_session.flush()
    db_session.add(review)
    await db_session.flush()
    db_session.add(version)
    await db_session.flush()
    db_session.add(review_run)
    await db_session.flush()
    db_session.add(finding)
    await db_session.commit()
    return SimpleNamespace(
        agent_id=agent_id,
        review_id=review.id,
        version_id=version.id,
        review_run_id=review_run.id,
        finding_id=finding.id,
        run_ids=[run.id],
    )


async def test_review_results_match_direct_finding_read(
    e2e_client, platform_admin, org1_user, db_session: AsyncSession
):
    """Review results return the same Markdown/provenance/linked cases."""
    fixture = await _seed_review_finding(
        e2e_client, platform_admin, org1_user, db_session
    )
    try:
        results = e2e_client.get(
            f"/api/agent-reviews/runs/{fixture.review_run_id}/results",
            headers=org1_user.headers,
        )
        assert results.status_code == 200, results.text
        findings = results.json()["findings"]
        assert len(findings) == 1
        via_results = findings[0]

        direct = e2e_client.get(
            f"/api/agent-findings/{fixture.finding_id}",
            headers=org1_user.headers,
        )
        assert direct.status_code == 200, direct.text
        via_direct = direct.json()

        for key in (
            "evidence_markdown",
            "finding_kind",
            "source_review_id",
            "source_review_version_id",
            "source_review_run_id",
            "source_review_version",
            "source_run_refs",
            "source_ordinal",
            "linked_case_ids",
        ):
            assert via_results[key] == via_direct[key], key
        assert via_results["evidence_markdown"] == "## Proof\n- cited run\n"

        by_review = e2e_client.get(
            "/api/agent-findings/search",
            params={"review_run_id": str(fixture.review_run_id)},
            headers=org1_user.headers,
        )
        assert by_review.status_code == 200, by_review.text
        assert [item["id"] for item in by_review.json()["items"]] == [
            str(fixture.finding_id)
        ]
    finally:
        await db_session.rollback()
        await db_session.execute(
            delete(AgentFinding).where(AgentFinding.id == fixture.finding_id)
        )
        await db_session.execute(
            delete(AgentReviewRun).where(AgentReviewRun.id == fixture.review_run_id)
        )
        await db_session.execute(
            delete(AgentReviewVersion).where(
                AgentReviewVersion.id == fixture.version_id
            )
        )
        await db_session.execute(
            delete(AgentReviewDefinition).where(
                AgentReviewDefinition.id == fixture.review_id
            )
        )
        await db_session.execute(delete(AgentRun).where(AgentRun.id.in_(fixture.run_ids)))
        await db_session.commit()
        e2e_client.delete(
            f"/api/agents/{fixture.agent_id}", headers=platform_admin.headers
        )
