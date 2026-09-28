"""Unit tests for the service supervision SDK namespace."""

import asyncio

import pytest

from bifrost import service
from bifrost.client import get_client
from bifrost._service_runtime import (
    clear_service_runtime,
    install_service_credentials,
    install_service_runtime,
    take_ready_report,
)


@pytest.fixture(autouse=True)
def clean_runtime():
    clear_service_runtime()
    yield
    clear_service_runtime()


def test_supervision_callables_live_on_the_decorator_namespace():
    assert callable(service.ready)
    assert callable(service.is_stopping)
    assert callable(service.wait_until_stopping)


def test_is_stopping_false_outside_service_execution():
    assert service.is_stopping() is False


@pytest.mark.asyncio
async def test_ready_and_stop_lifecycle():
    stop = asyncio.Event()
    install_service_runtime(stop)

    assert service.is_stopping() is False
    await service.ready()
    assert take_ready_report() is True
    assert take_ready_report() is False

    waiter = asyncio.ensure_future(service.wait_until_stopping())
    await asyncio.sleep(0)
    assert not waiter.done()
    stop.set()
    await asyncio.wait_for(waiter, timeout=1.0)
    assert service.is_stopping() is True


@pytest.mark.asyncio
async def test_ready_outside_service_execution_raises():
    with pytest.raises(RuntimeError, match="@service"):
        await service.ready()


@pytest.mark.asyncio
async def test_wait_outside_service_execution_raises():
    with pytest.raises(RuntimeError, match="@service"):
        await service.wait_until_stopping()


def test_rotated_service_credentials_update_cached_sdk_client(monkeypatch):
    from bifrost.client import (
        _clear_engine_socket,
        _install_engine_socket,
        _thread_local,
    )

    monkeypatch.setenv("BIFROST_API_URL", "http://service-test.invalid")
    previous = getattr(_thread_local, "bifrost_client", None)
    _thread_local.bifrost_client = None
    _install_engine_socket("/tmp/service-test.sock")
    try:
        assert install_service_credentials("initial-token")
        client = get_client()
        assert client._get_engine_async_client().headers["Authorization"] == "Bearer initial-token"

        assert install_service_credentials("rotated-token")
        assert get_client() is client
        assert client._get_engine_async_client().headers["Authorization"] == "Bearer rotated-token"
    finally:
        _clear_engine_socket()
        _thread_local.bifrost_client = previous
