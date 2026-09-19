"""Agent-run response constructors expose persisted runtime fields."""
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from src.core.principal import UserPrincipal
from src.models.contracts.agent_runs import (
    AgentRunChildResponse,
    AgentRunDetailResponse,
    AgentRunResponse,
)
from src.models.orm.agent_runs import AgentRun
from src.routers.agent_runs import _get_executable_agent, _run_to_response, get_agent_run


def test_agent_run_response_has_new_fields():
    fields = AgentRunResponse.model_fields
    for name in (
        "asked", "did", "metadata", "confidence", "confidence_reason",
        "verdict", "verdict_note", "verdict_set_at", "verdict_set_by",
        "output_schema", "contract_valid", "contract_errors",
    ):
        assert name in fields, f"missing {name} on AgentRunResponse"


def test_agent_run_detail_response_inherits_new_fields():
    fields = AgentRunDetailResponse.model_fields
    for name in (
        "asked", "did", "metadata", "confidence",
        "verdict", "verdict_note",
    ):
        assert name in fields


def test_agent_run_detail_response_has_lean_child_summaries():
    assert "child_runs" in AgentRunDetailResponse.model_fields
    assert set(AgentRunChildResponse.model_fields) == {
        "id",
        "agent_id",
        "agent_name",
        "status",
        "asked",
        "did",
        "answered",
        "duration_ms",
        "created_at",
    }


def _contract_run() -> AgentRun:
    return AgentRun(
        id=uuid4(),
        trigger_type="api",
        status="completed",
        input={"question": "status"},
        output={"answer": "ok"},
        output_schema={"type": "object"},
        contract_valid=False,
        contract_errors=["answer is required"],
        iterations_used=1,
        tokens_used=2,
        summary_status="pending",
        created_at=datetime.now(timezone.utc),
    )


def test_list_response_constructor_preserves_contract_outcome():
    response = _run_to_response(_contract_run())

    assert response.output_schema == {"type": "object"}
    assert response.contract_valid is False
    assert response.contract_errors == ["answer is required"]


@pytest.mark.asyncio
async def test_detail_response_constructor_preserves_contract_outcome():
    run = _contract_run()
    run.steps = []
    single_run = MagicMock()
    single_run.scalar_one_or_none.return_value = run
    no_entries = MagicMock()
    no_entries.scalars.return_value.all.return_value = []
    db = AsyncMock()
    # Run lookup, AI usage lookup, then child-run lookup.
    db.execute.side_effect = [single_run, no_entries, no_entries]
    user = UserPrincipal(
        user_id=uuid4(),
        email="viewer@example.test",
        organization_id=None,
        is_superuser=True,
    )

    response = await get_agent_run(run.id, db, user)

    assert response.output_schema == {"type": "object"}
    assert response.contract_valid is False
    assert response.contract_errors == ["answer is required"]


@pytest.mark.asyncio
async def test_execute_by_name_returns_404_when_repository_denies_visibility():
    db = AsyncMock()
    user = UserPrincipal(
        user_id=uuid4(),
        email="member@example.test",
        organization_id=uuid4(),
        is_external=True,
    )
    repository = MagicMock()
    repository.get = AsyncMock(return_value=None)

    repository_factory = MagicMock(return_value=repository)
    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setattr(
            "src.routers.agent_runs.AgentRepository", repository_factory
        )
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc_info:
            await _get_executable_agent(db, user, "other-org-private-agent")

    assert exc_info.value.status_code == 404
    assert repository_factory.call_args.kwargs == {
        "org_id": user.organization_id,
        "user_id": user.user_id,
        "is_superuser": False,
        "is_external": True,
    }
    repository.get.assert_awaited_once_with(name="other-org-private-agent")
