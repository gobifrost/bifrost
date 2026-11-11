"""Static header credentials never enter event metadata."""

import pytest

from src.services.webhooks.adapters.generic import GenericWebhookAdapter
from src.services.webhooks.protocol import Deliver, Rejected, WebhookRequest
from src.services.webhooks.signing_secret import with_signing_secret


@pytest.mark.parametrize("header,token", [("X-Gitlab-Token", "gitlab-test"), ("Authorization", "Bearer grafana-test")])
async def test_header_token_accepts_only_exact_value_and_filters_credentials(header, token):
    adapter = GenericWebhookAdapter()
    config = {"header_auth": True, "auth_header": header}
    state = with_signing_secret({}, token)
    headers = {header.lower(): token, "cookie": "session=synthetic", "x-safe": "kept", "x-signature-256": "signature"}
    request = WebhookRequest("POST", "/", headers, {}, b'{ "ok": true }')
    result = await adapter.handle_request(request, config, state)
    assert isinstance(result, Deliver)
    assert result.raw_headers == {"x-safe": "kept", "x-signature-256": "signature"}
    for value in (None, "wrong"):
        if value is None:
            request.headers.pop(header.lower(), None)
        else:
            request.headers[header.lower()] = value
        rejected = await adapter.handle_request(request, config, state)
        assert isinstance(rejected, Rejected) and rejected.status_code == 401


async def test_header_auth_fails_closed_and_does_not_enable_signed_dedup():
    adapter = GenericWebhookAdapter()
    request = WebhookRequest("POST", "/", {"x-token": "secret", "x-id": "abc"}, {}, b'{}')
    state = with_signing_secret({}, "secret")
    for config in ({"header_auth": True}, {"header_auth": True, "auth_header": "bad header"},
                   {"header_auth": True, "auth_header": "X-Token", "event_type_header": "x-token"},
                   {"header_auth": True, "auth_header": "X-Token", "delivery_id_header": "X-ID"}):
        result = await adapter.handle_request(request, config, state)
        assert isinstance(result, Rejected) and result.status_code == 503


@pytest.mark.parametrize("header", ["X-Forwarded-For", "x-forwarded-for", "X-FORWARDED-FOR"])
async def test_forwarded_ip_header_cannot_be_a_credential(header):
    adapter = GenericWebhookAdapter()
    config = {"header_auth": True, "auth_header": header}
    assert adapter.validate_config(config) is not None
    request = WebhookRequest("POST", "/", {header.lower(): "synthetic-token"}, {}, b"{}", client_ip="synthetic-token")
    result = await adapter.handle_request(request, config, with_signing_secret({}, "synthetic-token"))
    assert isinstance(result, Rejected) and result.status_code == 503
