"""
Generic webhook adapter for the Bifrost event system.

Provides a simple webhook endpoint that accepts any POST request.
Optionally validates HMAC signatures if a secret is configured.
"""

from typing import Any

from src.services.webhooks.protocol import (
    Deliver,
    HandleResult,
    Rejected,
    SubscribeResult,
    WebhookAdapter,
    WebhookRequest,
)


class GenericWebhookAdapter(WebhookAdapter):
    """
    Generic webhook adapter for simple "paste URL" webhooks.

    No external subscription management - just validates and delivers.
    Optionally supports HMAC signature verification if secret configured.

    Configuration:
        secret: Optional HMAC secret for signature verification
        signature_header: Header containing the signature (default: X-Signature-256)
        signature_prefix: Prefix in signature value (default: sha256=)
        event_type_header: Optional header containing event type
        event_type_field: Optional field in payload containing event type
    """

    name = "generic"
    display_name = "Generic Webhook"
    description = "Simple webhook endpoint for any service. Paste the URL in your external service's webhook settings."
    requires_integration = None
    renewal_interval = None  # No renewal needed

    config_schema = {
        "type": "object",
        "properties": {
            "secret": {
                "type": "string",
                "title": "Webhook Secret",
                "description": "HMAC secret for signature verification (optional)",
                "format": "password",
                "x-help": {
                    "text": "Shared secret used to verify webhook signatures via HMAC-SHA256. Set the same secret in both Bifrost and the sending service.",
                    "code": (
                        "# Python example: sign a webhook request\n"
                        "import hmac, hashlib\n"
                        "sig = hmac.new(\n"
                        "    secret.encode(), body, hashlib.sha256\n"
                        ").hexdigest()\n"
                        'headers["X-Signature-256"] = f"sha256={sig}"'
                    ),
                },
            },
            "signature_header": {
                "type": "string",
                "title": "Signature Header",
                "description": "Header containing the HMAC signature",
                "default": "X-Signature-256",
                "x-help": {
                    "text": "The HTTP header where the sender places the HMAC signature. Common values: X-Signature-256 (default), X-Hub-Signature-256 (GitHub).",
                },
            },
            "signature_prefix": {
                "type": "string",
                "title": "Signature Prefix",
                "description": "Prefix in signature value (e.g., 'sha256=')",
                "default": "sha256=",
                "x-help": {
                    "text": "Prefix before the hex digest in the signature header value. Bifrost strips this before comparing. Use 'sha256=' for GitHub-style signatures, or leave empty if the header contains only the hex digest.",
                },
            },
            "event_type_header": {
                "type": "string",
                "title": "Event Type Header",
                "description": "Header containing the event type (optional)",
                "x-help": {
                    "text": "HTTP header the sender uses to indicate the event type. This lets subscriptions filter by event type. Example: GitHub sends X-GitHub-Event.",
                },
            },
            "event_type_field": {
                "type": "string",
                "title": "Event Type Field",
                "description": "Field in payload containing event type (e.g., 'event', 'type')",
                "x-help": {
                    "text": "Top-level JSON field in the request body containing the event type. Takes precedence over the header if both are set. Example: Stripe uses 'type' (e.g. 'invoice.paid').",
                },
            },
        },
    }

    async def subscribe(
        self,
        callback_url: str,
        config: dict[str, Any],
        integration: Any | None,
    ) -> SubscribeResult:
        """
        Create subscription for generic webhook.

        No external service call - just returns the state.
        """
        # Store secret in state if provided (will be encrypted at rest)
        state = {}
        if config.get("secret"):
            state["secret"] = config["secret"]

        return SubscribeResult(
            external_id=None,  # No external subscription
            state=state,
            expires_at=None,  # Never expires
        )

    async def configure(
        self,
        config: dict[str, Any],
        state: dict[str, Any],
    ) -> dict[str, Any]:
        """Copy the optional shared secret into encrypted adapter state."""
        new_state = dict(state)
        if config.get("secret"):
            new_state["secret"] = config["secret"]
        return new_state

    async def unsubscribe(
        self,
        external_id: str | None,
        state: dict[str, Any],
        integration: Any | None,
    ) -> None:
        """
        Unsubscribe from generic webhook.

        Nothing to do - no external service to notify.
        """
        pass

    async def handle_request(
        self,
        request: WebhookRequest,
        config: dict[str, Any],
        state: dict[str, Any],
    ) -> HandleResult:
        """
        Handle incoming webhook request.

        Validates signature if secret is configured, then delivers the payload.
        """
        # Get secret from state (set during subscribe)
        secret = state.get("secret")

        if secret:
            # Validate signature
            sig_header = config.get("signature_header", "x-signature-256").lower()
            sig_prefix = config.get("signature_prefix", "sha256=")

            signature = request.headers.get(sig_header)
            if not signature:
                return Rejected(
                    message=f"Missing signature header: {sig_header}",
                    status_code=401,
                )

            if not self.verify_hmac_sha256(
                request.body,
                secret,
                signature,
                prefix=sig_prefix,
            ):
                return Rejected(
                    message="Invalid signature",
                    status_code=401,
                )

        # Parse payload
        payload = request.json_body
        if payload is None:
            # Accept non-JSON payloads as raw body
            payload = {"body": request.text_body}

        # Extract event type (default to "None" if not configured/found)
        event_type: str | None = None

        # From header
        type_header = config.get("event_type_header")
        if type_header:
            event_type = request.headers.get(type_header.lower())

        # From payload field (overrides header)
        type_field = config.get("event_type_field")
        if type_field and isinstance(payload, dict):
            event_type = payload.get(type_field) or event_type

        # Default to "None" if no event type was extracted
        if not event_type:
            event_type = "None"

        return Deliver(
            data=payload,
            event_type=event_type,
            raw_headers=request.headers,
        )
