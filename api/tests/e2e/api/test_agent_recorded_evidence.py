"""Recorded-evidence projection over live database fixtures."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from sqlalchemy import delete

from shared.agent_recorded_evidence import (
    RecordedEvidenceNotFound,
    load_recorded_run_evidence,
)
from src.core.principal import UserPrincipal
from src.models.orm.agent_runs import (
    AgentRun,
    AgentRunJournalEntry,
    AgentToolInvocation,
)
from src.models.orm.ai_usage import AIUsage
from src.services.agent_runtime import types as runtime_types

pytestmark = pytest.mark.asyncio


def _principal(user) -> UserPrincipal:
    return UserPrincipal(
        user_id=user.user_id,
        email=user.email,
        organization_id=user.organization_id,
        is_superuser=False,
    )


async def test_loader_honors_private_delegation_visibility(alice_user, bob_user, db_session):
    run = AgentRun(
        id=uuid4(),
        org_id=alice_user.organization_id,
        trigger_type="delegation",
        status="completed",
        caller_user_id=str(bob_user.user_id),
        root_run_id=None,
    )
    db_session.add(run)
    await db_session.flush()
    run.root_run_id = run.id
    db_session.add(
        AgentRunJournalEntry(
            run_id=run.id,
            sequence=1,
            kind=runtime_types.JOURNAL_COMPLETION,
            data={"status": "completed"},
        )
    )
    await db_session.commit()
    try:
        with pytest.raises(RecordedEvidenceNotFound):
            await load_recorded_run_evidence(db_session, run.id, user=_principal(alice_user))

        result = await load_recorded_run_evidence(
            db_session, run.id, user=_principal(bob_user)
        )
        assert result["evidence"]["terminal_status"] == "completed"
        assert result["completeness"]["tool_calls"] is True
    finally:
        await db_session.execute(delete(AgentRun).where(AgentRun.id == run.id))
        await db_session.commit()


async def test_loader_projects_durable_tool_and_usage_rows(alice_user, db_session):
    run_id = uuid4()
    started = datetime(2026, 9, 20, 12, 0, tzinfo=timezone.utc)
    db_session.add(
        AgentRun(
            id=run_id,
            org_id=alice_user.organization_id,
            trigger_type="api",
            status="completed",
            output={"answer": "ok"},
            iterations_used=2,
            root_run_id=run_id,
            started_at=started,
            completed_at=started + timedelta(seconds=2),
        )
    )
    db_session.add_all(
        [
            AgentRunJournalEntry(
                run_id=run_id,
                sequence=1,
                kind=runtime_types.JOURNAL_TOOL_CALL,
                data={
                    "tool_name": "get_ticket",
                    "tool_call_id": "call-1",
                    "arguments": {"id": "T-1"},
                },
            ),
            AgentRunJournalEntry(
                run_id=run_id,
                sequence=2,
                kind=runtime_types.JOURNAL_COMPLETION,
                data={"status": "completed"},
            ),
            AgentToolInvocation(
                operation_id=uuid4().hex,
                run_id=run_id,
                provider_tool_call_id="call-1",
                tool_name="get_ticket",
                arguments={"id": "T-1"},
                state="completed",
                idempotency_key=f"{run_id}:call-1",
            ),
            AIUsage(
                agent_run_id=run_id,
                organization_id=alice_user.organization_id,
                provider="openai",
                model="m",
                input_tokens=12,
                output_tokens=8,
                cost=0.001,
                sequence=1,
            ),
        ]
    )
    await db_session.commit()
    try:
        result = await load_recorded_run_evidence(
            db_session, run_id, user=_principal(alice_user)
        )
        assert result["completeness"]["tool_calls"] is True
        assert result["completeness"]["usage.tokens"] is False
        assert result["evidence"]["tool_calls"][0]["name"] == "get_ticket"
        assert result["evidence"]["usage"]["tokens"] == 20
        assert result["evidence"]["usage"]["latency_ms"] == 2000
        assert any("observational" in item for item in result["limitations"])
    finally:
        await db_session.execute(delete(AgentRun).where(AgentRun.id == run_id))
        await db_session.commit()
