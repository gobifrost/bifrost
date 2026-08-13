"""Resend webhook adapter with Standard Webhooks (Svix) verification."""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import time
from typing import Any

from src.services.webhooks.protocol import (
    Deliver,
    HandleResult,
    Rejected,
    SubscribeResult,
    WebhookAdapter,
    WebhookRequest,
)


class ResendWebhookAdapter(WebhookAdapter):
    """Verify and normalize webhook deliveries sent by Resend."""

    name = "resend"
    display_name = "Resend"
    description = "Verified Resend webhooks, including real-time inbound email events."
    requires_integration = None
    renewal_interval = None

    config_schema = {
        "type": "object",
        "properties": {
            "signing_secret": {
                "type": "string",
                "title": "Signing Secret",
                "description": (
                    "Signing secret from the Resend webhook details page. "
                    "Create the source first to obtain its callback URL, then add this secret."
                ),
                "format": "password",
            },
            "timestamp_tolerance_seconds": {
                "type": "integer",
                "title": "Timestamp Tolerance",
                "description": "Maximum accepted clock skew in seconds.",
                "default": 300,
                "minimum": 30,
                "maximum": 900,
            },
        },
    }

    @staticmethod
    def _decode_secret(secret: str) -> bytes:
        if not secret.startswith("whsec_"):
            raise ValueError("Resend signing secret must start with 'whsec_'")
        encoded = secret.removeprefix("whsec_")
        encoded += "=" * (-len(encoded) % 4)
        try:
            decoded = base64.b64decode(encoded, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise ValueError("Resend signing secret is not valid base64") from exc
        if not decoded:
            raise ValueError("Resend signing secret is empty")
        return decoded

    @classmethod
    def _verify_signature(
        cls,
        *,
        body: bytes,
        message_id: str,
        timestamp: str,
        signature_header: str,
        signing_secret: str,
        tolerance_seconds: int,
    ) -> bool:
        try:
            timestamp_value = int(timestamp)
        except ValueError:
            return False

        if abs(int(time.time()) - timestamp_value) > tolerance_seconds:
            return False

        secret = cls._decode_secret(signing_secret)
        signed_payload = f"{message_id}.{timestamp}.".encode() + body
        expected = hmac.new(secret, signed_payload, hashlib.sha256).digest()

        for versioned_signature in signature_header.split():
            version, separator, encoded_signature = versioned_signature.partition(",")
            if version != "v1" or not separator or not encoded_signature:
                continue
            try:
                candidate = base64.b64decode(encoded_signature, validate=True)
            except (binascii.Error, ValueError):
                continue
            if hmac.compare_digest(expected, candidate):
                return True
        return False

    async def subscribe(
        self,
        callback_url: str,
        config: dict[str, Any],
        integration: Any | None,
    ) -> SubscribeResult:
        signing_secret = str(config.get("signing_secret") or "")
        state: dict[str, Any] = {}
        if signing_secret:
            self._decode_secret(signing_secret)
            state["signing_secret"] = signing_secret
        return SubscribeResult(state=state)

    async def configure(
        self,
        config: dict[str, Any],
        state: dict[str, Any],
    ) -> dict[str, Any]:
        """Validate and store the secret Resend reveals after endpoint creation."""
        signing_secret = str(config.get("signing_secret") or "")
        new_state = dict(state)
        if signing_secret:
            self._decode_secret(signing_secret)
            new_state["signing_secret"] = signing_secret
        return new_state

    async def unsubscribe(
        self,
        external_id: str | None,
        state: dict[str, Any],
        integration: Any | None,
    ) -> None:
        return None

    async def handle_request(
        self,
        request: WebhookRequest,
        config: dict[str, Any],
        state: dict[str, Any],
    ) -> HandleResult:
        if request.method.upper() != "POST":
            return Rejected(message="Method not allowed", status_code=405)

        message_id = request.headers.get("svix-id")
        timestamp = request.headers.get("svix-timestamp")
        signature = request.headers.get("svix-signature")
        if not message_id or not timestamp or not signature:
            return Rejected(message="Missing Resend signature headers", status_code=400)

        signing_secret = str(state.get("signing_secret") or "")
        if not signing_secret:
            return Rejected(message="Resend signing secret is not configured", status_code=500)

        tolerance = int(config.get("timestamp_tolerance_seconds") or 300)
        try:
            verified = self._verify_signature(
                body=request.body,
                message_id=message_id,
                timestamp=timestamp,
                signature_header=signature,
                signing_secret=signing_secret,
                tolerance_seconds=tolerance,
            )
        except ValueError:
            return Rejected(message="Resend signing secret is invalid", status_code=500)
        if not verified:
            return Rejected(message="Invalid or expired Resend signature", status_code=401)

        payload = request.json_body
        if not isinstance(payload, dict):
            return Rejected(message="Resend payload must be a JSON object", status_code=400)

        event_type = payload.get("type")
        if not isinstance(event_type, str) or not event_type:
            event_type = "None"

        return Deliver(
            data=payload,
            event_type=event_type,
            raw_headers=request.headers,
        )
