"""Bounded provider reconciliation and recovery over existing event dispatch."""

from datetime import datetime, timedelta, timezone
from uuid import UUID

from sqlalchemy import delete, select

from shared.github_app import credentials, fence, provider_client, refresh_installation
from shared.github_app_auth import GitHubAppError
from src.core.database import get_db_context
from src.jobs.platform.base import PlatformJobContext
from src.models.enums import EventDeliveryStatus
from src.models.orm.events import Event, EventDelivery
from src.models.orm.github_app import GitHubAppDelivery, GitHubAppInstallation
from src.services.events.processor import EventProcessor

DEDUP_RETENTION_DAYS = 30
PROVIDER_LOOKBACK_DAYS = 3
MAX_RECOVERY_PAGES = 10
RECOVERY_OVERLAP = timedelta(minutes=10)


async def recover_internal_events() -> int:
    async with get_db_context() as db:
        event_ids = list((await db.execute(select(Event.id).join(GitHubAppDelivery,
            GitHubAppDelivery.event_id == Event.id).join(EventDelivery, EventDelivery.event_id == Event.id)
            .where(EventDelivery.status == EventDeliveryStatus.PENDING).distinct().limit(100))).scalars())
    recovered = 0
    for event_id in event_ids:
        async with get_db_context() as db:
            event = await db.get(Event, event_id, with_for_update=True)
            if event is None:
                continue
            # Internal accepted events retain their original tenant and run
            # authority. No provider token or new webhook is needed to resume.
            queued = await EventProcessor(db).queue_event_deliveries(event_id)
            await db.commit()
        await queued.hand_off()
        recovered += queued.count
    return recovered


async def recover_provider_deliveries(context: PlatformJobContext) -> dict:
    async with get_db_context() as db:
        snapshot = await credentials(db)
        row = await fence(db, snapshot)
        started = datetime.now(timezone.utc)
        earliest = started - timedelta(days=PROVIDER_LOOKBACK_DAYS)
        cursor = max(row.recovery_cursor - RECOVERY_OVERLAP if row.recovery_cursor else started - timedelta(hours=24), earliest)
        if row.recovery_cursor and row.recovery_cursor < earliest:
            row.recovery_gap = True
        await db.commit()
    client = provider_client(snapshot)
    requested = 0
    complete = False
    saturated = False
    next_cursor = None
    seen_cursors = set()
    for _ in range(MAX_RECOVERY_PAGES):
        await context.report("Checking GitHub App recovery")
        page = await client.delivery_history(next_cursor)
        for delivery in page.entries:
            try:
                received = datetime.fromisoformat(delivery["delivered_at"].replace("Z", "+00:00"))
                delivery_id = int(delivery["id"])
                guid = str(delivery["guid"])
            except (KeyError, TypeError, ValueError):
                raise GitHubAppError("response", "GitHub returned an invalid delivery identity", 502) from None
            if received < cursor:
                continue
            if delivery.get("status_code") is not None and 200 <= delivery["status_code"] < 300:
                continue
            await context.report("Recovering failed GitHub deliveries")
            async with get_db_context() as db:
                row = await fence(db, snapshot)
                ledger = dict(row.redelivery_requests or {})
                accepted = await db.get(GitHubAppDelivery, (snapshot.app_id, guid))
                if accepted is not None:
                    # Already accepted internally, including a response lost on
                    # its way to GitHub. Local dispatch recovery handles it.
                    continue
                previous = next((attempt for attempt in ledger.values() if attempt["guid"] == guid), None)
                if previous:
                    # Never resend an uncertain administrative POST. A later
                    # local receipt or provider attempt is independent evidence.
                    row.recovery_gap = True
                    continue
                if len(ledger) >= 1000:
                    row.recovery_gap = True
                    saturated = True
                    await db.commit()
                    break
                ledger[str(delivery_id)] = {"guid": guid, "requested_at": started.isoformat(), "state": "sending"}
                row.redelivery_requests = ledger
                await db.commit()
            state = "uncertain"
            try:
                await client.request("POST", f"/app/hook/deliveries/{delivery_id}/attempts")
                state = "requested"
                requested += 1
            finally:
                async with get_db_context() as db:
                    row = await fence(db, snapshot)
                    ledger = dict(row.redelivery_requests)
                    ledger[str(delivery_id)] = {**ledger[str(delivery_id)], "state": state}
                    row.redelivery_requests = ledger
                    if state == "uncertain":
                        row.recovery_gap = True
                    await db.commit()
        if saturated:
            break
        next_cursor = page.next_cursor
        if next_cursor is None:
            complete = True
        if complete:
            break
        if next_cursor in seen_cursors:
            raise GitHubAppError("response", "GitHub repeated a delivery history cursor", 502)
        seen_cursors.add(next_cursor)
    async with get_db_context() as db:
        row = await fence(db, snapshot)
        if complete:
            row.recovery_cursor = started
        else:
            row.recovery_gap = True
        # Retain request identities for the full deduplication window. Their
        # existence fences ambiguous sends across job/process restarts.
        row.redelivery_requests = {key: value for key, value in row.redelivery_requests.items()
            if datetime.fromisoformat(value["requested_at"]) >= started - timedelta(days=DEDUP_RETENTION_DAYS)}
        await db.execute(delete(GitHubAppDelivery).where(GitHubAppDelivery.received_at < started - timedelta(days=DEDUP_RETENTION_DAYS)))
        await db.commit()
    return {"redeliveries_requested": requested, "history_scan_complete": complete}


async def reconcile(context: PlatformJobContext) -> dict:
    checkpoint = context.checkpoint or {}
    last_binding = checkpoint.get("last_binding")
    failed = 0
    refreshed = 0
    async with get_db_context() as db:
        app = await credentials(db)
        query = select(GitHubAppInstallation.id).where(
            GitHubAppInstallation.status != "disconnected", GitHubAppInstallation.app_id == app.app_id)
        if last_binding:
            query = query.where(GitHubAppInstallation.id > UUID(last_binding))
        ids = list((await db.execute(query.order_by(GitHubAppInstallation.id))).scalars())
    for index, binding_id in enumerate(ids):
        await context.report("Checking GitHub App recovery")
        await context.report("Verifying GitHub installations", current=index, total=len(ids))
        try:
            await refresh_installation(binding_id)
            refreshed += 1
        except GitHubAppError as exc:
            if exc.code in {"rate_limit", "transport", "busy", "configuration_changed"}:
                raise
            failed += 1
        await context.save_checkpoint({"last_binding": str(binding_id)}, phase="Verifying GitHub installations")
    internal = await recover_internal_events()
    provider = await recover_provider_deliveries(context)
    async with get_db_context() as db:
        row = await fence(db, app)
        row.last_reconciled_at = datetime.now(timezone.utc)
        await db.commit()
    return {"installations_refreshed": refreshed, "installations_failed": failed,
            "internal_deliveries_recovered": internal, **provider}
