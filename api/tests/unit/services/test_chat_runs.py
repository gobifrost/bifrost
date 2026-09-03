"""Unit tests for public Chat run state."""

from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

from src.services.chat_errors import CHAT_FAILURE_MESSAGE, CHAT_TIMEOUT_MESSAGE
from src.services.chat_runs import _run_public


def _run(*, status: str, error: str | None) -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid4(),
        conversation_id=uuid4(),
        agent_id=None,
        status=status,
        error=error,
        created_at=datetime.now(timezone.utc),
        started_at=None,
        completed_at=None,
    )


def test_run_public_replaces_internal_failure_diagnostic() -> None:
    public_run = _run_public(
        _run(status="failed", error="Traceback: internal implementation detail")
    )

    assert public_run is not None
    assert public_run.error == CHAT_FAILURE_MESSAGE


def test_run_public_uses_timeout_copy() -> None:
    public_run = _run_public(_run(status="timeout", error="Timed out after 300s"))

    assert public_run is not None
    assert public_run.error == CHAT_TIMEOUT_MESSAGE


def test_run_public_preserves_empty_error() -> None:
    public_run = _run_public(_run(status="running", error=None))

    assert public_run is not None
    assert public_run.error is None
