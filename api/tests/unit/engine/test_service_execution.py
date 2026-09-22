"""Unit tests for engine service mode (Slice 3).

Covers: clean return, exception, stop-request unwind, context injection,
@service type enforcement, log capture, and the Redis-backed supervisor
(ready drain, stop mirror, credential rotation) with faked Redis.
"""

import asyncio
import json
import logging
import os
from contextlib import asynccontextmanager
from unittest.mock import patch

import pytest

from src.core.cache.keys import (
    service_ready_key,
    service_stop_key,
    service_token_key,
)
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

_SERVICE_ENV_VARS = (
    "BIFROST_API_URL",
    "BIFROST_ACCESS_TOKEN",
    "BIFROST_REFRESH_TOKEN",
)


@pytest.fixture(autouse=True)
def _isolated_service_env():
    """Contain install_service_credentials' process env (no teardown).

    The supervisor installs service tokens as process-global env vars
    (scoped to the child process in production); in-process that leaks
    phantom credentials into every later suite (SDK "without context"
    tests, URL assertions). Snapshot explicitly: monkeypatch.delenv on
    an absent var records nothing, so writes made after the call are
    never undone.
    """
    saved = {var: os.environ.get(var) for var in _SERVICE_ENV_VARS}
    for var in _SERVICE_ENV_VARS:
        os.environ.pop(var, None)
    yield
    for var in _SERVICE_ENV_VARS:
        if saved[var] is None:
            os.environ.pop(var, None)
        else:
            os.environ[var] = saved[var]


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


class FakeRedis:
    """Minimal async Redis double backed by a dict."""

    def __init__(self, values=None):
        self.values = dict(values or {})

    async def setex(self, key, ttl, value):
        self.values[key] = value

    async def get(self, key):
        return self.values.get(key)


@asynccontextmanager
async def _fake_redis(values=None):
    yield FakeRedis(values)


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


async def test_stop_key_unwinds_to_success():
    cfg = _config()

    @service
    async def bridge():
        from bifrost import service as svc

        await svc.wait_until_stopping()
        return "unreachable"

    from src.core.cache import get_redis

    task = asyncio.ensure_future(execute(_request(bridge, cfg)))
    try:
        async with get_redis() as r:
            await r.set(service_stop_key(cfg.attempt_id), "1", ex=30)
        result = await asyncio.wait_for(task, timeout=15.0)
    finally:
        if not task.done():
            task.cancel()
        async with get_redis() as r:
            await r.delete(service_stop_key(cfg.attempt_id))
    assert result.status == ExecutionStatus.SUCCESS
    assert result.result is None


async def test_service_logs_captured_locally():
    @service
    async def bridge():
        logging.getLogger(__name__).info("bridged one message")

    result = await execute(_request(bridge))
    assert result.status == ExecutionStatus.SUCCESS
    assert any(
        entry["message"] == "bridged one message" for entry in result.logs
    )


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


async def test_supervisor_drains_ready_and_mirrors_stop():
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
        redis = FakeRedis({service_stop_key(cfg.attempt_id): "1"})
        with patch(
            "src.core.cache.get_redis", return_value=_fake_redis_values(redis)
        ):
            await _service_supervisor(cfg, stop)
        assert stop.is_set()
        assert redis.values.get(service_ready_key(cfg.attempt_id)) == "1"
    finally:
        clear_service_runtime()


@asynccontextmanager
async def _fake_redis_values(redis):
    yield redis


async def test_supervisor_installs_fresher_rotation_token():
    from bifrost._service_runtime import (
        clear_service_runtime,
        install_service_runtime,
    )

    cfg = _config()
    stop = asyncio.Event()
    install_service_runtime(stop)
    installed = {}

    def _record(token):
        installed["token"] = token
        os.environ["BIFROST_ACCESS_TOKEN"] = token
        return True

    async def _stop_soon():
        await asyncio.sleep(0.2)
        stop.set()

    redis = FakeRedis(
        {
            service_token_key(cfg.attempt_id): json.dumps(
                {
                    "token": "rotated-token",
                    "expires_at": "2026-09-20T08:30:00+00:00",
                }
            )
        }
    )
    stopper = asyncio.ensure_future(_stop_soon())
    previous = os.environ.get("BIFROST_ACCESS_TOKEN")
    try:
        with (
            patch(
                "src.core.cache.get_redis",
                return_value=_fake_redis_values(redis),
            ),
            patch(
                "bifrost._service_runtime.install_service_credentials",
                side_effect=_record,
            ),
        ):
            await _service_supervisor(cfg, stop)
    finally:
        stopper.cancel()
        clear_service_runtime()
        if previous is None:
            os.environ.pop("BIFROST_ACCESS_TOKEN", None)
        else:
            os.environ["BIFROST_ACCESS_TOKEN"] = previous
    assert installed.get("token") == "rotated-token"
    assert cfg.token_expires_at == "2026-09-20T08:30:00+00:00"
