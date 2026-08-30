"""Microsoft Bot Framework webhook adapter for Teams activity events."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any
from urllib.parse import urlparse

import httpx
import jwt

from src.services.webhooks.protocol import (
    Deliver,
    HandleResult,
    Rejected,
    SubscribeResult,
    WebhookAdapter,
    WebhookRequest,
)

logger = logging.getLogger(__name__)


class MicrosoftBotFrameworkAdapter(WebhookAdapter):
    """Validate and normalize Microsoft Teams Bot Framework activities."""

    name = "microsoft_bot_framework"
    display_name = "Microsoft Bot Framework"
    description = "Authenticated Microsoft Teams bot activities"
    requires_integration = None
    mapping_integration_name = "Microsoft Teams Bot"
    renewal_interval = None

    _OPENID_URL = "https://login.botframework.com/v1/.well-known/openidconfiguration"
    _EXPECTED_ISSUER = "https://api.botframework.com"
    _CACHE_TTL_SECONDS = 24 * 60 * 60
    _UNKNOWN_KEY_REFRESH_INTERVAL_SECONDS = 5 * 60
    _jwks: list[dict[str, Any]] | None = None
    _jwks_expires_at = 0.0
    _jwks_refreshed_at = 0.0
    _jwks_lock = asyncio.Lock()

    config_schema = {
        "type": "object",
        "required": ["app_id"],
        "properties": {
            "app_id": {
                "type": "string",
                "title": "Microsoft App ID",
                "description": "Application/client ID registered for the Azure Bot.",
            },
            "tenant_id": {
                "type": "string",
                "title": "Microsoft Tenant ID",
                "description": (
                    "Entra tenant ID allowed to send Teams activities when tenant "
                    "admission uses one configured tenant."
                ),
            },
            "tenant_admission": {
                "type": "string",
                "title": "Tenant Admission",
                "description": (
                    "Authorize one configured tenant or resolve the activity tenant "
                    "through mappings on the linked Microsoft Teams Bot integration."
                ),
                "enum": ["configured_tenant", "integration_mappings"],
                "default": "configured_tenant",
            },
        },
    }

    async def subscribe(
        self,
        callback_url: str,
        config: dict[str, Any],
        integration: Any | None,
    ) -> SubscribeResult:
        app_id = str(config.get("app_id") or "").strip()
        if not app_id:
            raise ValueError("Microsoft Bot Framework app_id is required")
        tenant_id = str(config.get("tenant_id") or "").strip()
        if not tenant_id and not self.uses_integration_mapping(config):
            raise ValueError("Microsoft Bot Framework tenant_id is required")
        return SubscribeResult()

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
        payload = request.json_body
        if not isinstance(payload, dict):
            return Rejected(message="Invalid Bot Framework activity", status_code=400)

        app_id = str(config.get("app_id") or "").strip()
        if not app_id:
            return Rejected(
                message="Bot Framework app ID is not configured", status_code=500
            )
        tenant_id = str(config.get("tenant_id") or "").strip()
        if not tenant_id and not self.uses_integration_mapping(config):
            return Rejected(
                message="Bot Framework tenant ID is not configured", status_code=500
            )

        authorization = request.headers.get("authorization", "")
        scheme, separator, token = authorization.partition(" ")
        if not separator or scheme.lower() != "bearer" or not token.strip():
            return Rejected(
                message="Missing Bot Framework bearer token", status_code=401
            )

        try:
            await self._validate_token(
                token.strip(),
                app_id,
                tenant_id or None,
                payload,
            )
        except (jwt.PyJWTError, KeyError, TypeError, ValueError) as exc:
            logger.warning(
                "Bot Framework token validation failed: %s", type(exc).__name__
            )
            return Rejected(
                message="Invalid Bot Framework bearer token", status_code=401
            )
        except httpx.HTTPError:
            logger.exception("Bot Framework signing metadata could not be loaded")
            return Rejected(
                message="Bot Framework authentication unavailable", status_code=503
            )

        activity_type = str(payload.get("type") or "unknown")
        channel_data = payload.get("channelData") or {}
        conversation = payload.get("conversation") or {}
        sender = payload.get("from") or {}

        return Deliver(
            data={
                "activity": payload,
                "activity_type": activity_type,
                "activity_id": payload.get("id"),
                "service_url": payload.get("serviceUrl"),
                "channel_id": payload.get("channelId"),
                "tenant_id": (channel_data.get("tenant") or {}).get("id"),
                "team_id": (channel_data.get("team") or {}).get("id"),
                "conversation_id": conversation.get("id"),
                "sender": sender,
                "reply_to_id": payload.get("replyToId"),
            },
            event_type=f"microsoft_teams.{activity_type}",
            raw_headers={
                key: value
                for key, value in request.headers.items()
                if key not in {"authorization", "cookie"}
            },
        )

    def uses_integration_mapping(self, config: dict[str, Any]) -> bool:
        return config.get("tenant_admission") == "integration_mappings"

    def get_mapping_entity_id(
        self,
        deliver: Deliver,
        config: dict[str, Any],
    ) -> str | None:
        if not self.uses_integration_mapping(config):
            return None
        return str(deliver.data.get("tenant_id") or "").strip() or None

    async def _validate_token(
        self,
        token: str,
        app_id: str,
        tenant_id: str | None,
        payload: dict[str, Any],
    ) -> None:
        header = jwt.get_unverified_header(token)
        if header.get("alg") != "RS256":
            raise ValueError("Unsupported signing algorithm")

        kid = str(header.get("kid") or "")
        if not kid:
            raise ValueError("Missing signing key ID")

        jwk = await self._get_signing_jwk(kid)
        signing_key = jwt.PyJWK.from_dict(jwk, algorithm="RS256").key
        claims = jwt.decode(
            token,
            key=signing_key,
            algorithms=["RS256"],
            audience=app_id,
            issuer=self._EXPECTED_ISSUER,
            leeway=300,
            options={"require": ["exp", "iss", "aud"]},
        )

        service_url = str(payload.get("serviceUrl") or "")
        claimed_service_url = str(
            claims.get("serviceurl") or claims.get("serviceUrl") or ""
        )
        if not service_url or claimed_service_url != service_url:
            raise ValueError("Bot Framework service URL mismatch")
        if urlparse(service_url).scheme != "https":
            raise ValueError("Bot Framework service URL must use HTTPS")

        channel_id = str(payload.get("channelId") or "")
        if channel_id != "msteams":
            raise ValueError("Only Microsoft Teams activities are accepted")
        channel_data = payload.get("channelData")
        activity_tenant_id = (
            (channel_data.get("tenant") or {}).get("id")
            if isinstance(channel_data, dict)
            else None
        )
        if tenant_id is not None and activity_tenant_id != tenant_id:
            raise ValueError("Microsoft Teams tenant mismatch")
        endorsements = jwk.get("endorsements") or []
        if channel_id not in endorsements:
            raise ValueError("Signing key is not endorsed for Microsoft Teams")

    async def _get_signing_jwk(self, kid: str) -> dict[str, Any]:
        keys = await self._get_jwks()
        match = next((key for key in keys if key.get("kid") == kid), None)
        cache_age = time.monotonic() - self._jwks_refreshed_at
        if match is None and cache_age >= self._UNKNOWN_KEY_REFRESH_INTERVAL_SECONDS:
            keys = await self._get_jwks(force_refresh=True)
            match = next((key for key in keys if key.get("kid") == kid), None)
        if match is None:
            raise ValueError("Unknown Bot Framework signing key")
        return match

    async def _get_jwks(self, force_refresh: bool = False) -> list[dict[str, Any]]:
        now = time.monotonic()
        if not force_refresh and self._jwks is not None and now < self._jwks_expires_at:
            return self._jwks

        async with self._jwks_lock:
            now = time.monotonic()
            if (
                not force_refresh
                and self._jwks is not None
                and now < self._jwks_expires_at
            ):
                return self._jwks

            async with httpx.AsyncClient(timeout=10.0) as client:
                metadata_response = await client.get(self._OPENID_URL)
                metadata_response.raise_for_status()
                metadata = metadata_response.json()
                if metadata.get("issuer") != self._EXPECTED_ISSUER:
                    raise ValueError("Unexpected Bot Framework issuer metadata")
                if "RS256" not in metadata.get(
                    "id_token_signing_alg_values_supported", []
                ):
                    raise ValueError("Bot Framework metadata does not allow RS256")

                jwks_uri = str(metadata.get("jwks_uri") or "")
                parsed_uri = urlparse(jwks_uri)
                if (
                    parsed_uri.scheme != "https"
                    or parsed_uri.hostname != "login.botframework.com"
                ):
                    raise ValueError("Unexpected Bot Framework signing-key URL")

                keys_response = await client.get(jwks_uri)
                keys_response.raise_for_status()
                keys = keys_response.json().get("keys")
                if not isinstance(keys, list) or not keys:
                    raise ValueError("Bot Framework signing keys are unavailable")

            self._jwks = keys
            self._jwks_refreshed_at = time.monotonic()
            self._jwks_expires_at = self._jwks_refreshed_at + self._CACHE_TTL_SECONDS
            return keys
