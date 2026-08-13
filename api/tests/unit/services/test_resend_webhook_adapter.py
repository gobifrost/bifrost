"""Tests for Resend webhook signature verification and normalization."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time

import pytest

from src.services.webhooks.adapters.resend import ResendWebhookAdapter
from src.services.webhooks.protocol import Deliver, Rejected, WebhookRequest
from src.services.webhooks.registry import AdapterRegistry


SECRET_BYTES = b"a-32-byte-test-secret-for-resend!"
SIGNING_SECRET = "whsec_" + base64.b64encode(SECRET_BYTES).decode()


def _request(
    payload: dict | bytes,
    *,
    timestamp: int | None = None,
    signatures: list[str] | None = None,
) -> WebhookRequest:
    body = payload if isinstance(payload, bytes) else json.dumps(payload, separators=(",", ":")).encode()
    message_id = "msg_test_123"
    timestamp_text = str(timestamp if timestamp is not None else int(time.time()))
    signed_payload = f"{message_id}.{timestamp_text}.".encode() + body
    valid = base64.b64encode(
        hmac.new(SECRET_BYTES, signed_payload, hashlib.sha256).digest()
    ).decode()
    signature_values = signatures or [f"v1,{valid}"]
    return WebhookRequest(
        method="POST",
        path="/api/hooks/test",
        headers={
            "svix-id": message_id,
            "svix-timestamp": timestamp_text,
            "svix-signature": " ".join(signature_values),
        },
        query_params={},
        body=body,
    )


@pytest.fixture
def adapter() -> ResendWebhookAdapter:
    return ResendWebhookAdapter()


@pytest.mark.asyncio
async def test_subscribe_validates_and_stores_signing_secret(adapter):
    result = await adapter.subscribe(
        callback_url="https://example.test/api/hooks/id",
        config={"signing_secret": SIGNING_SECRET},
        integration=None,
    )

    assert result.state == {"signing_secret": SIGNING_SECRET}


@pytest.mark.asyncio
async def test_subscribe_without_secret_provisions_callback_first(adapter):
    result = await adapter.subscribe(
        callback_url="https://example.test/api/hooks/id",
        config={},
        integration=None,
    )

    assert result.state == {}


@pytest.mark.asyncio
async def test_configure_stores_secret_after_callback_provisioning(adapter):
    state = await adapter.configure(
        config={"signing_secret": SIGNING_SECRET},
        state={},
    )

    assert state == {"signing_secret": SIGNING_SECRET}


@pytest.mark.asyncio
async def test_configure_rejects_invalid_secret(adapter):
    with pytest.raises(ValueError, match="must start"):
        await adapter.configure(
            config={"signing_secret": "not-a-resend-secret"},
            state={},
        )


@pytest.mark.asyncio
async def test_valid_email_received_delivery_is_normalized(adapter):
    payload = {"type": "email.received", "data": {"email_id": "email_123"}}

    result = await adapter.handle_request(
        _request(payload),
        config={},
        state={"signing_secret": SIGNING_SECRET},
    )

    assert isinstance(result, Deliver)
    assert result.event_type == "email.received"
    assert result.data == payload
    assert result.raw_headers["svix-id"] == "msg_test_123"


@pytest.mark.asyncio
async def test_any_valid_v1_signature_is_accepted_during_rotation(adapter):
    payload = {"type": "email.received", "data": {"email_id": "email_123"}}
    request = _request(payload)
    valid_signature = request.headers["svix-signature"]
    request.headers["svix-signature"] = f"v1,{base64.b64encode(b'wrong').decode()} {valid_signature}"

    result = await adapter.handle_request(
        request,
        config={},
        state={"signing_secret": SIGNING_SECRET},
    )

    assert isinstance(result, Deliver)


@pytest.mark.asyncio
async def test_tampered_body_is_rejected(adapter):
    request = _request({"type": "email.received", "data": {"email_id": "email_123"}})
    request.body = b'{"type":"email.received","data":{"email_id":"tampered"}}'

    result = await adapter.handle_request(
        request,
        config={},
        state={"signing_secret": SIGNING_SECRET},
    )

    assert isinstance(result, Rejected)
    assert result.status_code == 401


@pytest.mark.asyncio
async def test_stale_timestamp_is_rejected(adapter):
    result = await adapter.handle_request(
        _request({"type": "email.received"}, timestamp=int(time.time()) - 301),
        config={},
        state={"signing_secret": SIGNING_SECRET},
    )

    assert isinstance(result, Rejected)
    assert result.status_code == 401


@pytest.mark.asyncio
async def test_missing_signature_headers_are_rejected(adapter):
    request = _request({"type": "email.received"})
    request.headers.pop("svix-signature")

    result = await adapter.handle_request(
        request,
        config={},
        state={"signing_secret": SIGNING_SECRET},
    )

    assert isinstance(result, Rejected)
    assert result.status_code == 400


@pytest.mark.asyncio
async def test_non_json_payload_is_rejected_after_signature_verification(adapter):
    result = await adapter.handle_request(
        _request(b"not-json"),
        config={},
        state={"signing_secret": SIGNING_SECRET},
    )

    assert isinstance(result, Rejected)
    assert result.status_code == 400


def test_resend_adapter_is_registered():
    adapter = AdapterRegistry().get("resend")

    assert isinstance(adapter, ResendWebhookAdapter)
