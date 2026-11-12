"""Retry floors in rendered integration SDKs, without network or real sleeps."""

import asyncio
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from src.services.sdk_generator import generate_sdk


@pytest.fixture
def generated():
    spec = {
        "openapi": "3.0.0",
        "info": {"title": "Retry", "version": "1.0.0"},
        "paths": {"/tickets": {"get": {
            "operationId": "get_tickets",
            "responses": {"200": {"description": "OK"}},
        }}},
    }
    code, _ = generate_sdk(spec, "test", "bearer")
    namespace = {}
    exec(compile(code, "generated_retry_sdk.py", "exec"), namespace)
    return SimpleNamespace(**namespace)


@pytest.mark.parametrize("entry", ["direct", "lazy"])
@pytest.mark.parametrize(
    ("header", "expected_waits", "calls"),
    [
        ("300", [], 1),
        ("12", [12, 12, 12], 4),
        ("60", [60, 60, 60], 4),
        ("1.5", [1.5, 1.5, 1.5], 4),
        ("0", [0, 0, 0], 4),
        ("-1", [0, 0, 0], 4),
        ("Thu, 01 Jan 1970 00:00:12 GMT", [12, 12, 12], 4),
        ("Thu, 01 Jan 1970 00:05:00 GMT", [], 1),
        ("Wed, 31 Dec 1969 23:59:59 GMT", [0, 0, 0], 4),
        ("invalid", [1, 2, 4], 4),
        ("nan", [1, 2, 4], 4),
        ("inf", [1, 2, 4], 4),
        (None, [1, 2, 4], 4),
    ],
)
def test_retry_floor(generated, monkeypatch, entry, header, expected_waits, calls):
    response = SimpleNamespace(
        status_code=429, headers={} if header is None else {"Retry-After": header}
    )
    session = SimpleNamespace(request=Mock(return_value=response))
    client = generated._RetryClient("https://example.invalid", session)
    waits = []
    monkeypatch.setattr(generated.time, "sleep", waits.append)
    monkeypatch.setattr(generated.time, "time", lambda: 0)
    if entry == "direct":
        result = client._request_with_retry("GET", "/tickets")
    else:

        async def get_client():
            return client

        monkeypatch.setattr(generated._lazy, "_ensure_client", get_client)
        result = asyncio.run(generated._lazy._request_with_retry("GET", "/tickets"))
    assert result is response
    assert waits == expected_waits
    assert session.request.call_count == calls


@pytest.mark.parametrize("status", [200, 401, 403, 404, 500, 503])
def test_status_and_retry_count_unchanged(generated, monkeypatch, status):
    response = SimpleNamespace(status_code=status, headers={})
    session = SimpleNamespace(request=Mock(return_value=response))
    client = generated._RetryClient("https://example.invalid", session)
    waits = []
    monkeypatch.setattr(generated.time, "sleep", waits.append)
    assert client._request_with_retry("GET", "/tickets") is response
    assert waits == ([1, 2, 4] if status >= 500 else [])
    assert session.request.call_count == (4 if status >= 500 else 1)


def test_zero_retries_never_sleeps(generated, monkeypatch):
    response = SimpleNamespace(status_code=429, headers={"Retry-After": "12"})
    session = SimpleNamespace(request=Mock(return_value=response))
    client = generated._RetryClient("https://example.invalid", session, max_retries=0)
    sleep = Mock()
    monkeypatch.setattr(generated.time, "sleep", sleep)
    assert client._request_with_retry("GET", "/tickets") is response
    assert session.request.call_count == 1
    sleep.assert_not_called()


@pytest.mark.parametrize("entry", ["direct", "lazy"])
@pytest.mark.parametrize("header", ["12", "300"])
def test_generated_endpoint_preserves_result_or_terminal_error(
    generated, monkeypatch, entry, header
):
    response = generated.requests.Response()
    response.status_code = 429
    response.headers["Retry-After"] = header
    response._content = b'{"error": "rate limited"}'
    success = generated.requests.Response()
    success.status_code = 200
    success._content = b'{"id": "ticket-1"}'
    session = SimpleNamespace(request=Mock(side_effect=[response, success]))
    client = generated._RetryClient("https://example.invalid", session)
    waits = []
    monkeypatch.setattr(generated.time, "sleep", waits.append)

    async def get_client():
        return client

    monkeypatch.setattr(generated._lazy, "_ensure_client", get_client)

    def call():
        if entry == "direct":
            return client.list_tickets()
        return asyncio.run(generated._lazy.list_tickets())

    if header == "300":
        with pytest.raises(generated.SDKError) as exc:
            call()
        assert exc.value.status_code == 429
        assert session.request.call_count == 1
        assert waits == []
    else:
        assert call().id == "ticket-1"
        assert session.request.call_count == 2
        assert waits == [12]
    assert session.request.call_args_list[0].args == (
        "GET", "https://example.invalid/tickets"
    )


@pytest.mark.parametrize(("header", "calls", "waits"), [("6", 1, []), ("5", 4, [5, 5, 5])])
def test_custom_wait_budget(generated, monkeypatch, header, calls, waits):
    response = SimpleNamespace(status_code=429, headers={"Retry-After": header})
    session = SimpleNamespace(request=Mock(return_value=response))
    client = generated._RetryClient("https://example.invalid", session, max_backoff=5)
    sleep = Mock()
    monkeypatch.setattr(generated.time, "sleep", sleep)
    assert client._request_with_retry("GET", "/tickets") is response
    assert session.request.call_count == calls
    assert [call.args[0] for call in sleep.call_args_list] == waits
