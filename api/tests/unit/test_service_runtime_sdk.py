"""Unit tests for the service supervision SDK namespace."""

import asyncio

import pytest

from bifrost import service
from bifrost.client import get_client
from bifrost._service_runtime import (
    clear_service_runtime,
    install_legacy_service_token,
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
    # install_legacy_service_token mutates both variables directly; register
    # them with monkeypatch so later SDK tests see their original environment.
    monkeypatch.setenv("BIFROST_ACCESS_TOKEN", "test-placeholder")
    monkeypatch.setenv("BIFROST_REFRESH_TOKEN", "test-placeholder")
    previous = getattr(_thread_local, "bifrost_client", None)
    _thread_local.bifrost_client = None
    _install_engine_socket("/tmp/service-test.sock")
    try:
        assert install_legacy_service_token("initial-token")
        assert getattr(_thread_local, "bifrost_client", None) is None
        client = get_client()
        assert client._get_engine_async_client().headers["Authorization"] == "Bearer initial-token"

        assert install_legacy_service_token("rotated-token")
        assert get_client() is client
        assert client._get_engine_async_client().headers["Authorization"] == "Bearer rotated-token"
    finally:
        _clear_engine_socket()
        _thread_local.bifrost_client = previous


def test_a_claims_attempt_installs_an_access_token_and_its_run_refresh_token(monkeypatch):
    from bifrost.credentials import resolve_credentials

    monkeypatch.setenv("BIFROST_API_URL", "http://service-test.invalid")
    monkeypatch.setenv("BIFROST_ACCESS_TOKEN", "test-placeholder")
    monkeypatch.setenv("BIFROST_REFRESH_TOKEN", "test-placeholder")

    assert install_service_credentials("claims-access", "run-refresh")
    assert not install_service_credentials("claims-access", "")

    resolved = resolve_credentials("http://service-test.invalid")
    assert resolved is not None
    assert (resolved.credentials.access_token, resolved.credentials.refresh_token) == ("claims-access", "run-refresh")


def test_a_rotated_legacy_token_never_replaces_a_run_refresh_token(monkeypatch):
    """After a rollback, an older parent rotates legacy tokens into a
    claims-format attempt: only the access slot may change, so the attempt
    can still refresh once the newer API is back."""
    from bifrost.credentials import resolve_credentials

    monkeypatch.setenv("BIFROST_API_URL", "http://service-test.invalid")
    monkeypatch.setenv("BIFROST_ACCESS_TOKEN", "test-placeholder")
    monkeypatch.setenv("BIFROST_REFRESH_TOKEN", "test-placeholder")
    try:
        assert install_service_credentials("claims-access", "run-refresh")
        assert install_legacy_service_token("legacy-rotated")

        resolved = resolve_credentials("http://service-test.invalid")
        assert resolved is not None
        assert (resolved.credentials.access_token, resolved.credentials.refresh_token) == (
            "legacy-rotated",
            "run-refresh",
        )
    finally:
        clear_service_runtime()
