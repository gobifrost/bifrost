"""Focused unit tests for the SDK agent runs shared service.

Covers ``shared.sdk_agent_runs`` (enqueue success/paused/inactive
Solution plus name-lookup precedence, and get-run
visible/hidden/missing/usage/steps) DB-backed via the ``db_session``
fixture. HTTP handler behavior (202 vs 200 mapping, error translation)
is preserved by the existing e2e coverage (``test_agent_run_enqueue``,
``test_pause_semantics``, ``test_agent_run_children``); the router
delegates to these same functions.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from shared.sdk_agent_runs import (
    SdkAgentRunError,
    enqueue_sdk_agent_run,
    get_sdk_agent_run,
)
from src.core.principal import UserPrincipal
from src.models.contracts.agent_runs import AgentRunEnqueueResponse, PausedResponse


def _principal(org_id=None, **kwargs) -> UserPrincipal:
    return UserPrincipal(
        user_id=kwargs.get("user_id", uuid4()),
        email=kwargs.get("email", "sdk-agent-runs@test.local"),
        organization_id=org_id,
        name=kwargs.get("name", "SDK Agent Runs"),
        is_superuser=kwargs.get("is_superuser", False),
    )


async def _seed_org(db_session, **kwargs):
    from src.models.orm.organizations import Organization as OrganizationModel

    row = OrganizationModel(
        name=f"sdk-agent-runs-org-{uuid4().hex[:8]}",
        is_active=kwargs.get("is_active", True),
        is_provider=kwargs.get("is_provider", False),
        created_by="sdk-agent-runs-test",
    )
    db_session.add(row)
    await db_session.flush()
    return row


async def _seed_solution(db_session, *, status="active"):
    from src.models.orm.solutions import Solution as SolutionModel

    row = SolutionModel(
        slug=f"sdk-agent-runs-sol-{uuid4().hex[:8]}",
        name="SDK Agent Runs Solution",
        status=status,
    )
    db_session.add(row)
    await db_session.flush()
    return row


async def _seed_agent(db_session, name, **kwargs):
    from src.models.orm.agents import Agent as AgentModel

    row = AgentModel(
        name=name,
        system_prompt="Test agent prompt.",
        is_active=kwargs.get("is_active", True),
        organization_id=kwargs.get("organization_id"),
        solution_id=kwargs.get("solution_id"),
        created_by="sdk-agent-runs-test",
    )
    db_session.add(row)
    await db_session.flush()
    return row


async def _seed_run(db_session, agent_id, **kwargs):
    from src.models.orm.agent_runs import AgentRun as AgentRunModel

    row = AgentRunModel(
        agent_id=agent_id,
        trigger_type=kwargs.get("trigger_type", "api"),
        status=kwargs.get("status", "completed"),
        org_id=kwargs.get("org_id"),
        caller_user_id=kwargs.get("caller_user_id"),
        parent_run_id=kwargs.get("parent_run_id"),
    )
    db_session.add(row)
    await db_session.flush()
    return row


async def _seed_step(db_session, run_id, step_number, **kwargs):
    from src.models.orm.agent_runs import AgentRunStep as AgentRunStepModel

    row = AgentRunStepModel(
        run_id=run_id,
        step_number=step_number,
        type=kwargs.get("type", "tool_call"),
        content=kwargs.get("content", {"tool": "lookup"}),
        tokens_used=kwargs.get("tokens_used", 10),
        duration_ms=kwargs.get("duration_ms", 5),
    )
    db_session.add(row)
    await db_session.flush()
    return row


async def _seed_usage(db_session, run_id, **kwargs):
    from src.models.orm.ai_usage import AIUsage as AIUsageModel

    row = AIUsageModel(
        agent_run_id=run_id,
        provider=kwargs.get("provider", "openai"),
        model=kwargs.get("model", "gpt-4o"),
        input_tokens=kwargs.get("input_tokens", 100),
        output_tokens=kwargs.get("output_tokens", 50),
        cache_read_tokens=kwargs.get("cache_read_tokens", 0),
        cache_write_tokens=kwargs.get("cache_write_tokens", 0),
        provider_cost=kwargs.get("provider_cost", Decimal("0.001")),
        cost=kwargs.get("cost", Decimal("0.002")),
        duration_ms=kwargs.get("duration_ms", 120),
        timestamp=kwargs.get(
            "timestamp", datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc)
        ),
        sequence=kwargs.get("sequence", 1),
    )
    db_session.add(row)
    await db_session.flush()
    return row


def _redis_stream_context(entries):
    """Mock ``get_redis()`` async context manager yielding stream entries."""
    redis = AsyncMock()
    redis.xrange = AsyncMock(return_value=entries)
    context = MagicMock()
    context.__aenter__ = AsyncMock(return_value=redis)
    context.__aexit__ = AsyncMock(return_value=False)
    return context


@pytest.mark.asyncio
class TestEnqueueSdkAgentRun:
    async def test_success_enqueues_with_actor_attribution(self, db_session):
        org = await _seed_org(db_session)
        agent = await _seed_agent(db_session, "Ticket Agent")
        principal = _principal(org.id, name="Caller Name")
        receipt_id = str(uuid4())

        with patch(
            "src.services.execution.agent_run_service.enqueue_agent_run",
            new=AsyncMock(return_value=receipt_id),
        ) as mock_enqueue:
            result = await enqueue_sdk_agent_run(
                db_session,
                principal,
                agent_name="Ticket Agent",
                input_data={"ticket_id": 42},
                output_schema={"type": "object"},
            )

        assert isinstance(result, AgentRunEnqueueResponse)
        assert str(result.run_id) == receipt_id
        assert result.status == "queued"
        mock_enqueue.assert_awaited_once_with(
            agent_id=str(agent.id),
            trigger_type="api",
            input_data={"ticket_id": 42},
            output_schema={"type": "object"},
            org_id=str(org.id),
            caller_user_id=str(principal.user_id),
            caller_email=principal.email,
            caller_name="Caller Name",
            sync=False,
        )

    async def test_name_lookup_is_case_insensitive(self, db_session):
        await _seed_agent(db_session, "Ticket Agent")
        receipt_id = str(uuid4())

        with patch(
            "src.services.execution.agent_run_service.enqueue_agent_run",
            new=AsyncMock(return_value=receipt_id),
        ) as mock_enqueue:
            result = await enqueue_sdk_agent_run(
                db_session,
                _principal(is_superuser=True),
                agent_name="ticket agent",
            )

        assert isinstance(result, AgentRunEnqueueResponse)
        mock_enqueue.assert_awaited_once()

    async def test_unknown_agent_is_404(self, db_session):
        with patch(
            "src.services.execution.agent_run_service.enqueue_agent_run",
            new=AsyncMock(),
        ) as mock_enqueue:
            with pytest.raises(SdkAgentRunError) as exc_info:
                await enqueue_sdk_agent_run(
                    db_session,
                    _principal(is_superuser=True),
                    agent_name="No Such Agent",
                )

        assert exc_info.value.status_code == 404
        assert exc_info.value.detail == "Agent 'No Such Agent' not found"
        mock_enqueue.assert_not_awaited()

    async def test_paused_agent_returns_paused_response(self, db_session):
        agent = await _seed_agent(db_session, "Paused Agent", is_active=False)

        with patch(
            "src.services.execution.agent_run_service.enqueue_agent_run",
            new=AsyncMock(),
        ) as mock_enqueue:
            result = await enqueue_sdk_agent_run(
                db_session,
                _principal(is_superuser=True),
                agent_name="Paused Agent",
            )

        assert isinstance(result, PausedResponse)
        assert result.message == (
            "Agent 'Paused Agent' is paused. Request not processed."
        )
        assert result.agent_id == agent.id
        mock_enqueue.assert_not_awaited()

    async def test_inactive_solution_is_409(self, db_session):
        solution = await _seed_solution(db_session, status="inactive")
        await _seed_agent(
            db_session, "Solution Agent", solution_id=solution.id
        )

        with patch(
            "src.services.execution.agent_run_service.enqueue_agent_run",
            new=AsyncMock(),
        ) as mock_enqueue:
            with pytest.raises(SdkAgentRunError) as exc_info:
                await enqueue_sdk_agent_run(
                    db_session,
                    _principal(is_superuser=True),
                    agent_name="Solution Agent",
                )

        assert exc_info.value.status_code == 409
        assert exc_info.value.detail == (
            "Agent 'Solution Agent' belongs to an inactive solution. "
            "Reinstall the solution to execute this agent."
        )
        mock_enqueue.assert_not_awaited()

    async def test_active_solution_enqueues(self, db_session):
        solution = await _seed_solution(db_session, status="active")
        await _seed_agent(
            db_session, "Live Solution Agent", solution_id=solution.id
        )
        receipt_id = str(uuid4())

        with patch(
            "src.services.execution.agent_run_service.enqueue_agent_run",
            new=AsyncMock(return_value=receipt_id),
        ) as mock_enqueue:
            result = await enqueue_sdk_agent_run(
                db_session,
                _principal(is_superuser=True),
                agent_name="Live Solution Agent",
            )

        assert isinstance(result, AgentRunEnqueueResponse)
        mock_enqueue.assert_awaited_once()


@pytest.mark.asyncio
class TestGetSdkAgentRun:
    async def test_missing_run_is_404(self, db_session):
        missing = uuid4()
        with pytest.raises(SdkAgentRunError) as exc_info:
            await get_sdk_agent_run(
                db_session, _principal(is_superuser=True), run_id=missing
            )

        assert exc_info.value.status_code == 404
        assert exc_info.value.detail == f"Agent run {missing} not found"

    async def test_completed_run_returns_db_steps_children_and_usage(
        self, db_session
    ):
        agent = await _seed_agent(db_session, "History Agent")
        child_agent = await _seed_agent(db_session, "Child Agent")
        run = await _seed_run(db_session, agent.id, status="completed")
        await _seed_step(db_session, run.id, 1)
        await _seed_step(db_session, run.id, 2)
        child = await _seed_run(
            db_session,
            child_agent.id,
            status="completed",
            trigger_type="delegation",
            parent_run_id=run.id,
        )
        await _seed_usage(db_session, run.id, input_tokens=100, output_tokens=50)
        await _seed_usage(db_session, run.id, input_tokens=200, output_tokens=25)

        detail = await get_sdk_agent_run(
            db_session, _principal(is_superuser=True), run_id=run.id
        )

        assert detail.id == run.id
        assert detail.agent_name == "History Agent"
        assert detail.status == "completed"
        assert [s.step_number for s in detail.steps] == [1, 2]
        assert detail.child_run_ids == [child.id]
        assert [c.agent_name for c in detail.child_runs] == ["Child Agent"]
        assert detail.ai_usage is not None and len(detail.ai_usage) == 2
        assert detail.ai_totals is not None
        assert detail.ai_totals.total_input_tokens == 300
        assert detail.ai_totals.total_output_tokens == 75
        assert detail.ai_totals.call_count == 2

    async def test_hidden_run_is_404_for_other_org(self, db_session):
        org_a = await _seed_org(db_session)
        org_b = await _seed_org(db_session)
        agent = await _seed_agent(db_session, "Scoped Agent")
        run = await _seed_run(db_session, agent.id, org_id=org_a.id)

        with pytest.raises(SdkAgentRunError) as exc_info:
            await get_sdk_agent_run(
                db_session, _principal(org_b.id), run_id=run.id
            )

        assert exc_info.value.status_code == 404

    async def test_org_user_sees_own_org_run(self, db_session):
        org = await _seed_org(db_session)
        agent = await _seed_agent(db_session, "Own Agent")
        run = await _seed_run(db_session, agent.id, org_id=org.id)

        detail = await get_sdk_agent_run(
            db_session, _principal(org.id), run_id=run.id
        )

        assert detail.id == run.id

    async def test_in_progress_run_reads_steps_from_redis(self, db_session):
        agent = await _seed_agent(db_session, "Running Agent")
        run = await _seed_run(db_session, agent.id, status="running")
        step_id = uuid4()

        entries = [
            (
                "1-0",
                {
                    "id": str(step_id),
                    "run_id": str(run.id),
                    "step_number": "1",
                    "type": "tool_call",
                    "content": json.dumps({"tool": "lookup"}),
                    "tokens_used": "10",
                    "duration_ms": "5",
                    "created_at": "2026-09-01T12:00:00+00:00",
                },
            )
        ]

        with patch(
            "shared.sdk_agent_runs.get_redis",
            return_value=_redis_stream_context(entries),
        ):
            detail = await get_sdk_agent_run(
                db_session, _principal(is_superuser=True), run_id=run.id
            )

        assert len(detail.steps) == 1
        assert detail.steps[0].id == step_id
        assert detail.steps[0].content == {"tool": "lookup"}
        assert detail.steps[0].tokens_used == 10

    async def test_in_progress_redis_failure_falls_back_to_db(self, db_session):
        agent = await _seed_agent(db_session, "Fallback Agent")
        run = await _seed_run(db_session, agent.id, status="queued")
        await _seed_step(db_session, run.id, 1)

        with patch(
            "shared.sdk_agent_runs.get_redis",
            side_effect=RuntimeError("redis down"),
        ):
            detail = await get_sdk_agent_run(
                db_session, _principal(is_superuser=True), run_id=run.id
            )

        assert [s.step_number for s in detail.steps] == [1]
