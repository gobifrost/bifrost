"""Saved multi-profile matrix: admission fan-out, pairing, cancel, tenant scope.

Admission shape only (no terminal wait — the test stack has no LLM
provider): cells are atomic single-path executions under one matrix, both
sides frozen under the selected profile, cancelled through the shared path.
"""
from __future__ import annotations

import logging
from typing import AsyncGenerator
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.orm.agent_evaluations import (
    AgentEvaluationExecution,
    AgentEvaluationMatrix,
)

logger = logging.getLogger(__name__)

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def matrix_agent(e2e_client, platform_admin, org1) -> AsyncGenerator[dict, None]:
    resp = e2e_client.post(
        "/api/agents",
        json={
            "name": f"Matrix Agent {uuid4().hex[:8]}",
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
async def matrix_profile(
    e2e_client, platform_admin, llm_config_cleanup
) -> AsyncGenerator[UUID, None]:
    """Selectable model profile via the supported API path.

    Seeds TWO profiles with distinct models/endpoints: profile A owns the
    ``primary`` assignment (what the baseline snapshot resolves alone),
    profile B is the matrix selection. Cells must freeze B's full runtime
    config, not A's model under B's credentials. Cells are cancelled right
    after admission, so no model call is ever attempted.
    """
    suffix = uuid4().hex[:8]
    connection = e2e_client.post(
        "/api/admin/ai/connections",
        json={
            "name": f"Matrix Conn A {suffix}",
            "provider": "openai",
            "api_key": "test-only",
            "endpoint": "https://primary-a.example/v1",
        },
        headers=platform_admin.headers,
    )
    assert connection.status_code == 201, connection.text
    connection_a_id = connection.json()["id"]
    profile_a = e2e_client.post(
        "/api/admin/ai/profiles",
        json={
            "name": f"Matrix Profile A {suffix}",
            "connection_id": connection_a_id,
            "model": "model-a-original",
            "enabled_for_chat": True,
        },
        headers=platform_admin.headers,
    )
    assert profile_a.status_code == 201, profile_a.text
    assignment = e2e_client.put(
        "/api/admin/ai/assignments/primary",
        json={"profile_id": profile_a.json()["id"]},
        headers=platform_admin.headers,
    )
    assert assignment.status_code == 200, assignment.text
    connection_b = e2e_client.post(
        "/api/admin/ai/connections",
        json={
            "name": f"Matrix Conn B {suffix}",
            "provider": "openai",
            "api_key": "test-only",
            "endpoint": "https://selected-b.example/v1",
        },
        headers=platform_admin.headers,
    )
    assert connection_b.status_code == 201, connection_b.text
    connection_b_id = connection_b.json()["id"]
    profile_b = e2e_client.post(
        "/api/admin/ai/profiles",
        json={
            "name": f"Matrix Profile B {suffix}",
            "connection_id": connection_b_id,
            "model": "model-b-selected",
            "enabled_for_chat": True,
        },
        headers=platform_admin.headers,
    )
    assert profile_b.status_code == 201, profile_b.text
    profile_b_id = profile_b.json()["id"]
    yield UUID(profile_b_id)
    e2e_client.delete(
        "/api/admin/ai/assignments/primary", headers=platform_admin.headers
    )
    e2e_client.delete(
        f"/api/admin/ai/profiles/{profile_b_id}",
        headers=platform_admin.headers,
    )
    e2e_client.delete(
        f"/api/admin/ai/profiles/{profile_a.json()['id']}",
        headers=platform_admin.headers,
    )
    e2e_client.delete(
        f"/api/admin/ai/connections/{connection_b_id}",
        headers=platform_admin.headers,
    )
    e2e_client.delete(
        f"/api/admin/ai/connections/{connection_a_id}",
        headers=platform_admin.headers,
    )


@pytest_asyncio.fixture
async def published_suite(
    e2e_client, platform_admin, org1, matrix_agent, matrix_profile, db_session: AsyncSession
):
    """Published suite with one accepted case + one candidate; DB-cleaned.

    Takes ``matrix_profile`` so the primary model assignment exists before
    candidate creation freezes the baseline snapshot.
    """
    suite_resp = e2e_client.post(
        "/api/agent-evaluations/suites",
        json={
            "name": f"Matrix Suite {uuid4().hex[:8]}",
            "agent_id": matrix_agent["id"],
            "organization_id": org1["id"],
        },
        headers=platform_admin.headers,
    )
    assert suite_resp.status_code == 200, suite_resp.text
    suite = suite_resp.json()
    case_resp = e2e_client.post(
        f"/api/agent-evaluations/suites/{suite['id']}/cases",
        json={"name": "matrix-case"},
        headers=platform_admin.headers,
    )
    assert case_resp.status_code == 200, case_resp.text
    pub_resp = e2e_client.post(
        f"/api/agent-evaluations/suites/{suite['id']}/publish",
        headers=platform_admin.headers,
    )
    assert pub_resp.status_code == 200, pub_resp.text
    candidate_resp = e2e_client.post(
        "/api/agent-evaluations/candidates",
        json={
            "base_agent_id": matrix_agent["id"],
            "name": "matrix-candidate",
            "organization_id": org1["id"],
        },
        headers=platform_admin.headers,
    )
    assert candidate_resp.status_code == 200, candidate_resp.text
    yield {"suite": pub_resp.json(), "candidate": candidate_resp.json()}
    from src.models.orm.agent_evaluations import (
        AgentCandidateSnapshot,
        AgentEvaluationCase,
        AgentEvaluationSuite,
    )

    await db_session.execute(
        delete(AgentEvaluationCase).where(
            AgentEvaluationCase.suite_id == UUID(suite["id"])
        )
    )
    await db_session.execute(
        delete(AgentEvaluationSuite).where(
            AgentEvaluationSuite.id == UUID(suite["id"])
        )
    )
    await db_session.execute(
        delete(AgentCandidateSnapshot).where(
            AgentCandidateSnapshot.id == UUID(candidate_resp.json()["id"])
        )
    )
    await db_session.commit()


async def _cleanup_matrix(db_session: AsyncSession, matrix_id: UUID) -> None:
    """Delete a matrix and exactly its member executions (shared or owned)."""
    matrix = await db_session.get(AgentEvaluationMatrix, matrix_id)
    member_ids = (
        [UUID(value) for value in (matrix.cell_execution_ids or [])]
        if matrix is not None
        else []
    )
    if member_ids:
        await db_session.execute(
            delete(AgentEvaluationExecution).where(
                AgentEvaluationExecution.id.in_(member_ids)
            )
        )
    await db_session.execute(
        delete(AgentEvaluationMatrix).where(AgentEvaluationMatrix.id == matrix_id)
    )
    await db_session.commit()


async def test_batch_admits_paired_cells_under_one_profile(
    e2e_client, platform_admin, published_suite, matrix_profile, db_session: AsyncSession
):
    suite = published_suite["suite"]
    candidate = published_suite["candidate"]
    resp = e2e_client.post(
        "/api/agent-evaluations/executions/batch",
        json={
            "suite_id": suite["id"],
            "candidate_ids": [candidate["id"]],
            "profile_ids": [str(matrix_profile)],
        },
        headers=platform_admin.headers,
    )
    assert resp.status_code == 202, resp.text
    body = resp.json()
    cancel_resp = e2e_client.post(
        f"/api/agent-evaluations/executions/batch/{body['matrix']['id']}/cancel",
        headers=platform_admin.headers,
    )
    assert cancel_resp.status_code == 200, cancel_resp.text
    try:
        assert body["planned_cells"] == 2
        # Baseline-only cell runs 1 side; candidate cell runs 2 sides.
        assert body["planned_runs"] == 3
        assert body["cost_estimate_usd"] is None
        by_candidate = {c["candidate_id"]: c for c in body["cells"]}
        assert set(by_candidate) == {None, candidate["id"]}
        for cell in body["cells"]:
            assert cell["profile_id"] == str(matrix_profile)
            assert cell["reused"] is False

        executions = (
            await db_session.execute(
                select(AgentEvaluationExecution).where(
                    AgentEvaluationExecution.matrix_id == UUID(body["matrix"]["id"])
                )
            )
        ).scalars().all()
        assert len(executions) == 2
        for execution in executions:
            assert execution.profile_id == matrix_profile
            assert execution.suite_version == suite["version"]
            for snapshot in (
                execution.baseline_snapshot,
                execution.candidate_snapshot or {},
            ):
                if snapshot:
                    # Selected profile B's full runtime config is frozen on
                    # both sides — never profile A's model under B's
                    # credentials, and never secret material.
                    model = snapshot.get("model", {})
                    assert model.get("profile_id") == str(matrix_profile)
                    assert model.get("model") == "model-b-selected"
                    assert (
                        model.get("endpoint")
                        == "https://selected-b.example/v1"
                    )
                    assert "api_key" not in model
    finally:
        await _cleanup_matrix(db_session, UUID(body["matrix"]["id"]))


async def test_batch_requires_existing_profile(
    e2e_client, platform_admin, published_suite
):
    resp = e2e_client.post(
        "/api/agent-evaluations/executions/batch",
        json={
            "suite_id": published_suite["suite"]["id"],
            "candidate_ids": [],
            "profile_ids": [str(uuid4())],
        },
        headers=platform_admin.headers,
    )
    assert resp.status_code == 404, resp.text


async def test_batch_rejects_foreign_candidate(
    e2e_client, platform_admin, org2, published_suite, matrix_profile, matrix_agent
):
    other = e2e_client.post(
        "/api/agents",
        json={
            "name": f"Foreign Matrix Agent {uuid4().hex[:8]}",
            "system_prompt": "Reply only with ok.",
            "channels": ["chat"],
            "access_level": "authenticated",
            "organization_id": org2["id"],
        },
        headers=platform_admin.headers,
    )
    assert other.status_code == 201, other.text
    foreign_candidate = e2e_client.post(
        "/api/agent-evaluations/candidates",
        json={
            "base_agent_id": other.json()["id"],
            "name": "foreign",
            "organization_id": org2["id"],
        },
        headers=platform_admin.headers,
    )
    assert foreign_candidate.status_code == 200, foreign_candidate.text
    try:
        resp = e2e_client.post(
            "/api/agent-evaluations/executions/batch",
            json={
                "suite_id": published_suite["suite"]["id"],
                "candidate_ids": [foreign_candidate.json()["id"]],
                "profile_ids": [str(matrix_profile)],
            },
            headers=platform_admin.headers,
        )
        assert resp.status_code == 422, resp.text
    finally:
        e2e_client.delete(
            f"/api/agents/{other.json()['id']}", headers=platform_admin.headers
        )


async def test_matrix_cancel_and_cross_org_scope(
    e2e_client,
    platform_admin,
    org2_user,
    published_suite,
    matrix_profile,
    db_session: AsyncSession,
):
    suite = published_suite["suite"]
    resp = e2e_client.post(
        "/api/agent-evaluations/executions/batch",
        json={"suite_id": suite["id"], "profile_ids": [str(matrix_profile)]},
        headers=platform_admin.headers,
    )
    assert resp.status_code == 202, resp.text
    body = resp.json()
    matrix_id = body["matrix"]["id"]
    try:
        assert body["planned_cells"] == 1
        assert body["cells"][0]["candidate_id"] is None

        other_read = e2e_client.get(
            f"/api/agent-evaluations/executions/batch/{matrix_id}",
            headers=org2_user.headers,
        )
        assert other_read.status_code == 404, other_read.text

        cancelled = e2e_client.post(
            f"/api/agent-evaluations/executions/batch/{matrix_id}/cancel",
            headers=platform_admin.headers,
        )
        assert cancelled.status_code == 200, cancelled.text
        statuses = {c["status"] for c in cancelled.json()["cells"]}
        assert statuses <= {"cancelled", "succeeded", "failed"}
    finally:
        await _cleanup_matrix(db_session, UUID(matrix_id))


async def test_single_execution_path_unchanged(
    e2e_client, platform_admin, published_suite, db_session: AsyncSession
):
    """The refactored single path still admits baseline-only executions."""
    suite = published_suite["suite"]
    resp = e2e_client.post(
        "/api/agent-evaluations/executions",
        json={"suite_id": suite["id"]},
        headers=platform_admin.headers,
    )
    assert resp.status_code == 202, resp.text
    execution_id = resp.headers["X-Evaluation-Execution-Id"]
    try:
        execution = await db_session.get(
            AgentEvaluationExecution, UUID(execution_id)
        )
        assert execution is not None
        assert execution.matrix_id is None
        assert execution.profile_id is None
        cancelled = e2e_client.post(
            f"/api/agent-evaluations/executions/{execution_id}/cancel",
            headers=platform_admin.headers,
        )
        assert cancelled.status_code == 200, cancelled.text
    finally:
        await db_session.execute(
            delete(AgentEvaluationExecution).where(
                AgentEvaluationExecution.id == UUID(execution_id)
            )
        )
        await db_session.commit()


def _admit_batch(e2e_client, platform_admin, suite_id, candidate_ids, profile_ids, repetitions=None):
    body = {
        "suite_id": suite_id,
        "candidate_ids": candidate_ids,
        "profile_ids": profile_ids,
    }
    if repetitions is not None:
        body["repetitions_override"] = repetitions
    return e2e_client.post(
        "/api/agent-evaluations/executions/batch",
        json=body,
        headers=platform_admin.headers,
    )


async def test_repeat_active_batch_reuses_visible_cells(
    e2e_client, platform_admin, published_suite, matrix_profile, db_session: AsyncSession
):
    """Repeating a batch reuses cells; the new matrix sees and cancels them."""
    suite = published_suite["suite"]
    candidate_id = published_suite["candidate"]["id"]
    profile = str(matrix_profile)
    first = _admit_batch(
        e2e_client, platform_admin, suite["id"], [candidate_id], [profile]
    )
    assert first.status_code == 202, first.text
    first_ids = sorted(c["execution_id"] for c in first.json()["cells"])
    assert all(c["reused"] is False for c in first.json()["cells"]
               ), first.json()
    second = _admit_batch(
        e2e_client, platform_admin, suite["id"], [candidate_id], [profile]
    )
    assert second.status_code == 202, second.text
    second_body = second.json()
    try:
        # Same cells, explicitly reused — and the second matrix owns them.
        assert sorted(c["execution_id"] for c in second_body["cells"]) == first_ids
        assert all(c["reused"] is True for c in second_body["cells"])
        reloaded = e2e_client.get(
            f"/api/agent-evaluations/executions/batch/{second_body['matrix']['id']}",
            headers=platform_admin.headers,
        )
        assert reloaded.status_code == 200, reloaded.text
        assert sorted(c["execution_id"] for c in reloaded.json()["cells"]) == first_ids
        cancelled = e2e_client.post(
            f"/api/agent-evaluations/executions/batch/{second_body['matrix']['id']}/cancel",
            headers=platform_admin.headers,
        )
        assert cancelled.status_code == 200, cancelled.text
        statuses = {c["status"] for c in cancelled.json()["cells"]}
        assert statuses <= {"cancelled", "succeeded", "failed"}
        assert len(cancelled.json()["cells"]) == 2
    finally:
        await _cleanup_matrix(db_session, UUID(first.json()["matrix"]["id"]))
        await _cleanup_matrix(db_session, UUID(second_body["matrix"]["id"]))


async def test_partial_overlap_reuses_matching_cells_only(
    e2e_client, platform_admin, published_suite, matrix_profile, matrix_agent, org1,
    db_session: AsyncSession,
):
    """A wider batch reuses matching cells and admits only the rest."""
    suite = published_suite["suite"]
    candidate_id = published_suite["candidate"]["id"]
    profile = str(matrix_profile)
    other_candidate = e2e_client.post(
        "/api/agent-evaluations/candidates",
        json={
            "base_agent_id": matrix_agent["id"],
            "name": "overlap-candidate",
            "organization_id": org1["id"],
        },
        headers=platform_admin.headers,
    )
    assert other_candidate.status_code == 200, other_candidate.text
    other_id = other_candidate.json()["id"]
    narrow = _admit_batch(
        e2e_client, platform_admin, suite["id"], [candidate_id], [profile]
    )
    assert narrow.status_code == 202, narrow.text
    wide = _admit_batch(
        e2e_client, platform_admin, suite["id"], [candidate_id, other_id], [profile]
    )
    assert wide.status_code == 202, wide.text
    wide_body = wide.json()
    try:
        assert wide_body["planned_cells"] == 3
        by_candidate = {c["candidate_id"]: c for c in wide_body["cells"]}
        assert by_candidate[None]["reused"] is True
        assert by_candidate[candidate_id]["reused"] is True
        assert by_candidate[other_id]["reused"] is False
    finally:
        await _cleanup_matrix(db_session, UUID(narrow.json()["matrix"]["id"]))
        await _cleanup_matrix(db_session, UUID(wide_body["matrix"]["id"]))
        from src.models.orm.agent_evaluations import AgentCandidateSnapshot

        await db_session.execute(
            delete(AgentCandidateSnapshot).where(
                AgentCandidateSnapshot.id == UUID(other_id)
            )
        )
        await db_session.commit()


async def test_changed_repetitions_admit_distinct_cells(
    e2e_client, platform_admin, published_suite, matrix_profile, db_session: AsyncSession
):
    """Repetitions participate in cell identity: reps=2 never reuses reps=1."""
    suite = published_suite["suite"]
    profile = str(matrix_profile)
    first = _admit_batch(
        e2e_client, platform_admin, suite["id"], [], [profile], repetitions=1
    )
    assert first.status_code == 202, first.text
    second = _admit_batch(
        e2e_client, platform_admin, suite["id"], [], [profile], repetitions=2
    )
    assert second.status_code == 202, second.text
    second_body = second.json()
    try:
        assert second_body["planned_cells"] == 1
        assert second_body["planned_runs"] == 2
        assert all(c["reused"] is False for c in second_body["cells"])
        assert (
            second_body["cells"][0]["execution_id"]
            != first.json()["cells"][0]["execution_id"]
        )
    finally:
        await _cleanup_matrix(db_session, UUID(first.json()["matrix"]["id"]))
        await _cleanup_matrix(db_session, UUID(second_body["matrix"]["id"]))


async def test_quota_counts_new_work_not_reused_cells(
    e2e_client, platform_admin, published_suite, matrix_profile, db_session: AsyncSession
):
    """A full org still admits fully-reused batches but rejects new cells."""
    suite = published_suite["suite"]
    candidate_id = published_suite["candidate"]["id"]
    profile = str(matrix_profile)
    base = _admit_batch(
        e2e_client, platform_admin, suite["id"], [candidate_id], [profile]
    )
    assert base.status_code == 202, base.text
    single_ids = []
    try:
        # Fill the org to the 5-active limit: 2 batch cells + 3 singles with
        # distinct repetition identities.
        for reps in (1, 2, 3):
            single = e2e_client.post(
                "/api/agent-evaluations/executions",
                json={"suite_id": suite["id"], "repetitions_override": reps},
                headers=platform_admin.headers,
            )
            assert single.status_code == 202, single.text
            single_ids.append(single.headers["X-Evaluation-Execution-Id"])
        # Fully reused batch still admits despite the full quota.
        repeat = _admit_batch(
            e2e_client, platform_admin, suite["id"], [candidate_id], [profile]
        )
        assert repeat.status_code == 202, repeat.text
        assert all(c["reused"] is True for c in repeat.json()["cells"])
        # New work is rejected.
        fresh = _admit_batch(
            e2e_client, platform_admin, suite["id"], [], [profile], repetitions=2
        )
        assert fresh.status_code == 429, fresh.text
        await _cleanup_matrix(db_session, UUID(repeat.json()["matrix"]["id"]))
    finally:
        await _cleanup_matrix(db_session, UUID(base.json()["matrix"]["id"]))
        for execution_id in single_ids:
            e2e_client.post(
                f"/api/agent-evaluations/executions/{execution_id}/cancel",
                headers=platform_admin.headers,
            )
            await db_session.execute(
                delete(AgentEvaluationExecution).where(
                    AgentEvaluationExecution.id == UUID(execution_id)
                )
            )
        await db_session.commit()


async def _chat_disabled_profile(e2e_client, platform_admin):
    """Profile the tenant caller may not select (gate 403, admin bypasses)."""
    suffix = uuid4().hex[:8]
    connection = e2e_client.post(
        "/api/admin/ai/connections",
        json={
            "name": f"Gated Conn {suffix}",
            "provider": "openai",
            "api_key": "test-only",
            "endpoint": "https://gated.example/v1",
        },
        headers=platform_admin.headers,
    )
    assert connection.status_code == 201, connection.text
    connection_id = connection.json()["id"]
    profile = e2e_client.post(
        "/api/admin/ai/profiles",
        json={
            "name": f"Gated Profile {suffix}",
            "connection_id": connection_id,
            "model": "model-gated",
            "enabled_for_chat": False,
        },
        headers=platform_admin.headers,
    )
    assert profile.status_code == 201, profile.text
    return connection_id, profile.json()["id"]


async def _delete_profile(e2e_client, platform_admin, connection_id, profile_id):
    e2e_client.delete(
        f"/api/admin/ai/profiles/{profile_id}", headers=platform_admin.headers
    )
    e2e_client.delete(
        f"/api/admin/ai/connections/{connection_id}",
        headers=platform_admin.headers,
    )


async def test_reuse_enforces_identical_authorization_for_tenant(
    e2e_client, platform_admin, org1_user, published_suite, matrix_profile,
    db_session: AsyncSession,
):
    """A tenant cannot reuse (or see into) an admin cell it may not admit."""
    suite = published_suite["suite"]
    connection_id, gated_id = await _chat_disabled_profile(
        e2e_client, platform_admin
    )
    try:
        admin_batch = e2e_client.post(
            "/api/agent-evaluations/executions/batch",
            json={"suite_id": suite["id"], "profile_ids": [gated_id]},
            headers=platform_admin.headers,
        )
        assert admin_batch.status_code == 202, admin_batch.text
        before = (
            await db_session.execute(
                select(func.count()).select_from(AgentEvaluationExecution)
            )
        ).scalar() or 0

        denied = e2e_client.post(
            "/api/agent-evaluations/executions/batch",
            json={"suite_id": suite["id"], "profile_ids": [gated_id]},
            headers=org1_user.headers,
        )
        assert denied.status_code == 403, denied.text

        after = (
            await db_session.execute(
                select(func.count()).select_from(AgentEvaluationExecution)
            )
        ).scalar() or 0
        assert after == before
        await _cleanup_matrix(
            db_session, UUID(admin_batch.json()["matrix"]["id"])
        )
    finally:
        await _delete_profile(e2e_client, platform_admin, connection_id, gated_id)


async def test_reuse_blocked_when_baseline_access_revoked(
    e2e_client, platform_admin, org1_user, published_suite, matrix_profile,
    matrix_agent, db_session: AsyncSession,
):
    """Pausing the baseline agent turns a repeat batch into 422, not reuse."""
    suite = published_suite["suite"]
    profile = str(matrix_profile)
    first = e2e_client.post(
        "/api/agent-evaluations/executions/batch",
        json={"suite_id": suite["id"], "profile_ids": [profile]},
        headers=platform_admin.headers,
    )
    assert first.status_code == 202, first.text
    paused = e2e_client.put(
        f"/api/agents/{matrix_agent['id']}",
        json={"is_active": False},
        headers=platform_admin.headers,
    )
    assert paused.status_code == 200, paused.text
    try:
        repeat = e2e_client.post(
            "/api/agent-evaluations/executions/batch",
            json={"suite_id": suite["id"], "profile_ids": [profile]},
            headers=org1_user.headers,
        )
        assert repeat.status_code == 422, repeat.text
    finally:
        e2e_client.put(
            f"/api/agents/{matrix_agent['id']}",
            json={"is_active": True},
            headers=platform_admin.headers,
        )
        await _cleanup_matrix(db_session, UUID(first.json()["matrix"]["id"]))


async def test_mixed_overlap_maps_reused_flags_by_execution(
    e2e_client, platform_admin, published_suite, matrix_profile,
    db_session: AsyncSession,
):
    """Two profiles interleave sorted cells; reused flags must follow ids."""
    suite = published_suite["suite"]
    candidate_id = published_suite["candidate"]["id"]
    profile_a = str(matrix_profile)
    connection_id, profile_b = await _chat_disabled_profile(
        e2e_client, platform_admin
    )
    # Re-enable chat so the second profile is selectable by anyone.
    enabled = e2e_client.patch(
        f"/api/admin/ai/profiles/{profile_b}",
        json={"enabled_for_chat": True},
        headers=platform_admin.headers,
    )
    assert enabled.status_code == 200, enabled.text
    try:
        narrow = e2e_client.post(
            "/api/agent-evaluations/executions/batch",
            json={
                "suite_id": suite["id"],
                "candidate_ids": [candidate_id],
                "profile_ids": [profile_a],
            },
            headers=platform_admin.headers,
        )
        assert narrow.status_code == 202, narrow.text
        wide = e2e_client.post(
            "/api/agent-evaluations/executions/batch",
            json={
                "suite_id": suite["id"],
                "candidate_ids": [candidate_id],
                "profile_ids": [profile_a, profile_b],
            },
            headers=platform_admin.headers,
        )
        assert wide.status_code == 202, wide.text
        wide_body = wide.json()
        try:
            assert wide_body["planned_cells"] == 4
            by_key = {
                (c["candidate_id"], c["profile_id"]): c["reused"]
                for c in wide_body["cells"]
            }
            assert by_key[(None, profile_a)] is True
            assert by_key[(candidate_id, profile_a)] is True
            assert by_key[(None, profile_b)] is False
            assert by_key[(candidate_id, profile_b)] is False
        finally:
            await _cleanup_matrix(db_session, UUID(wide_body["matrix"]["id"]))
        await _cleanup_matrix(db_session, UUID(narrow.json()["matrix"]["id"]))
    finally:
        await _delete_profile(e2e_client, platform_admin, connection_id, profile_b)
