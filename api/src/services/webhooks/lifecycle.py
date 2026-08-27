"""Provider-managed webhook lifecycle operations."""

from datetime import datetime, timezone
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from src.models.orm.events import EventSource, WebhookSource
from src.services.webhooks.auth import (
    build_webhook_integration_credentials,
    resolve_webhook_integration_auth,
)
from src.services.webhooks.registry import get_adapter_registry


async def _resolve_integration(
    db: AsyncSession,
    source: EventSource,
    webhook: WebhookSource,
    adapter: Any,
) -> Any | None:
    if not adapter.requires_integration:
        return None
    if not webhook.integration_id:
        raise ValueError(
            f"Adapter '{webhook.adapter_name}' requires an integration"
        )
    credentials = await build_webhook_integration_credentials(
        db,
        webhook.integration_id,
        source.organization_id,
    )
    return await resolve_webhook_integration_auth(credentials)


async def unsubscribe_provider(
    db: AsyncSession,
    source: EventSource,
) -> None:
    """Confirm a provider subscription is gone before local deletion."""
    webhook = source.webhook_source
    if webhook is None or webhook.adapter_name is None:
        return
    adapter = get_adapter_registry().get(webhook.adapter_name)
    if adapter is None:
        raise ValueError(f"Unknown webhook adapter: {webhook.adapter_name}")
    integration = await _resolve_integration(db, source, webhook, adapter)
    await adapter.unsubscribe(
        external_id=webhook.external_id,
        state=webhook.state or {},
        integration=integration,
    )


async def resubscribe_provider(
    db: AsyncSession,
    source: EventSource,
    callback_url: str,
) -> None:
    """Replace a provider registration while preserving the local source."""
    webhook = source.webhook_source
    if webhook is None or webhook.adapter_name is None:
        raise ValueError("This event source has no provider-managed webhook")
    adapter = get_adapter_registry().get(webhook.adapter_name)
    if adapter is None:
        raise ValueError(f"Unknown webhook adapter: {webhook.adapter_name}")
    integration = await _resolve_integration(db, source, webhook, adapter)

    try:
        await adapter.unsubscribe(
            external_id=webhook.external_id,
            state=webhook.state or {},
            integration=integration,
        )
    except Exception as exc:
        source.error_message = f"Could not remove the existing provider subscription: {exc}"
        source.updated_at = datetime.now(timezone.utc)
        await db.commit()
        raise

    webhook.external_id = None
    webhook.state = {}
    webhook.expires_at = None
    webhook.updated_at = datetime.now(timezone.utc)
    await db.commit()

    try:
        result = await adapter.subscribe(
            callback_url=callback_url,
            config=webhook.config or {},
            integration=integration,
        )
    except Exception as exc:
        source.error_message = f"Provider resubscription failed: {exc}"
        source.updated_at = datetime.now(timezone.utc)
        await db.commit()
        raise

    webhook.external_id = result.external_id
    webhook.state = result.state
    webhook.expires_at = result.expires_at
    webhook.updated_at = datetime.now(timezone.utc)
    source.error_message = None
    source.updated_at = datetime.now(timezone.utc)
    await db.commit()
