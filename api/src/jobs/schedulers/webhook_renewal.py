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
from src.services.webhooks.registry import get_adapter

logger = logging.getLogger(__name__)

# Check for subscriptions expiring within 48 hours
RENEWAL_THRESHOLD_HOURS = 48


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
                webhook_data.append({
                    "id": webhook.id,
                    "adapter_name": webhook.adapter_name,
                    "external_id": webhook.external_id,
                    "state": webhook.state or {},
                    "integration": webhook.integration if webhook.integration_id else None,
                    "event_source_id": webhook.event_source_id,
                    "has_event_source": webhook.event_source is not None,
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

                renewal_result = await adapter.renew(
                    external_id=wh["external_id"],
                    state=wh["state"],
                    integration=wh["integration"],
                )

                if renewal_result:
                    renewal_results.append({
                        "id": wh["id"],
                        "expires_at": renewal_result.expires_at,
                        "state": renewal_result.state,
                        "success": True,
                    })
                    results["renewed_successfully"] += 1
                    logger.info(
                        f"Renewed webhook subscription for event source {wh['event_source_id']}",
                        extra={
                            "webhook_id": str(wh["id"]),
                            "event_source_id": str(wh["event_source_id"]),
                            "adapter": wh["adapter_name"],
                            "new_expires_at": renewal_result.expires_at.isoformat() if renewal_result.expires_at else None,
                        },
                    )
                else:
                    renewal_results.append({
                        "id": wh["id"],
                        "success": False,
                        "has_event_source": wh["has_event_source"],
                    })
                    results["renewal_failed"] += 1
                    results["errors"].append({
                        "webhook_id": str(wh["id"]),
                        "adapter": wh["adapter_name"],
                        "error": "Renewal returned None - subscription may have expired",
                    })

            except Exception as e:
                results["renewal_failed"] += 1
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
                from src.repositories.events import WebhookSourceRepository

                repo = WebhookSourceRepository(db)
                for rr in renewal_results:
                    webhook = await repo.get_by_id(rr["id"])
                    if not webhook:
                        continue

                    if rr.get("success"):
                        webhook.expires_at = rr["expires_at"]
                        webhook.updated_at = datetime.now(timezone.utc)
                        if rr.get("state"):
                            webhook.state = {**(webhook.state or {}), **rr["state"]}
                    elif rr.get("has_event_source") and webhook.event_source:
                        webhook.event_source.error_message = "Webhook subscription expired and could not be renewed"

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
