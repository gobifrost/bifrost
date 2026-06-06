"""End-to-end: solution_id is populated on entity API responses (badge linking).

Task 9 fix: the response DTOs declare `solution_id`, but the routers build them
via explicit kwargs / a dict-returning validator, so `from_attributes` never
reads it. This exercises the REAL construction path (deploy a solution-managed
workflow + agent, then GET them) and asserts `solution_id` == the remapped
install entity scope, not just that the field exists on the model.
"""
from __future__ import annotations

import uuid
from uuid import UUID

import pytest

from src.services.solutions.deploy import solution_entity_id

pytestmark = pytest.mark.e2e


def _create_solution(e2e_client, headers, slug: str) -> str:
    r = e2e_client.post("/api/solutions", headers=headers, json={
        "slug": slug, "name": slug.upper(), "scope": "global",
    })
    assert r.status_code in (200, 201), r.text
    return r.json()["id"]


def test_solution_id_populated_on_workflow_and_agent(e2e_client, platform_admin):
    headers = platform_admin.headers
    slug = f"solid-{uuid.uuid4().hex[:8]}"
    sid = _create_solution(e2e_client, headers, slug)

    wf_id = str(uuid.uuid4())
    agent_id = str(uuid.uuid4())
    dep = e2e_client.post(f"/api/solutions/{sid}/deploy", headers=headers, json={
        "python_files": {"workflows/w.py": "from bifrost import workflow\n@workflow\nasync def w():\n    return {}\n"},
        "workflows": [{
            "id": wf_id, "name": f"w_{slug}", "function_name": "w",
            "path": "workflows/w.py", "type": "workflow",
        }],
        "agents": [{
            "id": agent_id, "name": f"a_{slug}",
            "system_prompt": "You are a helper.", "channels": ["chat"],
        }],
    })
    assert dep.status_code in (200, 201), dep.text

    # Deploy remaps each manifest id to uuid5(install_id, manifest_id).
    real_wf_id = solution_entity_id(UUID(sid), UUID(wf_id))
    real_agent_id = solution_entity_id(UUID(sid), UUID(agent_id))

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
