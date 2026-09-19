"""Unit tests for PG-authoritative agent run enqueue."""

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from src.services.execution import agent_run_service
from src.services.execution.agent_run_service import enqueue_agent_run


@pytest.fixture
def db_session(monkeypatch):
    session = AsyncMock()
    session.add = MagicMock()
    session_context = MagicMock()
    session_context.__aenter__ = AsyncMock(return_value=session)
    session_context.__aexit__ = AsyncMock(return_value=False)
    session_factory = MagicMock(return_value=session_context)
    monkeypatch.setattr(
        agent_run_service,
        "get_session_factory",
        MagicMock(return_value=session_factory),
    )
    return session


@pytest.fixture
def snapshot_agent():
    with patch(
        "src.services.agent_runtime.execution_snapshot.snapshot_agent",
        new_callable=AsyncMock,
        return_value={"format_version": 1, "agent_name": "pinned"},
    ) as mock_snapshot:
        yield mock_snapshot


class TestEnqueueAgentRun:
    @pytest.mark.asyncio
    @patch("src.services.execution.agent_run_service.publish_message")
    async def test_persists_queued_run_before_publish(
        self, mock_publish, db_session, snapshot_agent
    ):
        calls = []
        db_session.commit.side_effect = lambda: calls.append("commit")
        mock_publish.side_effect = lambda *_: calls.append("publish")

        run_id = await enqueue_agent_run(
            agent_id=str(uuid4()),
            trigger_type="event",
            input_data={"ticket_id": 123},
        )

        queued_run = db_session.add.call_args.args[0]
        assert str(queued_run.id) == run_id
        assert queued_run.status == "queued"
        assert queued_run.input == {"ticket_id": 123}
        # Immutable snapshot persisted in the admitting transaction.
        assert queued_run.execution_snapshot == {
            "format_version": 1,
            "agent_name": "pinned",
        }
        # Top-level runs root their own tree.
        assert str(queued_run.root_run_id) == run_id
        assert calls == ["commit", "publish"]
        assert mock_publish.call_args.args[0] == "agent-runs"

    @pytest.mark.asyncio
    @patch("src.services.execution.agent_run_service.publish_message")
    async def test_enqueue_nudge_carries_only_run_id(
        self, mock_publish, db_session, snapshot_agent
    ):
        run_id = await enqueue_agent_run(
            agent_id=str(uuid4()),
            trigger_type="sdk",
            input_data={"task": "analyze"},
            output_schema={"action": {"type": "string"}},
            sync=True,
        )

        assert mock_publish.call_args.args[1] == {"run_id": run_id}

    @pytest.mark.asyncio
    @patch("src.services.execution.agent_run_service.publish_message")
    async def test_enqueue_persists_caller_context_and_correlation(
        self, mock_publish, db_session, snapshot_agent
    ):
        await enqueue_agent_run(
            agent_id=str(uuid4()),
            trigger_type="sdk",
            caller_context={"ticket_id": 7},
            correlation={"kind": "ticket", "ticket_id": "7"},
        )

        queued_run = db_session.add.call_args.args[0]
        assert queued_run.caller_context == {"ticket_id": 7}
        assert queued_run.correlation == {"kind": "ticket", "ticket_id": "7"}

    @pytest.mark.asyncio
    @patch("src.services.execution.agent_run_service.publish_message")
    async def test_runs_lifecycle_hook_before_queue_publish(
        self, mock_publish, db_session, snapshot_agent
    ):
        calls = []
        db_session.commit.side_effect = lambda: calls.append("commit")
        before_publish = AsyncMock(side_effect=lambda *_: calls.append("lifecycle"))
        mock_publish.side_effect = lambda *_: calls.append("publish")

        run_id = await enqueue_agent_run(
            agent_id=None,
            trigger_type="chat",
            before_queue_publish=before_publish,
        )

        before_publish.assert_awaited_once_with(run_id)
        assert calls == ["commit", "lifecycle", "publish"]

    @pytest.mark.asyncio
    @patch("src.services.execution.agent_run_service.publish_message")
    async def test_enqueue_supports_agentless_conversation_runs(
        self, mock_publish, db_session, snapshot_agent
    ):
        conversation_id = str(uuid4())

        await enqueue_agent_run(
            agent_id=None,
            trigger_type="chat",
            conversation_id=conversation_id,
            input_data={"content": "hello"},
        )

        queued_run = db_session.add.call_args.args[0]
        assert queued_run.agent_id is None
        assert str(queued_run.conversation_id) == conversation_id
        # Chat runs resolve live conversation state; no snapshot is pinned.
        assert queued_run.execution_snapshot is None
        snapshot_agent.assert_not_called()
        assert mock_publish.call_args.args[1] == {"run_id": str(queued_run.id)}

    @pytest.mark.asyncio
    @patch("src.services.execution.agent_run_service.publish_message")
    async def test_enqueue_uses_provided_run_id(
        self, mock_publish, db_session, snapshot_agent
    ):
        expected_run_id = str(uuid4())

        run_id = await enqueue_agent_run(
            agent_id=str(uuid4()),
            trigger_type="sdk",
            run_id=expected_run_id,
        )

        assert run_id == expected_run_id

    @pytest.mark.asyncio
    @patch(
        "src.services.execution.agent_run_service.publish_message",
        new_callable=AsyncMock,
    )
    async def test_publish_failure_keeps_run_queued_with_recoverable_error(
        self, mock_publish, db_session, snapshot_agent
    ):
        mock_publish.side_effect = RuntimeError("queue unavailable")

        async def get_added_run(*_args, **_kwargs):
            return db_session.add.call_args.args[0]

        db_session.get.side_effect = get_added_run

        with pytest.raises(RuntimeError, match="queue unavailable"):
            await enqueue_agent_run(
                agent_id=str(uuid4()),
                trigger_type="sdk",
            )

        queued_run = db_session.add.call_args.args[0]
        # Admitted work is never terminalized by a transport failure.
        assert queued_run.status == "queued"
        assert "remains queued" in queued_run.error
        assert queued_run.completed_at is None
