"""
Unit tests for webhook adapter authentication.

Tests HMAC-SHA256 signature verification and the GenericWebhookAdapter
request handling logic.
"""

import hashlib
import hmac
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from src.services.webhooks.adapters.generic import GenericWebhookAdapter
from src.services.webhooks.adapters.local_fixture import LocalFixtureWebhookAdapter
from src.services.webhooks import registry as webhook_registry
from src.services.webhooks.protocol import Deliver, Rejected, WebhookAdapter, WebhookRequest


def _sign(body: bytes, secret: str, prefix: str = "sha256=") -> str:
    """Helper to compute HMAC-SHA256 signature."""
    sig = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return f"{prefix}{sig}"


def _make_request(
    body: bytes = b'{"event": "test"}',
    headers: dict | None = None,
) -> WebhookRequest:
    """Helper to build a WebhookRequest."""
    return WebhookRequest(
        method="POST",
        path="/webhook/test",
        headers=headers or {},
        body=body,
        query_params={},
    )


@pytest.mark.asyncio
async def test_local_fixture_adapter_renews_deterministically():
    adapter = LocalFixtureWebhookAdapter()

    result = await adapter.renew(
        external_id="local-scheduler-fixture",
        state={"renewal_count": 2},
        integration=None,
    )

    assert result is not None
    assert result.state == {"renewal_count": 3}
    assert result.expires_at is not None
    assert result.expires_at > datetime.now(timezone.utc)


@pytest.mark.asyncio
async def test_local_fixture_adapter_rejects_unknown_subscription():
    adapter = LocalFixtureWebhookAdapter()

    result = await adapter.renew(
        external_id="not-the-fixture",
        state={},
        integration=None,
    )

    assert result is None


def test_local_fixture_adapter_is_registered_only_outside_production(monkeypatch):
    monkeypatch.setattr(
        webhook_registry,
        "get_settings",
        lambda: SimpleNamespace(environment="development"),
    )
    assert webhook_registry.AdapterRegistry().get("local_fixture") is not None

    monkeypatch.setattr(
        webhook_registry,
        "get_settings",
        lambda: SimpleNamespace(environment="production"),
    )
    assert webhook_registry.AdapterRegistry().get("local_fixture") is None


# =============================================================================
# TestVerifyHmacSha256 - WebhookAdapter.verify_hmac_sha256()
# =============================================================================


class TestVerifyHmacSha256:
    """Tests for the static verify_hmac_sha256 helper."""

    def test_valid_signature(self):
        body = b"hello world"
        secret = "mysecret"
        sig = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()

        assert WebhookAdapter.verify_hmac_sha256(body, secret, sig) is True

    def test_invalid_signature(self):
        body = b"hello world"
        secret = "mysecret"

        assert WebhookAdapter.verify_hmac_sha256(body, secret, "bad") is False

    def test_prefix_stripping(self):
        body = b"hello world"
        secret = "mysecret"
        sig = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()

        assert (
            WebhookAdapter.verify_hmac_sha256(
                body, secret, f"sha256={sig}", prefix="sha256="
            )
            is True
        )

    def test_empty_prefix(self):
        body = b"hello world"
        secret = "mysecret"
        sig = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()

        assert (
            WebhookAdapter.verify_hmac_sha256(body, secret, sig, prefix="")
            is True
        )

    def test_none_signature_returns_false(self):
        assert (
            WebhookAdapter.verify_hmac_sha256(b"body", "secret", None) is False
        )


# =============================================================================
# TestGenericWebhookAdapterHandleRequest
# =============================================================================


class TestGenericWebhookAdapterHandleRequest:
    """Tests for GenericWebhookAdapter.handle_request()."""

    @pytest.fixture
    def adapter(self):
        return GenericWebhookAdapter()

    @pytest.mark.asyncio
    async def test_no_secret_accepts_any_request(self, adapter):
        """No secret in state → delivers without checking signature."""
        request = _make_request()
        result = await adapter.handle_request(request, config={}, state={})

        assert isinstance(result, Deliver)

    @pytest.mark.asyncio
    async def test_valid_signature_accepted(self, adapter):
        """Valid HMAC signature → Deliver."""
        body = b'{"event": "push"}'
        secret = "test-secret"
        sig = _sign(body, secret)

        request = _make_request(
            body=body,
            headers={"x-signature-256": sig},
        )
        result = await adapter.handle_request(
            request, config={}, state={"secret": secret}
        )

        assert isinstance(result, Deliver)

    @pytest.mark.asyncio
    async def test_missing_signature_rejected(self, adapter):
        """Secret set but no signature header → Rejected(401)."""
        request = _make_request(headers={})
        result = await adapter.handle_request(
            request, config={}, state={"secret": "mysecret"}
        )

        assert isinstance(result, Rejected)
        assert result.status_code == 401

    @pytest.mark.asyncio
    async def test_invalid_signature_rejected(self, adapter):
        """Bad HMAC → Rejected(401)."""
        request = _make_request(
            headers={"x-signature-256": "sha256=badhash"},
        )
        result = await adapter.handle_request(
            request, config={}, state={"secret": "mysecret"}
        )

        assert isinstance(result, Rejected)
        assert result.status_code == 401

    @pytest.mark.asyncio
    async def test_custom_signature_header(self, adapter):
        """Reads signature from custom header name."""
        body = b'{"data": 1}'
        secret = "s3cret"
        sig = _sign(body, secret)

        request = _make_request(
            body=body,
            headers={"x-hub-signature-256": sig},
        )
        result = await adapter.handle_request(
            request,
            config={"signature_header": "X-Hub-Signature-256"},
            state={"secret": secret},
        )

        assert isinstance(result, Deliver)

    @pytest.mark.asyncio
    async def test_custom_signature_prefix(self, adapter):
        """Handles different prefix."""
        body = b'{"data": 1}'
        secret = "s3cret"
        sig = _sign(body, secret, prefix="hmac-sha256=")

        request = _make_request(
            body=body,
            headers={"x-signature-256": sig},
        )
        result = await adapter.handle_request(
            request,
            config={"signature_prefix": "hmac-sha256="},
            state={"secret": secret},
        )

        assert isinstance(result, Deliver)

    @pytest.mark.asyncio
    async def test_event_type_from_header(self, adapter):
        """Extracts event type from header."""
        request = _make_request(
            headers={"x-event-type": "push"},
        )
        result = await adapter.handle_request(
            request,
            config={"event_type_header": "X-Event-Type"},
            state={},
        )

        assert isinstance(result, Deliver)
        assert result.event_type == "push"

    @pytest.mark.asyncio
    async def test_event_type_from_payload_field(self, adapter):
        """Extracts event type from JSON field."""
        request = _make_request(
            body=b'{"type": "invoice.paid"}',
        )
        result = await adapter.handle_request(
            request,
            config={"event_type_field": "type"},
            state={},
        )

        assert isinstance(result, Deliver)
        assert result.event_type == "invoice.paid"

    @pytest.mark.asyncio
    async def test_event_type_field_overrides_header(self, adapter):
        """Payload field takes precedence over header."""
        request = _make_request(
            body=b'{"type": "from_field"}',
            headers={"x-event-type": "from_header"},
        )
        result = await adapter.handle_request(
            request,
            config={
                "event_type_header": "X-Event-Type",
                "event_type_field": "type",
            },
            state={},
        )

        assert isinstance(result, Deliver)
        assert result.event_type == "from_field"


# =============================================================================
# TestGenericWebhookAdapterSubscribe
# =============================================================================


class TestGenericWebhookAdapterSubscribe:
    """Tests for GenericWebhookAdapter.subscribe()."""

    @pytest.fixture
    def adapter(self):
        return GenericWebhookAdapter()

    @pytest.mark.asyncio
    async def test_subscribe_stores_secret_in_state(self, adapter):
        result = await adapter.subscribe(
            callback_url="https://example.com/webhook",
            config={"secret": "my-secret-key"},
            integration=None,
        )

        assert result.state["secret"] == "my-secret-key"

    @pytest.mark.asyncio
    async def test_subscribe_without_secret_empty_state(self, adapter):
        result = await adapter.subscribe(
            callback_url="https://example.com/webhook",
            config={},
            integration=None,
        )

        assert result.state == {}
