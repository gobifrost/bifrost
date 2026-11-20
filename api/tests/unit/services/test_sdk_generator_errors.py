"""Generated endpoint errors retain status without exposing vendor text."""

from types import SimpleNamespace
import traceback
from unittest.mock import Mock

import pytest
import requests

from src.services.sdk_generator import generate_sdk


@pytest.fixture
def generated():
    spec = {
        "openapi": "3.0.0",
        "info": {"title": "Errors", "version": "1.0.0"},
        "paths": {"/items": {"get": {"responses": {"200": {"description": "OK"}}}}},
    }
    code, _ = generate_sdk(spec, "test", "bearer")
    namespace = {}
    exec(compile(code, "generated_errors_sdk.py", "exec"), namespace)
    return SimpleNamespace(**namespace)


def client_for(generated, status, body):
    response = requests.Response()
    response.status_code = status
    response._content = body
    # HTTPError includes the vendor-controlled reason and URL by default.
    response.reason = "VENDOR_SENTINEL"
    response.url = "https://example.invalid/VENDOR_SENTINEL"
    session = SimpleNamespace(request=Mock(return_value=response))
    return generated._ErrorsClient("https://example.invalid", session, max_retries=0)


@pytest.mark.parametrize("status", [401, 500])
@pytest.mark.parametrize("body", [b'{"detail":"VENDOR_SENTINEL"}', b'<html>VENDOR_SENTINEL</html>', b''])
def test_http_error_keeps_status_without_vendor_text(generated, status, body):
    client = client_for(generated, status, body)
    with pytest.raises(generated.SDKError) as caught:
        client.list_items()

    error = caught.value
    assert str(error) == f"HTTP {status}"
    assert error.status_code == status
    assert error.response_body is None
    assert "VENDOR_SENTINEL" not in repr(error)
    assert "VENDOR_SENTINEL" not in "".join(traceback.format_exception(error))


def test_invalid_json_keeps_status_without_vendor_text(generated):
    client = client_for(generated, 200, b'<html>VENDOR_SENTINEL</html>')
    with pytest.raises(generated.SDKError) as caught:
        client.list_items()

    error = caught.value
    assert str(error) == "Invalid JSON response (HTTP 200)"
    assert error.status_code == 200
    assert error.response_body is None
    assert "VENDOR_SENTINEL" not in repr(error)
    assert "VENDOR_SENTINEL" not in "".join(traceback.format_exception(error))


def test_empty_success_returns_none(generated):
    assert client_for(generated, 204, b'').list_items() is None


def test_json_success_retains_dot_access(generated):
    assert client_for(generated, 200, b'{"id":"item-1"}').list_items().id == "item-1"
