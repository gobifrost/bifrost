"""
Webhook Adapter SDK for Bifrost

Provides base classes and decorators for creating custom webhook adapters
in the workspace. Custom adapters can be used to integrate with services
that require specific subscription management, validation, or renewal logic.

Usage in workspace/adapters/my_adapter.py:
    from bifrost.webhooks import WebhookAdapter, adapter

    @adapter(name="my_service", integration="MyService")
    class MyServiceAdapter(WebhookAdapter):
        '''Handle webhooks from MyService.'''

        display_name = "My Service"
        description = "Webhooks from My Service API"

        config_schema = {
            "type": "object",
            "properties": {
                "event_types": {
                    "type": "array",
                    "items": {"type": "string"},
                },
            },
        }

        async def subscribe(self, callback_url, config, integration):
            # Create subscription with external service
            ...

        async def handle_request(self, request, config, state):
            # Validate and parse incoming webhook
            ...
"""

import base64
import hashlib
import hmac
import json
import secrets
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, TypeVar

F = TypeVar("F", bound=type)


@dataclass
class WebhookRequest:
    """
    Incoming webhook request data.

    Provides access to all aspects of the HTTP request for validation
    and payload extraction.
    """

    method: str
    """HTTP method (GET, POST, etc.)"""

    headers: dict[str, str]
    """Request headers (lowercase keys)"""

    query_params: dict[str, str]
    """Query string parameters"""

    body: bytes
    """Raw request body"""

    source_ip: str | None = None
    """Client IP address"""

    @property
    def json_body(self) -> dict[str, Any] | None:
        """Parse body as JSON, returns None if invalid."""
        try:
            return json.loads(self.body.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            return None

    @property
    def text_body(self) -> str:
        """Decode body as UTF-8 text."""
        return self.body.decode("utf-8", errors="replace")


@dataclass
class SubscribeResult:
    """Result of a subscribe operation."""

    external_id: str | None = None
    """External subscription ID from the service (for renewal/unsubscribe)"""

    state: dict[str, Any] | None = None
    """Adapter-managed state to persist (secrets, tokens, etc.)"""

    expires_at: datetime | None = None
    """When the subscription expires (triggers renewal)"""


@dataclass
class RenewResult:
    """Result of a renewal operation."""

    expires_at: datetime | None = None
    """New expiration time"""

    state: dict[str, Any] | None = None
    """Updated state (merged with existing)"""


@dataclass
class ValidationResponse:
    """
    Return this from handle_request for validation/handshake responses.

    Some services (like Microsoft Graph) send a validation challenge
    that must be echoed back. Use this to return the exact response needed.
    """

    status_code: int
    """HTTP status code to return"""

    body: str
    """Response body"""

    content_type: str = "text/plain"
    """Content-Type header"""

    headers: dict[str, str] | None = None
    """Additional response headers"""


@dataclass
class Deliver:
    """
    Return this from handle_request to deliver the event.

    The event will be logged and delivered to all matching subscriptions.
    """

    data: dict[str, Any]
    """Normalized event payload to pass to workflows"""

    event_type: str | None = None
    """Optional event type for subscription filtering"""

    raw_headers: dict[str, str] | None = None
    """Original headers to store with event (for debugging)"""


@dataclass
class Rejected:
    """
    Return this from handle_request to reject the request.

    Used when validation fails (bad signature, invalid payload, etc.)
    """

    message: str
    """Error message to return"""

    status_code: int = 400
    """HTTP status code (400, 401, 403, etc.)"""


# Type alias for adapter responses
HandleResult = ValidationResponse | Deliver | Rejected


class WebhookAdapter(ABC):
    """
    Base class for webhook adapters.

    Subclass this to create custom adapters for services that require
    specific subscription management, validation, or renewal logic.

    Class attributes to define:
        name: Unique adapter name (set by @adapter decorator)
        display_name: Human-readable name for UI
        description: Description of what this adapter handles
        requires_integration: Integration name required (e.g., "Microsoft")
        config_schema: JSON Schema for adapter configuration
        renewal_interval: How often to check for renewal (None = no renewal)
    """

    # Set by @adapter decorator
    name: str = ""

    # Override these in subclass
    display_name: str = ""
    description: str = ""
    requires_integration: str | None = None
    config_schema: dict[str, Any] = {}
    renewal_interval: timedelta | None = None

    @abstractmethod
    async def subscribe(
        self,
        callback_url: str,
        config: dict[str, Any],
        integration: Any | None,
    ) -> SubscribeResult:
        """
        Create a subscription with the external service.

        Called when a new event source is created. For simple webhooks
        (paste URL), this may just return state with secrets.

        Args:
            callback_url: Full URL for the webhook endpoint
            config: Adapter configuration from user
            integration: OAuth integration instance (if requires_integration)

        Returns:
            SubscribeResult with external_id, state, and expires_at
        """
        ...

    async def unsubscribe(
        self,
        external_id: str | None,
        state: dict[str, Any],
        integration: Any | None,
    ) -> None:
        """
        Delete a subscription from the external service.

        Called when an event source is deleted. Best effort - doesn't
        raise on failure.

        Args:
            external_id: External subscription ID from subscribe
            state: Adapter state
            integration: OAuth integration instance
        """
        pass  # Default: no-op

    async def renew(
        self,
        external_id: str | None,
        state: dict[str, Any],
        integration: Any | None,
    ) -> RenewResult | None:
        """
        Renew a subscription before expiration.

        Called periodically for adapters with renewal_interval set.
        Return None if renewal fails and subscription should be recreated.

        Args:
            external_id: External subscription ID
            state: Adapter state
            integration: OAuth integration instance

        Returns:
            RenewResult with new expiration, or None if failed
        """
        return None  # Default: no renewal

    @abstractmethod
    async def handle_request(
        self,
        request: WebhookRequest,
        config: dict[str, Any],
        state: dict[str, Any],
    ) -> HandleResult:
        """
        Handle an incoming webhook request.

        Called for every incoming request to this webhook. Should:
        1. Validate the request (signature, etc.)
        2. Handle validation challenges if needed
        3. Parse the payload and return normalized data

        Args:
            request: Incoming webhook request
            config: Adapter configuration
            state: Adapter state (may contain secrets)

        Returns:
            - ValidationResponse: For handshake/challenge responses
            - Deliver: To process the event
            - Rejected: To reject the request
        """
        ...

    # ==========================================================================
    # Helper methods for common operations
    # ==========================================================================

    @staticmethod
    def generate_secret(length: int = 32) -> str:
        """Generate a random secret string."""
        return secrets.token_hex(length // 2)

    @staticmethod
    def verify_hmac_sha256(
        payload: bytes,
        secret: str,
        signature: str,
        prefix: str = "sha256=",
    ) -> bool:
        """
        Verify a hex- or base64-encoded HMAC-SHA256 signature.

        Common pattern for webhook validation (GitHub, Stripe, etc.)

        Args:
            payload: Raw request body
            secret: HMAC secret
            signature: Signature from header
            prefix: Prefix in signature value (e.g., "sha256=")

        Returns:
            True if signature is valid
        """
        signature = signature.strip()
        if prefix:
            if not signature.startswith(prefix):
                return False
            signature = signature[len(prefix) :].strip()

        digest = hmac.new(
            secret.encode(),
            payload,
            hashlib.sha256,
        ).digest()

        hex_matches = hmac.compare_digest(signature, digest.hex())
        base64_matches = hmac.compare_digest(
            signature,
            base64.b64encode(digest).decode("ascii"),
        )

        return hex_matches or base64_matches

    @staticmethod
    def expiration_datetime(
        days: int = 0,
        hours: int = 0,
        minutes: int = 0,
    ) -> str:
        """
        Generate ISO8601 expiration datetime string.

        Useful for creating subscription expiration times.

        Args:
            days: Days from now
            hours: Hours from now
            minutes: Minutes from now

        Returns:
            ISO8601 datetime string
        """
        dt = datetime.now(timezone.utc) + timedelta(
            days=days, hours=hours, minutes=minutes
        )
        return dt.isoformat()

    @staticmethod
    def parse_datetime(dt_string: str) -> datetime | None:
        """Parse ISO8601 datetime string."""
        try:
            return datetime.fromisoformat(dt_string.replace("Z", "+00:00"))
        except (ValueError, AttributeError):
            return None


@dataclass
class AdapterMetadata:
    """Metadata attached to adapter classes by the @adapter decorator."""

    name: str
    integration: str | None = None


def adapter(
    _cls: type | None = None,
    *,
    name: str | None = None,
    integration: str | None = None,
) -> Callable[[F], F] | F:
    """
    Decorator for registering webhook adapter classes.

    Marks a WebhookAdapter subclass for discovery by the platform.
    Adapters decorated with @adapter will be automatically loaded
    from workspace/adapters/*.py files.

    Usage:
        @adapter(name="my_service", integration="MyService")
        class MyServiceAdapter(WebhookAdapter):
            ...

    Args:
        name: Unique adapter name (defaults to class name, snake_case)
        integration: Required integration name (e.g., "Microsoft")

    Returns:
        Decorated class with _adapter_metadata attribute
    """

    def decorator(cls: F) -> F:
        # Convert class name to snake_case for default name
        adapter_name = name
        if adapter_name is None:
            # CamelCase to snake_case
            import re

            adapter_name = re.sub(r"(?<!^)(?=[A-Z])", "_", cls.__name__).lower()
            # Remove _adapter suffix if present
            if adapter_name.endswith("_adapter"):
                adapter_name = adapter_name[:-8]

        # Create metadata
        metadata = AdapterMetadata(
            name=adapter_name,
            integration=integration,
        )

        # Set name on class
        cls.name = adapter_name  # type: ignore

        # Attach metadata to class
        cls._adapter_metadata = metadata  # type: ignore

        return cls

    if _cls is not None:
        return decorator(_cls)
    return decorator


# Re-export everything users need
__all__ = [
    # Base class
    "WebhookAdapter",
    # Request/response types
    "WebhookRequest",
    "SubscribeResult",
    "RenewResult",
    "ValidationResponse",
    "Deliver",
    "Rejected",
    "HandleResult",
    # Decorator
    "adapter",
    "AdapterMetadata",
]
