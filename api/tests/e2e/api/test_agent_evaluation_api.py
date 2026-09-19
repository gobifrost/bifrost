"""Studio REST API coverage: suites, cases, candidates, drafts, executions.

Exercises the tenant-authorized CRUD/version surface, optimistic version
checks, published immutability, the ``202 PlatformJobAccepted`` + shared
``Location`` execution contract with active dedupe, pagination, and
role/tenant boundaries through HTTP (no mocks: the API container serves
the bind-mounted router).
"""

from __future__ import annotations

from uuid import uuid4

import pytest

pytestmark = pytest.mark.asyncio


def _agent_payload(name: str) -> dict:
    return {
        "name": name,
        "description": "Studio API test agent",
        "system_prompt": "Be helpful and brief.",
        "channels": ["chat"],
        "access_level": "authenticated",
        "organization_id": None,
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


def _designer_body() -> dict:
    return {
        "tool_schemas": {
            "get_ticket": {
                "type": "object",
                "properties": {"id": {"type": "string"}},
                "required": ["id"],
            }
        },
        "allowed_run_ids": [],
        "historical_runs": [],
        "designer_output": {
            "proposals": [
                {
                    "name": "vip-lookup",
                    "input": {"task": "look up ticket-0001"},
                    "fixture": {
                        "entities": {
                            "ticket": {"ticket-0001": {"id": "ticket-0001"}}
                        },
                        "allowed_tools": ["get_ticket"],
                        "rules": [],
                    },
                    "simulator_policy": {},
                    "assertions": [
                        {
                            "type": "tool_args",
                            "params": {
                                "tool": "get_ticket",
                                "args": {"id": "ticket-0001"},
                            },
                        }
                    ],
                    "expected_tools": ["get_ticket"],
                    "forbidden_tools": [],
                    "coverage": "success",
                }
            ]
        },
    }


async def test_studio_suite_case_candidate_flow(e2e_client, platform_admin):
    headers = platform_admin.headers
    agent = e2e_client.post(
        "/api/agents", json=_agent_payload(f"Studio Agent {uuid4().hex[:8]}"),
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

    updated = e2e_client.put(
        f"/api/agent-evaluations/suites/{suite_id}/cases/{case.json()['id']}",
        json={"enabled": False, "expected_version": 1},
        headers=headers,
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["enabled"] is False

    stale = e2e_client.put(
        f"/api/agent-evaluations/suites/{suite_id}/cases/{case.json()['id']}",
        json={"enabled": True, "expected_version": 999},
        headers=headers,
    )
    assert stale.status_code == 409

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


async def test_designer_drafts_require_acceptance(e2e_client, platform_admin):
    headers = platform_admin.headers
    suite = e2e_client.post(
        "/api/agent-evaluations/suites",
        json={"name": f"designer-{uuid4().hex[:8]}"},
        headers=headers,
    )
    assert suite.status_code == 200, suite.text
    suite_id = suite.json()["id"]

    drafts = e2e_client.post(
        f"/api/agent-evaluations/suites/{suite_id}/designer/drafts",
        json=_designer_body(),
        headers=headers,
    )
    assert drafts.status_code == 200, drafts.text
    draft = drafts.json()["drafts"][0]
    assert draft["accepted"] is False
    assert draft["enabled"] is False
    assert draft["provenance"] == "generated"

    malformed = e2e_client.post(
        f"/api/agent-evaluations/suites/{suite_id}/designer/drafts",
        json={**_designer_body(), "designer_output": {"proposals": []}},
        headers=headers,
    )
    assert malformed.status_code == 422

    accepted = e2e_client.post(
        f"/api/agent-evaluations/suites/{suite_id}/cases/accept",
        json={"draft_id": draft["id"]},
        headers=headers,
    )
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["accepted"] is True

    double_accept = e2e_client.post(
        f"/api/agent-evaluations/suites/{suite_id}/cases/accept",
        json={"draft_id": draft["id"]},
        headers=headers,
    )
    assert double_accept.status_code == 409


async def test_execution_returns_202_with_location_and_dedupe(
    e2e_client, platform_admin
):
    headers = platform_admin.headers
    agent = e2e_client.post(
        "/api/agents", json=_agent_payload(f"Exec Agent {uuid4().hex[:8]}"),
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


async def test_execution_results_paginate_and_link_debugger(
    e2e_client, platform_admin
):
    headers = platform_admin.headers
    agent = e2e_client.post(
        "/api/agents", json=_agent_payload(f"Links Agent {uuid4().hex[:8]}"),
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


async def test_tenant_boundaries(e2e_client, platform_admin, org2_user, alice_user):
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
