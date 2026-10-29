"""Unit tests for engine service mode (Slice 3).

Covers: clean return, exception, stop-request unwind, context injection,
@service type enforcement, log capture, and the authenticated runtime-control
supervisor (ready drain, stop mirror, credential rotation) with scoped I/O.
"""

import asyncio
import json
from io import StringIO
import logging

import pytest

from src.models.enums import ExecutionStatus
from src.sdk.context import Caller, ExecutionContext, Organization
from src.sdk.decorators import service
from src.services.execution.engine import (
    ExecutionRequest,
    ServiceRunConfig,
    _execute_service_with_trace,
    _service_supervisor,
    execute,
)

def _caller():
    return Caller(
        user_id="00000000-0000-0000-0000-000000000001",
        email="service-aaaaaaaaaaaa@bifrost.internal",
        name="service-aaaaaaaaaaaa",
    )


def _org():
    return Organization(
        id="22222222-2222-4222-8222-222222222222",
        name="Acme",
        is_active=True,
    )


def _config(**overrides):
    args = {
        "service_id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
        "attempt_id": "11111111-1111-4111-8111-111111111111",
        "lease_token": "lease-token",
        "revision": "abc1234",
        "token": "initial-token",
        "token_expires_at": "2026-09-20T08:15:00+00:00",
    }
    args.update(overrides)
    return ServiceRunConfig(**args)


def _request(func, cfg=None):
    return ExecutionRequest(
        execution_id="11111111-1111-4111-8111-111111111111",
        caller=_caller(),
        organization=_org(),
        func=func,
        name="telegram_bridge",
        service=cfg or _config(),
    )


class _RuntimeControlResponse:
    status_code = 200

    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


class _RuntimeControlTransport:
    """Attempt-scoped fake for the engine-local authenticated transport."""

    def __init__(self):
        self.calls = []
        self.logs = []
        self.responses = []
        self.installed_tokens = []
        self.stop_after_request = None

    async def engine_request(self, method, path, **kwargs):
        self.calls.append((method, path, kwargs))
        payload = self.responses.pop(0) if self.responses else {
            "stop_requested": False,
            "access_token": None,
            "token_expires_at": "",
        }
        if self.stop_after_request is not None:
            self.stop_after_request.set()
        return _RuntimeControlResponse(payload)

    def write_log(self, level, message):
        self.logs.append((level, message))
        return "service-log"


@pytest.fixture(autouse=True)
def runtime_io(monkeypatch):
    """Keep service execution/logging inside the authenticated runtime seam."""
    transport = _RuntimeControlTransport()
    monkeypatch.setattr("bifrost.client.get_client", lambda: transport)
    monkeypatch.setattr(
        "bifrost.client.raise_for_status_with_detail", lambda response: None
    )
    monkeypatch.setattr(
        "bifrost._runtime_io.write_log", transport.write_log
    )
    monkeypatch.setattr(
        "src.services.execution.engine.write_log",
        transport.write_log,
    )
    monkeypatch.setattr(
        "bifrost._service_runtime.install_service_credentials",
        transport.installed_tokens.append,
    )
    return transport


async def test_clean_return_is_success_without_variables():
    @service
    async def bridge():
        return {"bridged": 3}

    result = await execute(_request(bridge))
    assert result.status == ExecutionStatus.SUCCESS
    assert result.result == {"bridged": 3}
    assert result.variables is None
    assert result.execution_context is None


async def test_context_first_param_supported():
    seen = {}

    @service
    async def bridge(context: ExecutionContext):
        seen["org"] = context.organization.id
        seen["admin"] = context.is_platform_admin

    result = await execute(_request(bridge))
    assert result.status == ExecutionStatus.SUCCESS
    assert seen == {"org": "22222222-2222-4222-8222-222222222222", "admin": False}


async def test_rejects_non_service_function():
    from src.sdk.decorators import workflow

    @workflow
    async def not_a_service():
        return None

    try:
        await execute(_request(not_a_service))
    except ValueError as e:
        assert "@service" in str(e)
    else:
        raise AssertionError("expected ValueError for non-service function")


async def test_exception_is_failed_without_variables():
    @service
    async def bridge():
        raise RuntimeError("broker refused")

    result = await execute(_request(bridge))
    assert result.status == ExecutionStatus.FAILED
    assert result.error_type == "RuntimeError"
    assert result.variables is None


async def test_exception_message_redacts_fetched_secrets():
    @service
    async def bridge(context: ExecutionContext):
        context._register_dynamic_secret("s3cr3t-service-token-value")
        raise RuntimeError("auth failed for s3cr3t-service-token-value")

    result = await execute(_request(bridge))
    assert result.status == ExecutionStatus.FAILED
    assert "s3cr3t-service-token-value" not in (result.error_message or "")
    assert "s3cr3t-service-token-value" not in json.dumps(result.logs)


async def test_exception_header_is_redacted_after_service_capture_closes():
    @service
    async def bridge():
        raise RuntimeError("Set-Cookie: session=service-exception-secret; Path=/")

    stream = StringIO()
    console = logging.StreamHandler(stream)
    console.setFormatter(logging.Formatter("[worker] %(levelname)s - %(message)s"))
    root = logging.getLogger()
    root.addHandler(console)
    try:
        result = await execute(_request(bridge))
    finally:
        root.removeHandler(console)

    assert result.status == ExecutionStatus.FAILED
    assert "service-exception-secret" not in (result.error_message or "")
    assert "service-exception-secret" not in stream.getvalue()
    assert "[REDACTED]" in (result.error_message or "")


async def test_stop_control_unwinds_to_success(runtime_io):
    cfg = _config()

    @service
    async def bridge():
        from bifrost import service as svc

        await svc.wait_until_stopping()
        return "unreachable"

    runtime_io.responses.append(
        {
            "stop_requested": True,
            "access_token": None,
            "token_expires_at": cfg.token_expires_at,
        }
    )
    result = await execute(_request(bridge, cfg))

    assert result.status == ExecutionStatus.SUCCESS
    assert result.result is None
    assert runtime_io.calls[0] == (
        "POST",
        "/api/sdk/runtime/service-control",
        {"json": {"ready": False, "token_expires_at": cfg.token_expires_at}},
    )


async def test_service_logs_captured_locally(runtime_io):
    @service
    async def bridge():
        logging.getLogger(__name__).info("bridged one message")

    result = await execute(_request(bridge))
    assert result.status == ExecutionStatus.SUCCESS
    assert any(
        entry["message"] == "bridged one message" for entry in result.logs
    )
    assert ("INFO", "bridged one message") in runtime_io.logs


def _bare_context(cfg):
    return ExecutionContext(
        user_id="u",
        email="e",
        name="n",
        scope="s",
        organization=None,
        is_platform_admin=False,
        is_function_key=False,
        execution_id=cfg.attempt_id,
    )


async def test_trace_returns_clean_and_cancelled_runs():
    cfg = _config()

    @service
    async def bridge():
        return "done"

    stop = asyncio.Event()
    result, _, stopped = await _execute_service_with_trace(
        bridge, _bare_context(cfg), {}, cfg, stop
    )
    assert (result, stopped) == ("done", False)

    @service
    async def hanging():
        await asyncio.sleep(60)

    stop.set()
    _, _, stopped = await _execute_service_with_trace(
        hanging, _bare_context(cfg), {}, cfg, stop
    )
    assert stopped is True


async def test_supervisor_drains_ready_and_mirrors_stop(runtime_io):
    from bifrost import service as svc
    from bifrost._service_runtime import (
        clear_service_runtime,
        install_service_runtime,
    )

    cfg = _config()
    stop = asyncio.Event()
    install_service_runtime(stop)
    try:
        await svc.ready()
        runtime_io.responses.append(
            {
                "stop_requested": True,
                "access_token": None,
                "token_expires_at": cfg.token_expires_at,
            }
        )
        await _service_supervisor(cfg, stop)
        assert stop.is_set()
        assert runtime_io.calls == [
            (
                "POST",
                "/api/sdk/runtime/service-control",
                {"json": {"ready": True, "token_expires_at": cfg.token_expires_at}},
            )
        ]
    finally:
        clear_service_runtime()


async def test_supervisor_installs_fresher_rotation_token(runtime_io):
    from bifrost._service_runtime import (
        clear_service_runtime,
        install_service_runtime,
    )

    cfg = _config()
    stop = asyncio.Event()
    install_service_runtime(stop)
    runtime_io.responses.append(
        {
            "stop_requested": False,
            "access_token": "rotated-token",
            "token_expires_at": "2026-09-20T08:30:00+00:00",
        }
    )
    runtime_io.stop_after_request = stop
    try:
        await _service_supervisor(cfg, stop)
    finally:
        clear_service_runtime()
    assert runtime_io.installed_tokens == ["rotated-token"]
    assert cfg.token_expires_at == "2026-09-20T08:30:00+00:00"
