"""Runtime log delivery does not lock out infrastructure logging threads."""

import asyncio
import logging
import threading
from uuid import uuid4

import pytest

from src.sdk.context import ExecutionContext
from src.services.execution import engine


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["workflow", "service"])
async def test_log_transport_can_wait_for_a_thread_that_emits_infrastructure_logs(monkeypatch, kind):
    from bifrost import _runtime_io

    completed_while_sending = []
    threads = []

    def send_log(*args, **kwargs):
        finished = threading.Event()

        def transport_thread():
            record = logging.LogRecord(
                "asyncio", logging.DEBUG, "/usr/lib/asyncio/events.py", 1,
                "transport event loop initialized", (), None,
            )
            logging.getLogger("asyncio").handle(record)
            finished.set()

        thread = threading.Thread(target=transport_thread)
        threads.append(thread)
        thread.start()
        thread.join(timeout=1)
        completed_while_sending.append(finished.is_set())
        return "1-0"

    monkeypatch.setattr(_runtime_io, "write_log", send_log)
    monkeypatch.setattr(engine, "write_log", send_log)
    context = ExecutionContext(
        user_id=str(uuid4()), email="actor@example.test", name="Contoso Actor",
        scope="GLOBAL", organization=None, is_platform_admin=False,
        is_function_key=False, execution_id=str(uuid4()),
    )

    async def work():
        logging.getLogger(__name__).info("Contoso ready")
        return "done"

    root_logger = logging.getLogger()
    original_level = root_logger.level
    try:
        if kind == "service":
            config = engine.ServiceRunConfig(
                service_id=str(uuid4()), attempt_id=str(uuid4()), lease_token="test-lease",
                revision="review", token="test-attempt", token_expires_at="2026-10-17T00:00:00Z",
            )
            result, logs, _ = await engine._execute_service_with_trace(
                work, context, {}, config, asyncio.Event(),
            )
            messages = [row["message"] for row in logs]
        else:
            result, _, logs = await engine._execute_workflow_with_trace(
                work, context, {}, execution_id=context.execution_id,
            )
            messages = logs
    finally:
        root_logger.setLevel(original_level)
        for thread in threads:
            thread.join(timeout=1)

    assert result == "done"
    assert completed_while_sending and all(completed_while_sending)
    assert any("Contoso ready" in message for message in messages)
    assert not any("transport event loop initialized" in message for message in messages)


@pytest.mark.asyncio
async def test_failed_service_log_delivery_keeps_local_rows_without_recursing(monkeypatch):
    from unittest.mock import Mock

    from bifrost import _runtime_io

    send = Mock(side_effect=RuntimeError("log transport unavailable"))
    monkeypatch.setattr(_runtime_io, "write_log", send)
    context = ExecutionContext(
        user_id=str(uuid4()), email="actor@example.test", name="Contoso Actor",
        scope="GLOBAL", organization=None, is_platform_admin=False,
        is_function_key=False, execution_id=str(uuid4()),
    )

    async def work():
        return "done"

    work.__module__ = engine.__name__
    config = engine.ServiceRunConfig(
        service_id=str(uuid4()), attempt_id=str(uuid4()), lease_token="test-lease",
        revision="review", token="test-attempt", token_expires_at="2026-10-17T00:00:00Z",
    )
    root_logger = logging.getLogger()
    original_level = root_logger.level
    try:
        result, logs, _ = await engine._execute_service_with_trace(
            work, context, {}, config, asyncio.Event(),
        )
    finally:
        root_logger.setLevel(original_level)
    assert result == "done"
    assert send.call_count == len(logs)
    assert logs
    assert not any("log publish failed" in row["message"] for row in logs)
