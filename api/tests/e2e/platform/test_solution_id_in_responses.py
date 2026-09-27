"""End-to-end: solution_id is populated on entity API responses (badge linking).

Task 9 fix: the response DTOs declare `solution_id`, but the routers build them
via explicit kwargs / a dict-returning validator, so `from_attributes` never
reads it. Seed owned rows and exercise the real GET/list construction paths.
"""
from __future__ import annotations

import uuid
from uuid import UUID

import pytest

from src.models.orm.agents import Agent
from src.models.orm.workflows import Workflow
from src.services.solutions.deploy import solution_entity_id

pytestmark = pytest.mark.e2e


def _create_solution(e2e_client, headers, slug: str) -> str:
    r = e2e_client.post("/api/solutions", headers=headers, json={
        "slug": slug, "name": slug.upper(), "organization_id": None,
    })
    assert r.status_code in (200, 201), r.text
    return r.json()["id"]


async def test_solution_id_populated_on_workflow_and_agent(
    e2e_client, platform_admin, db_session,
):
    headers = platform_admin.headers
    slug = f"solid-{uuid.uuid4().hex[:8]}"
    sid = _create_solution(e2e_client, headers, slug)

    real_wf_id = solution_entity_id(UUID(sid), uuid.uuid4())
    real_agent_id = solution_entity_id(UUID(sid), uuid.uuid4())
    db_session.add_all([
        Workflow(
            id=real_wf_id,
            name=f"w_{slug}",
            function_name="w",
            path="workflows/w.py",
            solution_id=UUID(sid),
        ),
        Agent(
            id=real_agent_id,
            name=f"a_{slug}",
            system_prompt="You are a helper.",
            created_by=platform_admin.email,
            solution_id=UUID(sid),
        ),
    ])
    await db_session.commit()

    # Workflow goes through _convert_workflow_orm_to_schema (list endpoint).
    wf_list = e2e_client.get("/api/workflows", headers=headers)
    assert wf_list.status_code == 200, wf_list.text
    wf = next((w for w in wf_list.json() if w["id"] == str(real_wf_id)), None)
    assert wf is not None, f"deployed workflow {real_wf_id} not in list"
    assert wf["solution_id"] == str(sid), wf
    assert wf["is_solution_managed"] is True

    # Agent goes through _agent_to_public (GET-by-id endpoint).
    ag = e2e_client.get(f"/api/agents/{real_agent_id}", headers=headers)
    assert ag.status_code == 200, ag.text
    body = ag.json()
    assert body["solution_id"] == str(sid), body
    assert body["is_solution_managed"] is True
