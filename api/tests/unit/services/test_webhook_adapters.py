"""
Unit tests for webhook adapter authentication.

Tests HMAC-SHA256 signature verification and the GenericWebhookAdapter
request handling logic.
"""

import hashlib
import hmac
import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from src.services.webhooks.adapters.generic import GenericWebhookAdapter
from src.services.webhooks.adapters.local_fixture import LocalFixtureWebhookAdapter
from src.services.webhooks.adapters.microsoft_bot_framework import (
    MicrosoftBotFrameworkAdapter,
)
from src.services.webhooks.adapters.microsoft_graph import MicrosoftGraphAdapter
from src.services.webhooks import registry as webhook_registry
from src.services.webhooks.protocol import (
    Deliver,
    Rejected,
    SubscribeResult,
    WebhookAdapter,
    WebhookIntegrationAuth,
    WebhookRequest,
)


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


class _GraphClient:
    """Small async client double that records Graph requests."""

    def __init__(self, response):
        self.response = response
        self.get = AsyncMock(return_value=response)
        self.post = AsyncMock(return_value=response)
        self.patch = AsyncMock(return_value=response)
        self.delete = AsyncMock(return_value=response)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None


def _graph_auth() -> WebhookIntegrationAuth:
    return WebhookIntegrationAuth(
        integration_id=uuid4(),
        organization_id=uuid4(),
        entity_id="tenant-covi",
        access_token="tenant-token",
    )


@pytest.mark.asyncio
async def test_graph_lists_users_with_resolved_tenant_token(monkeypatch):
    response = SimpleNamespace(
        status_code=200,
        json=lambda: {
            "value": [
                {
                    "id": "user-1",
                    "displayName": "Ada Lovelace",
                    "mail": "ada@example.com",
                    "userPrincipalName": "ada@example.com",
                }
            ]
        },
        text="",
    )
    client = _GraphClient(response)
    monkeypatch.setattr(
        "src.services.webhooks.adapters.microsoft_graph.httpx.AsyncClient",
        lambda: client,
    )

    users = await MicrosoftGraphAdapter().get_dynamic_values(
        "list_users",
        _graph_auth(),
        {},
    )

    assert users == [
        {
            "id": "user-1",
            "label": "ada@example.com · Ada Lovelace",
            "displayName": "Ada Lovelace",
            "userPrincipalName": "ada@example.com",
            "mail": "ada@example.com",
        }
    ]
    assert client.get.await_args.kwargs["headers"] == {
        "Authorization": "Bearer tenant-token"
    }


@pytest.mark.asyncio
async def test_graph_subscription_uses_public_callback_and_resolved_token(monkeypatch):
    user_response = SimpleNamespace(
        status_code=200,
        json=lambda: {
            "id": "user-1",
            "displayName": "Ada Lovelace",
            "mail": "ada@example.com",
            "userPrincipalName": "ada@example.com",
        },
        text="",
    )
    subscription_response = SimpleNamespace(
        status_code=201,
        json=lambda: {
            "id": "subscription-1",
            "expirationDateTime": "2026-08-30T12:00:00Z",
        },
        text="",
    )
    client = _GraphClient(subscription_response)
    client.get.return_value = user_response
    monkeypatch.setattr(
        "src.services.webhooks.adapters.microsoft_graph.httpx.AsyncClient",
        lambda: client,
    )

    result = await MicrosoftGraphAdapter().subscribe(
        "https://dev.example.com/api/hooks/source-1",
        {
            "user_id": "user-1",
            "resource": "/users/user-1/messages",
            "change_types": ["created"],
        },
        _graph_auth(),
    )

    assert result.external_id == "subscription-1"
    assert result.state["user_display_name"] == "Ada Lovelace"
    request = client.post.await_args
    assert request.kwargs["headers"]["Authorization"] == "Bearer tenant-token"
    assert request.kwargs["json"]["notificationUrl"] == (
        "https://dev.example.com/api/hooks/source-1"
    )
    assert "includeResourceData" not in request.kwargs["json"]


@pytest.mark.asyncio
async def test_graph_renewal_refreshes_user_identity_metadata(monkeypatch):
    renewal_response = SimpleNamespace(
        status_code=200,
        json=lambda: {"expirationDateTime": "2026-08-31T12:00:00Z"},
        text="",
    )
    user_response = SimpleNamespace(
        status_code=200,
        json=lambda: {
            "id": "user-1",
            "displayName": "Ada Lovelace",
            "mail": "ada@example.com",
            "userPrincipalName": "ada@example.com",
        },
        text="",
    )
    client = _GraphClient(renewal_response)
    client.get.return_value = user_response
    monkeypatch.setattr(
        "src.services.webhooks.adapters.microsoft_graph.httpx.AsyncClient",
        lambda: client,
    )

    result = await MicrosoftGraphAdapter().renew(
        external_id="subscription-1",
        state={},
        config={
            "user_id": "user-1",
            "resource": "/users/user-1/messages",
        },
        integration=_graph_auth(),
    )

    assert result is not None
    assert result.state == {
        "user_display_name": "Ada Lovelace",
        "user_principal_name": "ada@example.com",
        "user_mail": "ada@example.com",
    }


@pytest.mark.asyncio
async def test_graph_event_type_uses_configured_collection_not_object_id():
    request = WebhookRequest(
        method="POST",
        path="/api/hooks/source-1",
        headers={},
        query_params={},
        body=(
            b'{"value":[{"changeType":"created","resource":'
            b'"Users/user-1/Messages/01LONGMESSAGEID"}]}'
        ),
    )

    result = await MicrosoftGraphAdapter().handle_request(
        request,
        config={"resource": "/users/user-1/messages"},
        state={},
    )

    assert isinstance(result, Deliver)
    assert result.event_type == "graph.messages.created"


@pytest.mark.asyncio
@pytest.mark.parametrize("status_code", [204, 404])
async def test_graph_unsubscribe_accepts_deleted_or_already_missing(
    monkeypatch, status_code
):
    response = SimpleNamespace(status_code=status_code, text="", json=lambda: {})
    client = _GraphClient(response)
    monkeypatch.setattr(
        "src.services.webhooks.adapters.microsoft_graph.httpx.AsyncClient",
        lambda: client,
    )

    await MicrosoftGraphAdapter().unsubscribe("subscription-1", {}, _graph_auth())

    client.delete.assert_awaited_once()


@pytest.mark.asyncio
async def test_graph_unsubscribe_raises_when_provider_does_not_delete(monkeypatch):
    response = SimpleNamespace(
        status_code=503,
        text="unavailable",
        json=lambda: {"error": {"message": "Graph unavailable"}},
    )
    client = _GraphClient(response)
    monkeypatch.setattr(
        "src.services.webhooks.adapters.microsoft_graph.httpx.AsyncClient",
        lambda: client,
    )

    with pytest.raises(ValueError, match="Graph unavailable"):
        await MicrosoftGraphAdapter().unsubscribe(
            "subscription-1", {}, _graph_auth()
        )


@pytest.mark.asyncio
async def test_local_fixture_adapter_renews_deterministically():
    adapter = LocalFixtureWebhookAdapter()

    result = await adapter.renew(
        external_id="local-scheduler-fixture",
        state={"renewal_count": 2},
        config={},
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
        config={},
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


class TestMicrosoftBotFrameworkAdapter:
    """Tests Microsoft Bot Framework authentication and normalization."""

    app_id = "11111111-1111-1111-1111-111111111111"
    tenant_id = "tenant-1"
    service_url = "https://smba.trafficmanager.net/amer/"

    @pytest.fixture
    def adapter(self):
        return MicrosoftBotFrameworkAdapter()

    @pytest.fixture
    def signing_material(self):
        private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(private_key.public_key()))
        jwk.update({"kid": "teams-test-key", "endorsements": ["msteams"]})
        return private_key, jwk

    def _activity(self) -> dict:
        return {
            "type": "message",
            "id": "activity-1",
            "serviceUrl": self.service_url,
            "channelId": "msteams",
            "conversation": {"id": "conversation-1"},
            "from": {"id": "user-1", "name": "Test User"},
            "channelData": {
                "tenant": {"id": "tenant-1"},
                "team": {"id": "team-1"},
            },
        }

    def _token(self, private_key, **claim_overrides) -> str:
        claims = {
            "iss": "https://api.botframework.com",
            "aud": self.app_id,
            "exp": datetime.now(timezone.utc) + timedelta(minutes=5),
            "serviceurl": self.service_url,
        }
        claims.update(claim_overrides)
        return jwt.encode(
            claims,
            private_key,
            algorithm="RS256",
            headers={"kid": "teams-test-key"},
        )

    def _request(self, activity: dict, token: str | None = None) -> WebhookRequest:
        headers = {"content-type": "application/json", "cookie": "private"}
        if token:
            headers["authorization"] = f"Bearer {token}"
        return WebhookRequest(
            method="POST",
            path="/api/hooks/test",
            headers=headers,
            body=json.dumps(activity).encode(),
            query_params={},
        )

    def test_adapter_is_registered(self):
        assert (
            webhook_registry.AdapterRegistry().get("microsoft_bot_framework")
            is not None
        )

    @pytest.mark.asyncio
    async def test_subscribe_requires_app_id(self, adapter):
        with pytest.raises(ValueError, match="app_id"):
            await adapter.subscribe("https://example.com/hook", {}, None)

    @pytest.mark.asyncio
    async def test_subscribe_requires_tenant_id(self, adapter):
        with pytest.raises(ValueError, match="tenant_id"):
            await adapter.subscribe(
                "https://example.com/hook", {"app_id": self.app_id}, None
            )

    @pytest.mark.asyncio
    async def test_subscribe_allows_integration_mapping_admission(self, adapter):
        result = await adapter.subscribe(
            "https://example.com/hook",
            {
                "app_id": self.app_id,
                "tenant_admission": "integration_mappings",
            },
            None,
        )

        assert isinstance(result, SubscribeResult)

    @pytest.mark.asyncio
    async def test_missing_bearer_token_is_rejected(self, adapter):
        result = await adapter.handle_request(
            self._request(self._activity()),
            {"app_id": self.app_id, "tenant_id": self.tenant_id},
            {},
        )

        assert isinstance(result, Rejected)
        assert result.status_code == 401

    @pytest.mark.asyncio
    async def test_valid_teams_activity_is_normalized(self, adapter, signing_material):
        private_key, jwk = signing_material
        adapter._get_signing_jwk = AsyncMock(return_value=jwk)
        token = self._token(private_key)

        result = await adapter.handle_request(
            self._request(self._activity(), token),
            {"app_id": self.app_id, "tenant_id": self.tenant_id},
            {},
        )

        assert isinstance(result, Deliver)
        assert result.event_type == "microsoft_teams.message"
        assert result.data["conversation_id"] == "conversation-1"
        assert result.data["tenant_id"] == "tenant-1"
        assert result.data["team_id"] == "team-1"
        assert "authorization" not in result.raw_headers
        assert "cookie" not in result.raw_headers

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("claim_overrides", "activity_overrides"),
        [
            ({"aud": "wrong-app"}, {}),
            ({"serviceurl": "https://example.com/other"}, {}),
            ({}, {"channelId": "webchat"}),
        ],
    )
    async def test_invalid_token_or_activity_is_rejected(
        self,
        adapter,
        signing_material,
        claim_overrides,
        activity_overrides,
    ):
        private_key, jwk = signing_material
        adapter._get_signing_jwk = AsyncMock(return_value=jwk)
        activity = {**self._activity(), **activity_overrides}
        token = self._token(private_key, **claim_overrides)

        result = await adapter.handle_request(
            self._request(activity, token),
            {"app_id": self.app_id, "tenant_id": self.tenant_id},
            {},
        )

        assert isinstance(result, Rejected)
        assert result.status_code == 401

    @pytest.mark.asyncio
    async def test_key_must_be_endorsed_for_teams(self, adapter, signing_material):
        private_key, jwk = signing_material
        jwk["endorsements"] = ["webchat"]
        adapter._get_signing_jwk = AsyncMock(return_value=jwk)

        result = await adapter.handle_request(
            self._request(self._activity(), self._token(private_key)),
            {"app_id": self.app_id, "tenant_id": self.tenant_id},
            {},
        )

        assert isinstance(result, Rejected)
        assert result.status_code == 401

    @pytest.mark.asyncio
    async def test_activity_from_another_tenant_is_rejected(
        self, adapter, signing_material
    ):
        private_key, jwk = signing_material
        adapter._get_signing_jwk = AsyncMock(return_value=jwk)
        activity = self._activity()
        activity["channelData"]["tenant"]["id"] = "other-tenant"

        result = await adapter.handle_request(
            self._request(activity, self._token(private_key)),
            {"app_id": self.app_id, "tenant_id": self.tenant_id},
            {},
        )

        assert isinstance(result, Rejected)
        assert result.status_code == 401

    @pytest.mark.asyncio
    async def test_mapping_admission_defers_tenant_authorization_to_processor(
        self, adapter, signing_material
    ):
        private_key, jwk = signing_material
        adapter._get_signing_jwk = AsyncMock(return_value=jwk)
        activity = self._activity()
        activity["channelData"]["tenant"]["id"] = "customer-tenant"

        result = await adapter.handle_request(
            self._request(activity, self._token(private_key)),
            {
                "app_id": self.app_id,
                "tenant_admission": "integration_mappings",
            },
            {},
        )

        assert isinstance(result, Deliver)
        assert (
            adapter.get_mapping_entity_id(
                result, {"tenant_admission": "integration_mappings"}
            )
            == "customer-tenant"
        )
