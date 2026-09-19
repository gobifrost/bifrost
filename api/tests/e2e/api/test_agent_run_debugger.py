"""Debugger read surface: tree, timeline, snapshot, checkpoints."""

from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

import pytest
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.orm.agent_runs import (
    AgentRun,
    AgentRunCheckpoint,
    AgentRunJournalEntry,
)


pytestmark = pytest.mark.asyncio


def _create_agent(e2e_client, platform_admin, name: str) -> dict:
    response = e2e_client.post(
        "/api/agents",
        json={
            "name": name,
            "description": "Debugger surface test agent",
            "system_prompt": "Test debugger reads.",
            "channels": [],
            "access_level": "authenticated",
        },
        headers=platform_admin.headers,
    )
    assert response.status_code == 201, response.text
    return response.json()


async def _seed_tree(e2e_client, platform_admin, db_session) -> dict:
    """Root (waiting_child) + failed child + cancelled child + journal."""
    root_agent = _create_agent(e2e_client, platform_admin, f"Root {uuid4().hex[:8]}")
    child_agent = _create_agent(
        e2e_client, platform_admin, f"Child {uuid4().hex[:8]}"
    )
    root_id, failed_id, cancelled_id = uuid4(), uuid4(), uuid4()
    started = datetime(2026, 9, 18, 12, 0, tzinfo=timezone.utc)
    db_session.add(
        AgentRun(
            id=root_id,
            agent_id=UUID(root_agent["id"]),
            trigger_type="api",
            status="waiting_child",
            root_run_id=root_id,
            execution_snapshot={
                "format_version": 1,
                "system_prompt": "root prompt",
                "model": {"provider": "anthropic", "model": "m"},
                "tools": [{"name": "search"}],
                "delegated_agents": [],
                "system_tools": [],
                "limits": {"max_iterations": 5},
            },
            caller_context={"ticket_id": "T-1"},
            correlation={"ticket_id": "T-1"},
            created_at=started,
        )
    )
    db_session.add(
        AgentRun(
            id=failed_id,
            agent_id=UUID(child_agent["id"]),
            parent_run_id=root_id,
            root_run_id=root_id,
            trigger_type="delegation",
            status="failed",
            error="boom",
            created_at=started + timedelta(seconds=1),
        )
    )
    db_session.add(
        AgentRun(
            id=cancelled_id,
            agent_id=UUID(child_agent["id"]),
            parent_run_id=root_id,
            root_run_id=root_id,
            trigger_type="delegation",
            status="cancelled",
            created_at=started + timedelta(seconds=2),
        )
    )
    db_session.add_all(
        [
            AgentRunJournalEntry(
                run_id=root_id,
                sequence=1,
                kind="model_request",
                data={"model": "m"},
            ),
            AgentRunJournalEntry(
                run_id=root_id,
                sequence=2,
                kind="tool_call",
                data={
                    "tool_name": "search",
                    "arguments": {"api_key": "shh-secret", "q": "x"},
                },
            ),
            AgentRunJournalEntry(
                run_id=root_id,
                sequence=3,
                kind="delegation",
                data={
                    "tool_call_id": "call-1",
                    "child_run_id": str(failed_id),
                    "target_agent_name": child_agent["name"],
                },
            ),
            AgentRunJournalEntry(
                run_id=failed_id,
                sequence=1,
                kind="completion",
                data={"status": "failed"},
            ),
        ]
    )
    db_session.add(
        AgentRunCheckpoint(
            run_id=root_id,
            sequence=1,
            format_version=1,
            state={"messages": [{"role": "user"}]},
            attempt=1,
        )
    )
    await db_session.commit()
    return {
        "agents": (root_agent, child_agent),
        "root_id": root_id,
        "failed_id": failed_id,
        "cancelled_id": cancelled_id,
    }


async def _teardown(e2e_client, platform_admin, db_session, seed: dict) -> None:
    for run_id in (
        seed["root_id"],
        seed["failed_id"],
        seed["cancelled_id"],
    ):
        await db_session.execute(
            delete(AgentRunJournalEntry).where(
                AgentRunJournalEntry.run_id == run_id
            )
        )
        await db_session.execute(
            delete(AgentRunCheckpoint).where(
                AgentRunCheckpoint.run_id == run_id
            )
        )
    await db_session.execute(
        delete(AgentRun).where(
            AgentRun.id.in_(
                [seed["root_id"], seed["failed_id"], seed["cancelled_id"]]
            )
        )
    )
    await db_session.commit()
    for agent in seed["agents"]:
        e2e_client.delete(
            f"/api/agents/{agent['id']}", headers=platform_admin.headers
        )


async def test_debugger_tree_timeline_snapshot_checkpoints(
    e2e_client,
    platform_admin,
    db_session: AsyncSession,
):
    seed = await _seed_tree(e2e_client, platform_admin, db_session)
    root_id = seed["root_id"]
    try:
        tree = e2e_client.get(
            f"/api/agent-runs/{root_id}/tree", headers=platform_admin.headers
        )
        assert tree.status_code == 200, tree.text
        tree_body = tree.json()
        assert tree_body["root_run_id"] == str(root_id)
        assert tree_body["total_runs"] == 3
        assert {c["status"] for c in tree_body["root"]["children"]} == {
            "failed",
            "cancelled",
        }

        timeline = e2e_client.get(
            f"/api/agent-runs/{root_id}/timeline",
            headers=platform_admin.headers,
        )
        assert timeline.status_code == 200, timeline.text
        entries = timeline.json()["entries"]
        assert [e["sequence"] for e in entries] == [1, 2, 3]
        tool_entry = entries[1]
        assert tool_entry["kind"] == "tool_call"
        assert tool_entry["detail"]["arguments"]["api_key"] == "[REDACTED]"
        assert "shh-secret" not in timeline.text

        page_one = e2e_client.get(
            f"/api/agent-runs/{root_id}/timeline?limit=2",
            headers=platform_admin.headers,
        )
        assert page_one.status_code == 200, page_one.text
        cursor = page_one.json()["next_cursor"]
        assert cursor
        page_two = e2e_client.get(
            f"/api/agent-runs/{root_id}/timeline?limit=2&cursor={cursor}",
            headers=platform_admin.headers,
        )
        assert page_two.status_code == 200, page_two.text
        assert [e["sequence"] for e in page_two.json()["entries"]] == [3]

        merged = e2e_client.get(
            f"/api/agent-runs/{root_id}/timeline?include_descendants=true",
            headers=platform_admin.headers,
        )
        assert merged.status_code == 200, merged.text
        assert len(merged.json()["entries"]) == 4

        snapshot = e2e_client.get(
            f"/api/agent-runs/{root_id}/snapshot",
            headers=platform_admin.headers,
        )
        assert snapshot.status_code == 200, snapshot.text
        snap_body = snapshot.json()
        assert snap_body["status"] == "waiting_child"
        assert snap_body["tool_names"] == ["search"]
        assert snap_body["correlation"] == {"ticket_id": "T-1"}
        assert "caller_context" not in snapshot.text
        assert "root prompt" not in snapshot.text

        checkpoints = e2e_client.get(
            f"/api/agent-runs/{root_id}/checkpoints",
            headers=platform_admin.headers,
        )
        assert checkpoints.status_code == 200, checkpoints.text
        cp_body = checkpoints.json()
        assert len(cp_body["checkpoints"]) == 1
        assert cp_body["checkpoints"][0]["message_count"] == 1
    finally:
        await _teardown(e2e_client, platform_admin, db_session, seed)


async def test_debugger_detail_response_unchanged_with_links(
    e2e_client,
    platform_admin,
    db_session: AsyncSession,
):
    seed = await _seed_tree(e2e_client, platform_admin, db_session)
    try:
        detail = e2e_client.get(
            f"/api/agent-runs/{seed['root_id']}",
            headers=platform_admin.headers,
        )
        assert detail.status_code == 200, detail.text
        body = detail.json()
        # Existing contract fields keep their shape...
        assert body["status"] == "waiting_child"
        assert [c["status"] for c in body["child_runs"]] == [
            "failed",
            "cancelled",
        ]
        # ...while debugger navigation arrives additively.
        assert body["debug_links"] == {
            "tree": f"/api/agent-runs/{seed['root_id']}/tree",
            "timeline": f"/api/agent-runs/{seed['root_id']}/timeline",
            "snapshot": f"/api/agent-runs/{seed['root_id']}/snapshot",
            "checkpoints": f"/api/agent-runs/{seed['root_id']}/checkpoints",
        }
    finally:
        await _teardown(e2e_client, platform_admin, db_session, seed)


async def test_debugger_unknown_run_and_bad_params(
    e2e_client,
    platform_admin,
    db_session: AsyncSession,
):
    seed = await _seed_tree(e2e_client, platform_admin, db_session)
    missing = str(uuid4())
    try:
        for path in ("tree", "timeline", "snapshot", "checkpoints"):
            response = e2e_client.get(
                f"/api/agent-runs/{missing}/{path}",
                headers=platform_admin.headers,
            )
            assert response.status_code == 404, (path, response.text)

        bad_cursor = e2e_client.get(
            f"/api/agent-runs/{seed['root_id']}/timeline?cursor=bogus",
            headers=platform_admin.headers,
        )
        assert bad_cursor.status_code == 422, bad_cursor.text

        bad_kind = e2e_client.get(
            f"/api/agent-runs/{seed['root_id']}/timeline?kind=nope",
            headers=platform_admin.headers,
        )
        assert bad_kind.status_code == 422, bad_kind.text

        over_limit = e2e_client.get(
            f"/api/agent-runs/{seed['root_id']}/timeline?limit=500",
            headers=platform_admin.headers,
        )
        assert over_limit.status_code == 422, over_limit.text
    finally:
        await _teardown(e2e_client, platform_admin, db_session, seed)


async def test_debugger_routes_in_openapi(e2e_client) -> None:
    spec = e2e_client.get("/openapi.json")
    assert spec.status_code == 200, spec.text
    paths = spec.json()["paths"]
    for suffix in ("tree", "timeline", "snapshot", "checkpoints"):
        assert f"/api/agent-runs/{{run_id}}/{suffix}" in paths
