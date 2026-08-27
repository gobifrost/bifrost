"""Organization-aware OAuth resolution for webhook adapters."""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.security import decrypt_secret
from src.models.orm.integrations import Integration, IntegrationMapping
from src.services.oauth_provider import (
    build_token_refresh_context,
    get_token_for_org,
    refresh_oauth_token_http,
)
from src.services.webhooks.protocol import WebhookIntegrationAuth


@dataclass(frozen=True)
class WebhookIntegrationCredentials:
    """DB-derived OAuth material that can be resolved after the session closes."""

    integration_id: UUID
    organization_id: UUID
    entity_id: str
    token_context: dict[str, Any]
    encrypted_access_token: bytes | None = None
    access_token_expires_at: datetime | None = None


async def build_webhook_integration_credentials(
    db: AsyncSession,
    integration_id: UUID,
    organization_id: UUID | None,
) -> WebhookIntegrationCredentials:
    """Load an integration's exact org mapping and OAuth refresh material."""
    if organization_id is None:
        raise ValueError("Select an organization to authenticate this webhook")

    integration_result = await db.execute(
        select(Integration).where(
            Integration.id == integration_id,
            Integration.is_deleted.is_(False),
        )
    )
    integration = integration_result.scalar_one_or_none()
    if not integration:
        raise ValueError("Integration not found")
    if not integration.oauth_provider:
        raise ValueError(f"Integration '{integration.name}' has no OAuth provider")

    mapping_result = await db.execute(
        select(IntegrationMapping).where(
            IntegrationMapping.integration_id == integration_id,
            IntegrationMapping.organization_id == organization_id,
        )
    )
    mapping = mapping_result.scalar_one_or_none()
    if not mapping or not mapping.entity_id:
        raise ValueError(
            f"Integration '{integration.name}' is not mapped to the selected organization"
        )

    provider = integration.oauth_provider
    token = None
    if provider.oauth_flow_type != "client_credentials":
        token = await get_token_for_org(db, integration_id, organization_id)
        if not token:
            raise ValueError(
                f"Integration '{integration.name}' is not connected for the selected organization"
            )

    token_context = await build_token_refresh_context(
        db,
        provider,
        token=token,
        org_id=organization_id,
    )
    return WebhookIntegrationCredentials(
        integration_id=integration_id,
        organization_id=organization_id,
        entity_id=mapping.entity_id,
        token_context=token_context,
        encrypted_access_token=token.encrypted_access_token if token else None,
        access_token_expires_at=token.expires_at if token else None,
    )


async def resolve_webhook_integration_auth(
    credentials: WebhookIntegrationCredentials,
) -> WebhookIntegrationAuth:
    """Resolve a fresh plaintext access token without retaining a DB session."""
    use_stored_token = (
        credentials.encrypted_access_token is not None
        and credentials.access_token_expires_at is not None
        and credentials.access_token_expires_at
        > datetime.now(timezone.utc) + timedelta(minutes=5)
    )

    if use_stored_token:
        raw = credentials.encrypted_access_token
        access_token = decrypt_secret(raw.decode() if isinstance(raw, bytes) else raw)
    else:
        outcome = await refresh_oauth_token_http(credentials.token_context)
        if not outcome.get("success") or not outcome.get("access_token"):
            raise ValueError(outcome.get("error", "OAuth token request failed"))
        access_token = outcome["access_token"]

    return WebhookIntegrationAuth(
        integration_id=credentials.integration_id,
        organization_id=credentials.organization_id,
        entity_id=credentials.entity_id,
        access_token=access_token,
    )
