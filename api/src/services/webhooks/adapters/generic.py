"""
Generic webhook adapter for the Bifrost event system.

Provides a simple webhook endpoint that accepts any POST request.
Optionally validates HMAC signatures if a secret is configured.
"""

import hmac
import re
from typing import Any

from src.services.webhooks.protocol import (
    Deliver,
    HandleResult,
    Rejected,
    SubscribeResult,
    WebhookAdapter,
    WebhookRequest,
)
from src.services.webhooks.signing_secret import read_signing_secret


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
            "header_auth": {
                "type": "boolean",
                "title": "Header Token Authentication",
                "description": "Verify a static header value instead of an HMAC signature. Enter the complete value as the webhook secret, including Bearer when required.",
                "default": False,
            },
            "auth_header": {
                "type": "string",
                "title": "Authentication Header",
                "description": "Header containing the token, such as X-Gitlab-Token or Authorization.",
            },
            "secret": {
                "type": "string",
                "title": "Webhook Secret",
                "description": "HMAC signing secret, or complete header token when header authentication is enabled.",
                "format": "password",
                "x-help": {
                    "text": "For HMAC, enter the sender's signing secret. For header token authentication, enter the complete header value, including any Bearer prefix.",
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
                    "text": "Prefix before the encoded digest in the signature header value. Bifrost strips the prefix and surrounding whitespace before comparing. Use 'sha256=' for GitHub-style signatures, or leave empty if the header contains only the digest.",
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
            "delivery_id_header": {
                "type": "string",
                "title": "Delivery ID Header",
                "maxLength": 128,
                "description": "Optional provider delivery ID header. Requires a signing secret. "
                "Repeated IDs reuse the retained event; a changed body or event type is rejected. "
                "Deleting event history ends deduplication for those IDs.",
            },
        },
    }

    @staticmethod
    def validate_config(config: dict[str, Any]) -> str | None:
        """Validate header mode without inspecting or disclosing its secret."""
        header_auth = config.get("header_auth", False)
        if not isinstance(header_auth, bool):
            return "Header authentication must be enabled or disabled"
        if not header_auth:
            return None
        header = config.get("auth_header")
        if not isinstance(header, str) or not re.fullmatch(
            r"[!#$%&'*+.^_`|~0-9A-Za-z-]{1,128}", header
        ):
            return "Enter a valid authentication header name"
        if header.lower() == "x-forwarded-for":
            return "Authentication header cannot be X-Forwarded-For because it supplies event metadata"
        event_header = config.get("event_type_header")
        if isinstance(event_header, str) and event_header.lower() == header.lower():
            return "Authentication header cannot be used as the event type header"
        if config.get("delivery_id_header"):
            return "Delivery identity requires HMAC authentication"
        return None

    async def subscribe(
        self,
        callback_url: str,
        config: dict[str, Any],
        integration: Any | None,
    ) -> SubscribeResult:
        """
        Create subscription for generic webhook.

        No external service call. The events router stores the signing secret
        encrypted in state (see ``signing_secret``), so subscribe stores none.
        """
        return SubscribeResult(
            external_id=None,  # No external subscription
            state={},
            expires_at=None,  # Never expires
        )

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
        if self.validate_config(config):
            return Rejected("Header authentication is not configured correctly", 503)
        secret = read_signing_secret(state)

        header_auth = config.get("header_auth", False)
        if not isinstance(header_auth, bool):
            return Rejected("Header authentication is not configured correctly", 503)
        auth_header = config.get("auth_header", "")
        if header_auth:
            if not isinstance(auth_header, str) or not re.fullmatch(
                r"[!#$%&'*+.^_`|~0-9A-Za-z-]{1,128}", auth_header
            ) or not secret:
                return Rejected("Header authentication is not configured correctly", 503)
            auth_header = auth_header.lower()
            # A credential cannot simultaneously be metadata exposed to consumers.
            if any(
                isinstance(config.get(key), str) and config[key].lower() == auth_header
                for key in ("event_type_header", "delivery_id_header")
            ):
                return Rejected("Authentication header cannot be used as event metadata", 503)
            supplied = request.headers.get(auth_header)
            if supplied is None or not hmac.compare_digest(
                supplied.encode("utf-8"), secret.encode("utf-8")
            ):
                return Rejected("Invalid authentication token", 401)
        elif secret:
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

        credential_headers = {"authorization", "cookie"}
        if isinstance(auth_header, str) and auth_header:
            credential_headers.add(auth_header.lower())
        safe_headers = {key: value for key, value in request.headers.items()
                        if key.lower() not in credential_headers}

        delivery_id = None
        delivery_header = config.get("delivery_id_header")
        if delivery_header is not None and delivery_header != "":
            if not secret or header_auth:
                return Rejected("Delivery identity requires a signing secret", 503)
            if not isinstance(delivery_header, str) or not re.fullmatch(
                r"[!#$%&'*+.^_`|~0-9A-Za-z-]{1,128}", delivery_header
            ):
                return Rejected("Delivery ID header is not configured correctly", 503)
            delivery_id = safe_headers.get(delivery_header.lower(), "")
            if not isinstance(delivery_id, str) or not re.fullmatch(r"[!-~]{1,128}", delivery_id):
                return Rejected("Missing or invalid delivery identity", 400)

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
            event_type = safe_headers.get(type_header.lower())

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
            raw_headers=safe_headers,
            delivery_id=delivery_id,
        )
