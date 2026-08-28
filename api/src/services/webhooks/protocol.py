"""
Webhook adapter protocol for the Bifrost event system.

Defines the base class and result types for webhook adapters.
Adapters handle provider-specific subscription management and request validation.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID

from starlette.requests import Request


@dataclass
class WebhookRequest:
    """
    Wrapper around incoming webhook request.

    Provides convenient access to request data for adapters.
    """

    method: str
    path: str
    headers: dict[str, str]
    query_params: dict[str, str]
    body: bytes
    client_ip: str | None = None

    _json_cache: dict[str, Any] | None = field(default=None, repr=False)

    @classmethod
    async def from_starlette(cls, request: Request) -> "WebhookRequest":
        """Create WebhookRequest from a Starlette/FastAPI request."""
        body = await request.body()
        headers = {k.lower(): v for k, v in request.headers.items()}
        query_params = dict(request.query_params)
        client_ip = request.client.host if request.client else None

        return cls(
            method=request.method,
            path=request.url.path,
            headers=headers,
            query_params=query_params,
            body=body,
            client_ip=client_ip,
        )

    @property
    def json_body(self) -> dict[str, Any] | None:
        """Parse body as JSON. Returns None if not valid JSON."""
        if self._json_cache is not None:
            return self._json_cache

        if not self.body:
            return None

        try:
            import json

            self._json_cache = json.loads(self.body)
            return self._json_cache
        except (json.JSONDecodeError, UnicodeDecodeError):
            return None

    @property
    def text_body(self) -> str:
        """Decode body as UTF-8 text."""
        return self.body.decode("utf-8", errors="replace")


@dataclass
class SubscribeResult:
    """
    Result from adapter.subscribe().

    Contains subscription state to persist.
    """

    external_id: str | None = None
    """ID from external service (e.g., Graph subscription ID)."""

    state: dict[str, Any] = field(default_factory=dict)
    """State to persist (secrets, tokens, etc.). Encrypted at rest."""

    expires_at: datetime | None = None
    """When the subscription expires (for renewal tracking)."""


@dataclass
class RenewResult:
    """
    Result from adapter.renew().

    Contains updated subscription state.
    """

    expires_at: datetime | None = None
    """New expiration time."""

    state: dict[str, Any] = field(default_factory=dict)
    """Updated state (merged with existing)."""


@dataclass(frozen=True)
class WebhookIntegrationAuth:
    """Resolved, tenant-scoped credentials passed to a webhook adapter."""

    integration_id: UUID
    organization_id: UUID
    entity_id: str
    access_token: str = field(repr=False)


@dataclass
class ValidationResponse:
    """
    Adapter says: respond to external service with this response.

    Used for validation callbacks (e.g., Graph validationToken).
    """

    status_code: int = 200
    body: Any = None
    headers: dict[str, str] | None = None
    content_type: str = "text/plain"


@dataclass
class Deliver:
    """
    Adapter says: deliver this event to subscribers.

    Contains the normalized event payload.
    """

    data: dict[str, Any]
    """Normalized event payload to deliver to workflows."""

    event_type: str | None = None
    """Optional event type for filtering (e.g., 'ticket.created')."""

    raw_headers: dict[str, str] | None = None
    """Original request headers (for logging)."""


@dataclass
class Rejected:
    """
    Adapter says: reject this request.

    Used for invalid signatures, unauthorized requests, etc.
    """

    message: str = "Request rejected"
    status_code: int = 400


# Union type for handle_request return
HandleResult = ValidationResponse | Deliver | Rejected


class WebhookAdapter(ABC):
    """
    Base class for webhook adapters.

    Adapters handle provider-specific logic for:
    - Creating subscriptions with external services
    - Validating incoming webhook requests
    - Renewing expiring subscriptions
    - Unsubscribing when sources are deleted

    Built-in adapters:
    - GenericWebhookAdapter: Simple webhook with optional HMAC signature
    - MicrosoftGraphAdapter: Graph API subscriptions with renewal

    Custom adapters can be created in workspace/adapters/*.py using the
    @adapter decorator from bifrost.webhooks.
    """

    # ==================== ADAPTER METADATA ====================
    # Override these in subclasses

    name: str = "base"
    """Unique adapter name (used for lookup)."""

    display_name: str = "Base Adapter"
    """Human-readable name for UI."""

    description: str = ""
    """Description of what this adapter does."""

    requires_integration: str | None = None
    """Integration name required for this adapter (e.g., 'Microsoft')."""

    requires_organization: bool = False
    """Whether the adapter must authenticate in an organization/tenant context."""

    config_schema: dict[str, Any] = {}
    """JSON Schema for adapter configuration."""

    renewal_interval: timedelta | None = None
    """How often to check for subscription renewal. None = no renewal needed."""

    # ==================== ABSTRACT METHODS ====================

    @abstractmethod
    async def subscribe(
        self,
        callback_url: str,
        config: dict[str, Any],
        integration: Any | None,
    ) -> SubscribeResult:
        """
        Create webhook subscription with external service.

        Args:
            callback_url: Full URL for the webhook endpoint.
            config: Adapter-specific configuration from user.
            integration: Resolved organization-scoped authentication, when required.

        Returns:
            SubscribeResult with external_id, state, and optional expires_at.

        Raises:
            Exception: If subscription creation fails.
        """
        pass

    @abstractmethod
    async def unsubscribe(
        self,
        external_id: str | None,
        state: dict[str, Any],
        integration: Any | None,
    ) -> None:
        """
        Remove webhook subscription from external service.

        Args:
            external_id: ID from SubscribeResult.external_id.
            state: State dict from SubscribeResult.state.
            integration: Resolved organization-scoped authentication, when required.

        Raises:
            Exception: If the provider cannot confirm the subscription is gone.
                Providers should treat an already-missing subscription as success.
        """
        pass

    def get_public_metadata(
        self,
        config: dict[str, Any],
        state: dict[str, Any],
    ) -> dict[str, Any]:
        """Return non-secret provider details that are safe to show to operators."""
        return {}

    @abstractmethod
    async def handle_request(
        self,
        request: WebhookRequest,
        config: dict[str, Any],
        state: dict[str, Any],
    ) -> HandleResult:
        """
        Process incoming webhook request.

        Args:
            request: Incoming webhook request.
            config: Adapter configuration.
            state: Adapter state (secrets, etc.).

        Returns:
            - ValidationResponse: Return this response to external service
            - Deliver: Proceed with event delivery to subscribers
            - Rejected: Reject the request (invalid signature, etc.)
        """
        pass

    # ==================== OPTIONAL METHODS ====================

    async def get_dynamic_values(
        self,
        operation: str,
        integration: Any | None,
        current_config: dict[str, Any],
    ) -> list[dict[str, Any]]:
        """
        Fetch dynamic options for a config field.

        Called by the UI to populate dropdowns for config fields that have
        x-dynamic-values defined in config_schema. Similar to Power Automate's
        x-ms-dynamic-values pattern.

        Args:
            operation: The operation name from x-dynamic-values.operation
            integration: Resolved organization-scoped authentication, when required.
            current_config: Config values selected so far (for dependent fields)

        Returns:
            List of option objects. Each object should have fields matching
            the value_path and label_path specified in x-dynamic-values.

        Raises:
            NotImplementedError: If operation is not supported by this adapter
        """
        raise NotImplementedError(f"Operation '{operation}' not supported by {self.name}")

    async def renew(
        self,
        external_id: str | None,
        state: dict[str, Any],
        config: dict[str, Any],
        integration: Any | None,
    ) -> RenewResult | None:
        """
        Renew subscription with external service.

        Override this if the adapter needs subscription renewal.

        Args:
            external_id: ID from SubscribeResult.external_id.
            state: State dict from SubscribeResult.state.
            config: Persisted adapter configuration for the subscription.
            integration: Resolved organization-scoped authentication, when required.

        Returns:
            RenewResult with new expires_at and optional state updates.
            None if renewal is not needed or not supported.
        """
        return None

    # ==================== HELPER METHODS ====================

    @staticmethod
    def generate_secret(length: int = 32) -> str:
        """Generate a cryptographically secure secret."""
        import secrets

        return secrets.token_urlsafe(length)

    @staticmethod
    def verify_hmac_sha256(
        body: bytes,
        secret: str,
        signature: str | None,
        prefix: str = "",
    ) -> bool:
        """
        Verify HMAC-SHA256 signature.

        Args:
            body: Request body bytes.
            secret: HMAC secret.
            signature: Signature from request header.
            prefix: Optional prefix in signature (e.g., 'sha256=').

        Returns:
            True if signature is valid.
        """
        if not signature:
            return False

        import hashlib
        import hmac

        # Remove prefix if present
        if prefix and signature.startswith(prefix):
            signature = signature[len(prefix) :]

        expected = hmac.new(
            secret.encode("utf-8"),
            body,
            hashlib.sha256,
        ).hexdigest()

        return hmac.compare_digest(signature.lower(), expected.lower())

    @staticmethod
    def expiration_datetime(days: int = 3) -> str:
        """Get ISO format datetime for subscription expiration."""
        dt = datetime.now(timezone.utc) + timedelta(days=days)
        return dt.isoformat()

    @staticmethod
    def parse_datetime(dt_str: str) -> datetime:
        """Parse ISO format datetime string."""
        # Handle various ISO formats
        dt_str = dt_str.replace("Z", "+00:00")
        dt = datetime.fromisoformat(dt_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
