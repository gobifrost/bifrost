"""Mutation guards for persisted Agent Evaluation Studio drafts."""

from uuid import UUID, uuid4

import pytest
from sqlalchemy import delete

from src.models.orm.agent_evaluations import AgentEvaluationCase, AgentEvaluationSuite

pytestmark = pytest.mark.asyncio


@pytest.fixture
async def owned_suites(db_session):
    ids = []
    yield ids
    await db_session.rollback()
    if ids:
        await db_session.execute(delete(AgentEvaluationSuite).where(AgentEvaluationSuite.id.in_(ids)))
        await db_session.commit()


async def _draft_suite(e2e_client, headers, owned_suites) -> str:
    response = e2e_client.post(
        "/api/agent-evaluations/suites",
        headers=headers,
        json={"name": f"draft-mutation-{uuid4().hex}"},
    )
    assert response.status_code == 200, response.text
    owned_suites.append(UUID(response.json()["id"]))
    return response.json()["id"]


def _draft(suite_id: str, name: str, **overrides) -> AgentEvaluationCase:
    values = {
        "suite_id": UUID(suite_id),
        "name": name,
        "position": 0,
        "input": {"task": "check draft"},
        "fixture": {"entities": {}, "allowed_tools": [], "rules": []},
        "assertions": [{"type": "no_real_tools", "params": {}}],
        "provenance": "generated",
        "provenance_run_ids": [],
        "tags": ["edge"],
        "accepted": False,
    }
    values.update(overrides)
    return AgentEvaluationCase(**values)


async def test_suite_update_rejects_a_stale_draft_version(e2e_client, platform_admin, owned_suites):
    suite_id = await _draft_suite(e2e_client, platform_admin.headers, owned_suites)

    updated = e2e_client.put(
        f"/api/agent-evaluations/suites/{suite_id}",
        headers=platform_admin.headers,
        json={"description": "first edit", "expected_version": 1},
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["version"] == 2

    stale = e2e_client.put(
        f"/api/agent-evaluations/suites/{suite_id}",
        headers=platform_admin.headers,
        json={"description": "stale edit", "expected_version": 1},
    )
    assert stale.status_code == 409, stale.text


async def test_accept_case_revalidates_persisted_drafts(
    e2e_client, platform_admin, db_session, owned_suites
):
    suite_id = await _draft_suite(e2e_client, platform_admin.headers, owned_suites)
    invalid_fixture = _draft(suite_id, "invalid-fixture", fixture={"version": 2})
    invalid_assertion = _draft(
        suite_id,
        "invalid-assertion",
        assertions=[{"type": "not-a-real-assertion", "params": {}}],
    )
    db_session.add_all([invalid_fixture, invalid_assertion])
    await db_session.commit()

    for draft in (invalid_fixture, invalid_assertion):
        response = e2e_client.post(
            f"/api/agent-evaluations/suites/{suite_id}/cases/accept",
            headers=platform_admin.headers,
            json={"draft_id": str(draft.id)},
        )
        assert response.status_code == 422, response.text


async def test_accept_case_preserves_persisted_draft_provenance(
    e2e_client, platform_admin, db_session, owned_suites
):
    suite_id = await _draft_suite(e2e_client, platform_admin.headers, owned_suites)
    provenance_run_id = uuid4()
    draft = _draft(
        suite_id,
        "historical-draft",
        provenance="historical_inspiration",
        provenance_run_ids=[str(provenance_run_id)],
    )
    db_session.add(draft)
    await db_session.commit()

    accepted = e2e_client.post(
        f"/api/agent-evaluations/suites/{suite_id}/cases/accept",
        headers=platform_admin.headers,
        json={"draft_id": str(draft.id)},
    )
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["provenance"] == "historical_inspiration"
    assert accepted.json()["provenance_run_ids"] == [str(provenance_run_id)]
