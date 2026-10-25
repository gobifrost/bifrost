"""Runtime log delivery does not lock out infrastructure logging threads."""

import asyncio
import base64
from io import StringIO
import logging
import sys
import threading
from uuid import uuid4

import pytest

from src.sdk.context import Caller, ExecutionContext
from src.services.execution import engine


_HEADER_LOG_MESSAGES = (
    "Authorization: Bearer header-secret\r\nCookie: session=cookie-secret; theme=dark",
    '{"Proxy-Authorization": "proxy-secret, continued", "Set-Cookie": "session=set-cookie-secret; Expires=Wed, 21 Oct"}',
    "{'authorization': 'single-quote-secret', 'cookie': 'session=single-cookie-secret; Authorization: nested-header-secret; Secure'}",
    r"headers=[(b'Authorization', b'bytes-tuple-secret'), (b\'Cookie\', b\'session=escaped-cookie-secret\\'quote; closing)paren\')]",
)


def _runtime_context() -> ExecutionContext:
    return ExecutionContext(
        user_id=str(uuid4()), email="actor@example.test", name="Contoso Actor",
        scope="GLOBAL", organization=None, is_platform_admin=False,
        is_function_key=False, execution_id=str(uuid4()),
    )


def _service_config() -> engine.ServiceRunConfig:
    return engine.ServiceRunConfig(
        service_id=str(uuid4()), attempt_id=str(uuid4()), lease_token="test-lease",
        revision="review", token="test-attempt", token_expires_at="2026-10-17T00:00:00Z",
    )


def _inline_script_request(code: str) -> engine.ExecutionRequest:
    return engine.ExecutionRequest(
        execution_id=str(uuid4()),
        caller=Caller(
            user_id=str(uuid4()), email="actor@example.test", name="Contoso Actor",
        ),
        organization=None,
        code=base64.b64encode(code.encode()).decode(),
        name="fresh_transport_import",
    )


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
    context = _runtime_context()

    async def work():
        logging.getLogger(__name__).info("Contoso ready")
        return "done"

    root_logger = logging.getLogger()
    original_level = root_logger.level
    try:
        if kind == "service":
            config = _service_config()
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
    context = _runtime_context()

    async def work():
        return "done"

    work.__module__ = engine.__name__
    config = _service_config()
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


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["workflow", "service"])
async def test_runtime_logs_scrub_headers_without_hiding_user_debug_or_info(monkeypatch, kind):
    """Credential-shaped headers never reach the console or runtime log transport."""
    from bifrost import _runtime_io

    written = []
    monkeypatch.setattr(_runtime_io, "write_log", lambda level, message: written.append((level, message)))
    monkeypatch.setattr(engine, "write_log", lambda level, message: written.append((level, message)))

    context = _runtime_context()
    root_logger = logging.getLogger()
    original_level = root_logger.level
    console_stream = StringIO()
    console = logging.StreamHandler(console_stream)
    console.setLevel(logging.DEBUG)
    console.setFormatter(logging.Formatter("[runtime] %(levelname)s - %(message)s"))
    root_logger.addHandler(console)

    transport_logger = logging.getLogger("httpcore.connection.verbose")
    original_transport_level = transport_logger.level
    transport_logger.setLevel(logging.DEBUG)

    async def work():
        user_logger = logging.getLogger(__name__)
        user_logger.debug("user debug survives")
        user_logger.info("user info survives")
        user_logger.warning("user warning survives")
        for message in _HEADER_LOG_MESSAGES:
            user_logger.warning("prefix %s", message)
        transport_logger.debug("transport Authorization: Bearer transport-secret")
        return "done"

    try:
        if kind == "service":
            result, captured, _ = await engine._execute_service_with_trace(
                work, context, {}, _service_config(), asyncio.Event(),
            )
            messages = [entry["message"] for entry in captured]
        else:
            result, _, captured = await engine._execute_workflow_with_trace(
                work, context, {}, execution_id=context.execution_id,
            )
            messages = captured
    finally:
        root_logger.removeHandler(console)
        root_logger.setLevel(original_level)
        transport_logger.setLevel(original_transport_level)

    assert result == "done"
    captured_output = "\n".join(messages)
    written_output = "\n".join(message for _, message in written)
    console_output = console_stream.getvalue()
    for output in (captured_output, written_output, console_output):
        assert "user debug survives" in output
        assert "user info survives" in output
        assert "user warning survives" in output
        assert "transport Authorization" not in output
        for secret in (
            "header-secret", "cookie-secret", "proxy-secret", "set-cookie-secret",
            "single-quote-secret", "single-cookie-secret", "bytes-tuple-secret",
            "escaped-cookie-secret", "nested-header-secret", "transport-secret", "continued",
        ):
            assert secret not in output


async def test_inline_script_fresh_transport_import_uses_transport_logger():
    """Fresh urllib3 imports retain their noisy logger while user DEBUG is captured."""
    transport_marker = "fresh-urllib3-transport-debug"
    user_marker = "inline-user-debug-survives"
    console_stream = StringIO()
    console = logging.StreamHandler(console_stream)
    console.setLevel(logging.DEBUG)
    root_logger = logging.getLogger()
    root_logger.addHandler(console)

    prefixes = ("requests", "urllib3")
    saved_modules = {
        name: module
        for name, module in sys.modules.items()
        if name == prefixes[0] or name.startswith(f"{prefixes[0]}.")
        or name == prefixes[1] or name.startswith(f"{prefixes[1]}.")
    }
    for name in saved_modules:
        sys.modules.pop(name)

    try:
        result = await engine.execute(_inline_script_request(f"""
import logging
import requests
import urllib3.connectionpool

urllib3.connectionpool.log.debug("{transport_marker}")
logging.getLogger("user").debug("{user_marker}")
result = "done"
"""))
    finally:
        root_logger.removeHandler(console)
        for name in tuple(sys.modules):
            if any(name == prefix or name.startswith(f"{prefix}.") for prefix in prefixes):
                sys.modules.pop(name)
        sys.modules.update(saved_modules)

    captured_output = "\n".join(entry["message"] for entry in result.logs)
    assert result.result == "done"
    assert transport_marker not in captured_output
    assert transport_marker not in console_stream.getvalue()
    assert user_marker in captured_output


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["workflow", "service"])
async def test_runtime_logging_configuration_is_restored_after_failure(kind):
    root_logger = logging.getLogger()
    original_root_level = root_logger.level
    root_logger.setLevel(logging.ERROR)
    console = logging.StreamHandler(StringIO())
    formatter = logging.Formatter("restore %(message)s")
    console.setFormatter(formatter)
    root_logger.addHandler(console)

    transport_loggers = [
        logging.getLogger("httpcore"),
        logging.getLogger("hpack.decoder"),
        logging.getLogger("h2.connection"),
        logging.getLogger("h11.connection"),
        logging.getLogger("urllib3.connectionpool"),
    ]
    original_transport_levels = [logger.level for logger in transport_loggers]
    for transport_logger in transport_loggers:
        transport_logger.setLevel(logging.DEBUG)

    async def fails():
        raise RuntimeError("expected execution failure")

    try:
        with pytest.raises(Exception, match="expected execution failure"):
            if kind == "service":
                await engine._execute_service_with_trace(
                    fails, _runtime_context(), {}, _service_config(), asyncio.Event(),
                )
            else:
                await engine._execute_workflow_with_trace(
                    fails, _runtime_context(), {}, execution_id=str(uuid4()),
                )
        assert root_logger.level == logging.ERROR
        assert console.formatter is formatter
        assert [logger.level for logger in transport_loggers] == [logging.DEBUG] * len(transport_loggers)
    finally:
        root_logger.removeHandler(console)
        root_logger.setLevel(original_root_level)
        for transport_logger, level in zip(transport_loggers, original_transport_levels):
            transport_logger.setLevel(level)
