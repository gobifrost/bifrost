"""Pause semantics at autonomous executor entry."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from src.core.principal import UserPrincipal
from src.services.authorization.enforce import Caller
from src.services.execution.autonomous_agent_executor import AutonomousAgentExecutor


@pytest.mark.asyncio
async def test_paused_agent_returns_paused_status_without_constructing_runtime():
    """A paused agent is refused before the shared runtime builder can run."""
    user_id = uuid4()
    caller = Caller(
        UserPrincipal(
            user_id=user_id,
            email="operator@example.test",
            organization_id=uuid4(),
            name="Operator",
            is_active=True,
        ),
        None,
    )
    agent = SimpleNamespace(
        id=uuid4(),
        name="Paused Agent",
        is_active=False,
        llm_profile_id=None,
    )
    session = AsyncMock()
    session_context = AsyncMock()
    session_context.__aenter__.return_value = session
    session_context.__aexit__.return_value = False
    session_factory = MagicMock(return_value=session_context)

    with (
        patch(
            "src.services.execution.autonomous_agent_executor.authorize_agent_execution",
            new=AsyncMock(return_value=(caller, agent)),
        ),
        patch(
            "src.services.execution.autonomous_agent_executor.get_llm_configs",
            new=AsyncMock(return_value=[SimpleNamespace(model="test-model")]),
        ),
        patch(
            "src.services.execution.autonomous_agent_executor.chain_context_window",
            new=AsyncMock(return_value=128_000),
        ),
        patch(
            "src.services.execution.autonomous_agent_executor.build_agent_runtime"
        ) as build_runtime,
    ):
        result = await AutonomousAgentExecutor(session_factory).run(
            agent,
            caller=caller,
            run_user_id=None,
            run_as=None,
            agent_execution_mode="delegated",
        )

    assert result == {
        "output": None,
        "iterations_used": 0,
        "tokens_used": 0,
        "status": "paused",
        "accepted": False,
        "message": "Agent 'Paused Agent' is paused. Request not processed.",
        "llm_model": "test-model",
    }
    build_runtime.assert_not_called()
