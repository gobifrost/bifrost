"""Full agent management lifecycle — Plan 1 milestone happy path.

Exercises the agent management surfaces end-to-end via HTTP:

  1. Create agent (POST /api/agents)
  2. Seed a completed run directly via DB (matches the T7/T15 fixture pattern)
  3. Set verdict='down' via POST /api/agent-runs/{id}/verdict
  4. GET /api/agent-runs/{id}/flag-conversation — empty messages on first read
  5. Seed a flag conversation row directly (LLM-dependent POST /message is
     covered by ``tests/unit/test_tuning_service.py`` with a mocked client;
     the cross-process e2e runner cannot mock the LLM in the API container).
  6. PUT /api/agents/{id} with a new prompt + change_reason — verifies
     shape, history write, verdict preservation, and the 410 on the legacy
     tuning-session apply endpoint.
  7. GET /api/agents/{id} — verifies system_prompt now matches the new prompt.
  8. GET /api/agent-runs/{id} — verifies the verdict survived the apply.

Strategy: **C** (per the task spec). LLM-dependent steps (POST flag
conversation message, dry-run, consolidated tuning-session create) are
NOT exercised here. They have their own unit tests with mocked clients
(``tests/unit/test_tuning_service.py``, ``tests/unit/test_dry_run.py``,
``tests/unit/test_consolidated_tuning.py``). The test stack has no LLM
provider configured by default, and the runner is in a separate container
from the API server so we cannot patch it from here.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import AsyncGenerator
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.orm.agent_prompt_history import AgentPromptHistory
from src.models.orm.agent_run_flag_conversations import AgentRunFlagConversation
from src.models.orm.agent_run_verdict_history import AgentRunVerdictHistory
from src.models.orm.agent_runs import AgentRun
from src.models.orm.ai_usage import AIUsage

logger = logging.getLogger(__name__)


pytestmark = pytest.mark.asyncio


ORIGINAL_PROMPT = (
    "You are a routing agent. Route tickets based on keywords. "
    "Default to the network team if unsure."
)
NEW_PROMPT = (
    "You are a routing agent. Route tickets based on keywords. "
    "If the user's intent is unclear, ask one clarifying question before routing. "
    "Default to the network team only after clarification."
)


@pytest_asyncio.fixture
async def lifecycle_agent(
    e2e_client, platform_admin
) -> AsyncGenerator[dict, None]:
    """Create an agent via the public API; clean up after."""
    resp = e2e_client.post(
        "/api/agents",
        json={
            "name": f"Lifecycle Test Agent {uuid4().hex[:8]}",
            "description": "agent management m1 lifecycle smoke",
            "system_prompt": ORIGINAL_PROMPT,
            "channels": ["chat"],
            "access_level": "authenticated",
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
        # Best-effort fixture cleanup; teardown shouldn't fail the test
        logger.debug(f"fixture cleanup error: {e}")


@pytest_asyncio.fixture
async def lifecycle_run(
    lifecycle_agent, db_session: AsyncSession
) -> AsyncGenerator[AgentRun, None]:
    """Insert a completed AgentRun owned by ``lifecycle_agent``.

    We seed via DB rather than enqueueing a real run because the dev test
    stack has no LLM provider, so a real run would never complete.
    """
    now = datetime.now(timezone.utc)
    run = AgentRun(
        id=uuid4(),
        agent_id=UUID(lifecycle_agent["id"]),
        trigger_type="test",
        status="completed",
        iterations_used=2,
        tokens_used=300,
        input={"message": "VPN won't connect"},
        output={"text": "Routed to network team"},
        completed_at=now,
        summary_status="pending",
    )
    db_session.add(run)
    await db_session.commit()
    yield run
    # Cascade-related cleanup
    await db_session.execute(
        delete(AgentRunFlagConversation).where(
            AgentRunFlagConversation.run_id == run.id
        )
    )
    await db_session.execute(
        delete(AgentRunVerdictHistory).where(
            AgentRunVerdictHistory.run_id == run.id
        )
    )
    await db_session.execute(
        delete(AIUsage).where(AIUsage.agent_run_id == run.id)
    )
    await db_session.execute(
        delete(AgentPromptHistory).where(
            AgentPromptHistory.agent_id == UUID(lifecycle_agent["id"])
        )
    )
    await db_session.execute(delete(AgentRun).where(AgentRun.id == run.id))
    await db_session.commit()


class TestAgentManagementLifecycle:
    """End-to-end pass through the agent management surfaces.

    See module docstring for the strategy used to handle LLM-dependent
    endpoints — those are covered by unit tests with mocked LLM clients.
    """

    async def test_full_lifecycle(
        self,
        e2e_client,
        platform_admin,
        lifecycle_agent,
        lifecycle_run,
        db_session: AsyncSession,
    ):
        agent_id = lifecycle_agent["id"]
        run_id = str(lifecycle_run.id)

        # Step 1 (agent create) and step 2 (run seed) handled by fixtures.
        # Sanity check the agent fixture returned the prompt we expect.
        assert lifecycle_agent["system_prompt"] == ORIGINAL_PROMPT

        # Step 3: set verdict='down'.
        verdict_res = e2e_client.post(
            f"/api/agent-runs/{run_id}/verdict",
            json={
                "verdict": "down",
                "note": "Wrong route — should ask for clarification first.",
            },
            headers=platform_admin.headers,
        )
        assert verdict_res.status_code == 200, verdict_res.text
        verdict_body = verdict_res.json()
        assert verdict_body["verdict"] == "down"
        assert verdict_body["verdict_note"].startswith("Wrong route")
        assert verdict_body["verdict_set_at"] is not None
        assert verdict_body["verdict_set_by"] == str(platform_admin.user_id)

        # Step 4: GET /flag-conversation should create + return an empty conv.
        get_res = e2e_client.get(
            f"/api/agent-runs/{run_id}/flag-conversation",
            headers=platform_admin.headers,
        )
        assert get_res.status_code == 200, get_res.text
        get_body = get_res.json()
        assert get_body["run_id"] == run_id
        assert get_body["messages"] == []
        assert get_body["id"]  # conversation UUID issued

        # Step 5: seed conversation messages directly so the consolidated
        # tuning session has real content to operate on. (The HTTP POST
        # /message path is LLM-dependent and covered by unit tests.)
        now = datetime.now(timezone.utc)
        await db_session.execute(
            AgentRunFlagConversation.__table__.update()
            .where(AgentRunFlagConversation.run_id == lifecycle_run.id)
            .values(
                messages=[
                    {
                        "kind": "user",
                        "content": "Should have asked which 'VPN' first.",
                        "at": now.isoformat(),
                    },
                    {
                        "kind": "assistant",
                        "content": (
                            "Got it — proposing we add a clarifying-question "
                            "step before routing on ambiguous tickets."
                        ),
                        "at": now.isoformat(),
                    },
                ],
                last_updated_at=now,
            )
        )
        await db_session.commit()

        # Re-GET to confirm the seeded messages are visible via the API.
        seeded = e2e_client.get(
            f"/api/agent-runs/{run_id}/flag-conversation",
            headers=platform_admin.headers,
        )
        assert seeded.status_code == 200, seeded.text
        seeded_body = seeded.json()
        assert len(seeded_body["messages"]) == 2
        assert seeded_body["messages"][0]["kind"] == "user"
        assert seeded_body["messages"][1]["kind"] == "assistant"

        # Step 6: apply a new prompt via the normal authorized update.
        # This is LLM-free — it persists ``system_prompt``, writes
        # ``AgentPromptHistory``, and MUST preserve the flagged verdict
        # (the legacy tuning apply cleared verdicts; the replacement does
        # not). The legacy endpoint now answers 410.
        gone_res = e2e_client.post(
            f"/api/agents/{agent_id}/tuning-session/apply",
            json={"new_prompt": NEW_PROMPT},
            headers=platform_admin.headers,
        )
        assert gone_res.status_code == 410, gone_res.text
        agent_before = e2e_client.get(
            f"/api/agents/{agent_id}", headers=platform_admin.headers
        )
        assert agent_before.status_code == 200, agent_before.text
        updated_at = agent_before.json()["updated_at"]
        apply_res = e2e_client.put(
            f"/api/agents/{agent_id}",
            json={
                "system_prompt": NEW_PROMPT,
                "change_reason": "Add clarification step for ambiguous tickets.",
            },
            headers={
                **platform_admin.headers,
                "If-Unmodified-Since": updated_at,
            },
        )
        assert apply_res.status_code == 200, apply_res.text
        assert apply_res.json()["system_prompt"] == NEW_PROMPT

        # Step 6b: a stale guard blocks overwrites of newer production state.
        stale_res = e2e_client.put(
            f"/api/agents/{agent_id}",
            json={"system_prompt": "Stale overwrite attempt."},
            headers={
                **platform_admin.headers,
                "If-Unmodified-Since": updated_at,
            },
        )
        assert stale_res.status_code == 412, stale_res.text

        # Step 7: GET the agent — system_prompt should reflect the apply.
        agent_res = e2e_client.get(
            f"/api/agents/{agent_id}", headers=platform_admin.headers
        )
        assert agent_res.status_code == 200, agent_res.text
        assert agent_res.json()["system_prompt"] == NEW_PROMPT

        # Step 8: verify the run's verdict was PRESERVED by the apply step.
        # Capture IDs BEFORE expire_all() — touching ORM attributes after
        # expiration would trigger a sync lazy-load that errors out on the
        # async engine.
        run_uuid = lifecycle_run.id
        db_session.expire_all()

        run_row = (
            await db_session.execute(
                select(AgentRun).where(AgentRun.id == run_uuid)
            )
        ).scalar_one()
        assert run_row.verdict == "down"
        assert run_row.verdict_note is not None

        # Bonus: confirm an AgentPromptHistory row was written linking the change.
        history_rows = (
            (
                await db_session.execute(
                    select(AgentPromptHistory)
                    .where(AgentPromptHistory.agent_id == UUID(agent_id))
                    .order_by(AgentPromptHistory.changed_at.desc())
                )
            )
            .scalars()
            .all()
        )
        assert history_rows, "expected a prompt history row from PUT"
        history_row = history_rows[0]
        assert history_row.previous_prompt == ORIGINAL_PROMPT
        assert history_row.new_prompt == NEW_PROMPT
        assert history_row.changed_by == platform_admin.user_id
        assert (
            history_row.reason
            == "Add clarification step for ambiguous tickets."
        )
