"""
Webhook Subscription Renewal Scheduler

Automatically renews webhook subscriptions that are about to expire.
Some webhook providers (like Microsoft Graph) require periodic renewal
to keep subscriptions active.

Runs every 6 hours to check for subscriptions expiring within 48 hours.
"""

import logging
from datetime import datetime, timezone
from typing import Any


from src.core.database import get_db_context
from src.config import get_settings
from src.services.webhooks.auth import (
    build_webhook_integration_credentials,
    resolve_webhook_integration_auth,
)
from src.services.webhooks.registry import get_adapter

logger = logging.getLogger(__name__)

# Check for subscriptions expiring within 48 hours
RENEWAL_THRESHOLD_HOURS = 48


def _apply_renewal_result(
    webhook: Any,
    event_source: Any | None,
    result: dict[str, Any],
) -> None:
    """Apply a provider renewal outcome to persisted source state."""
    now = datetime.now(timezone.utc)
    if result.get("success"):
        webhook.expires_at = result["expires_at"]
        webhook.updated_at = now
        if result.get("external_id"):
            webhook.external_id = result["external_id"]
        if result.get("state"):
            webhook.state = (
                result["state"]
                if result.get("recreated")
                else {**(webhook.state or {}), **result["state"]}
            )
        if event_source:
            event_source.error_message = None
        return

    if event_source:
        event_source.error_message = (
            f"Provider subscription renewal failed: {result['error']}"
        )
        event_source.updated_at = now


async def _renew_or_recreate(
    adapter: Any,
    webhook: dict[str, Any],
    integration: Any | None,
) -> tuple[Any, bool]:
    """Renew an existing subscription, or recreate it when renewal is rejected."""
    result = await adapter.renew(
        external_id=webhook["external_id"],
        state=webhook["state"],
        config=webhook["config"],
        integration=integration,
    )
    if result is not None:
        return result, False

    return (
        await adapter.subscribe(
            callback_url=webhook["callback_url"],
            config=webhook["config"],
            integration=integration,
        ),
        True,
    )


async def renew_expiring_webhooks() -> dict[str, Any]:
    """
    Renew webhook subscriptions that are about to expire.

    Finds all webhook sources with expires_at within the renewal threshold
    and attempts to renew them using their adapter's renewal method.

    Returns:
        Summary of renewal results
    """
    start_time = datetime.now(timezone.utc)
    logger.info("▶ Webhook renewal starting")

    results: dict[str, Any] = {
        "total_webhooks": 0,
        "needs_renewal": 0,
        "renewed_successfully": 0,
        "recreated_successfully": 0,
        "renewal_failed": 0,
        "no_renewal_support": 0,
        "errors": [],
    }

    try:
        # Phase 1: Load webhooks needing renewal (short-lived session)
        async with get_db_context() as db:
            from src.repositories.events import WebhookSourceRepository

            repo = WebhookSourceRepository(db)
            webhooks = await repo.get_expiring_soon(within_hours=RENEWAL_THRESHOLD_HOURS)

            # Extract data needed for renewal (before session closes)
            webhook_data = []
            for webhook in webhooks:
                adapter = get_adapter(webhook.adapter_name)
                if not adapter or adapter.renewal_interval is None:
                    results["no_renewal_support"] += 1
                    continue
                credentials = None
                if adapter.requires_integration and webhook.integration_id:
                    try:
                        credentials = await build_webhook_integration_credentials(
                            db,
                            webhook.integration_id,
                            webhook.event_source.organization_id,
                        )
                    except ValueError as e:
                        results["renewal_failed"] += 1
                        results["errors"].append({
                            "webhook_id": str(webhook.id),
                            "adapter": webhook.adapter_name,
                            "error": str(e),
                        })
                        continue
                callback_path = f"/api/hooks/{webhook.event_source_id}"
                webhook_data.append({
                    "id": webhook.id,
                    "adapter_name": webhook.adapter_name,
                    "external_id": webhook.external_id,
                    "state": webhook.state or {},
                    "config": webhook.config or {},
                    "credentials": credentials,
                    "callback_path": callback_path,
                    "callback_url": f"{get_settings().public_url.rstrip('/')}{callback_path}",
                })

        results["total_webhooks"] = len(webhooks)
        results["needs_renewal"] = len(webhook_data)

        # Phase 2: Renew via HTTP (no DB connection held)
        renewal_results: list[dict] = []
        for wh in webhook_data:
            try:
                adapter = get_adapter(wh["adapter_name"])
                if not adapter:
                    continue

                integration = None
                if wh["credentials"] is not None:
                    integration = await resolve_webhook_integration_auth(
                        wh["credentials"]
                    )

                renewal_result, recreated = await _renew_or_recreate(
                    adapter,
                    wh,
                    integration,
                )

                if not recreated:
                    renewal_results.append({
                        "id": wh["id"],
                        "expires_at": renewal_result.expires_at,
                        "state": renewal_result.state,
                        "success": True,
                    })
                    results["renewed_successfully"] += 1
                    logger.info(
                        f"Renewed webhook subscription: {wh['callback_path']}",
                        extra={
                            "webhook_id": str(wh["id"]),
                            "adapter": wh["adapter_name"],
                            "new_expires_at": renewal_result.expires_at.isoformat() if renewal_result.expires_at else None,
                        },
                    )
                else:
                    renewal_results.append({
                        "id": wh["id"],
                        "expires_at": renewal_result.expires_at,
                        "external_id": renewal_result.external_id,
                        "state": renewal_result.state,
                        "success": True,
                        "recreated": True,
                    })
                    results["recreated_successfully"] += 1
                    logger.info(
                        f"Recreated webhook subscription: {wh['callback_path']}",
                        extra={
                            "webhook_id": str(wh["id"]),
                            "adapter": wh["adapter_name"],
                        },
                    )

            except Exception as e:
                results["renewal_failed"] += 1
                renewal_results.append({
                    "id": wh["id"],
                    "success": False,
                    "error": str(e),
                })
                results["errors"].append({
                    "webhook_id": str(wh["id"]),
                    "adapter": wh["adapter_name"],
                    "error": str(e),
                })
                logger.error(
                    f"Error renewing webhook {wh['id']}: {e}",
                    exc_info=True,
                )

        # Phase 3: Persist renewal results (short-lived session)
        if renewal_results:
            async with get_db_context() as db:
                from src.models.orm.events import EventSource
                from src.repositories.events import WebhookSourceRepository

                repo = WebhookSourceRepository(db)
                for rr in renewal_results:
                    webhook = await repo.get_by_id(rr["id"])
                    if not webhook:
                        continue

                    event_source = await db.get(
                        EventSource,
                        webhook.event_source_id,
                    )
                    _apply_renewal_result(webhook, event_source, rr)

                await db.commit()

        # Calculate duration
        end_time = datetime.now(timezone.utc)
        duration_seconds = (end_time - start_time).total_seconds()
        results["duration_seconds"] = duration_seconds
        results["start_time"] = start_time.isoformat()
        results["end_time"] = end_time.isoformat()

        # Log completion
        success = results["renewed_successfully"]
        failed = results["renewal_failed"]
        no_support = results["no_renewal_support"]

        if failed > 0:
            logger.warning(
                f"⚠ Webhook renewal completed with errors: "
                f"{success} renewed, {failed} failed, {no_support} no renewal needed "
                f"({duration_seconds:.1f}s)"
            )
        else:
            logger.info(
                f"✓ Webhook renewal completed: "
                f"{success} renewed, {failed} failed, {no_support} no renewal needed "
                f"({duration_seconds:.1f}s)"
            )

    except Exception as e:
        logger.error(f"✗ Webhook renewal failed: {e}", exc_info=True)
        results["errors"].append({"error": str(e)})

    return results
