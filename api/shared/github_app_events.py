"""Authenticated App ingress reusing canonical Event/EventDelivery acceptance."""

from datetime import datetime, timedelta, timezone
import hashlib
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from shared.github_app_auth import EVENT_PERMISSIONS, LIFECYCLE_EVENTS, GitHubAppError, parse_verified_webhook
from src.core.security import decrypt_secret
from src.core.rate_limit import RateLimiter
from src.models.orm.events import Event, EventSource, WebhookSource
from src.models.orm.github_app import GitHubAppDelivery, GitHubAppInstallation, GitHubAppRegistration
from src.services.events.processor import EventProcessor, QueuedDeliveries
from src.services.webhooks.protocol import Deliver, WebhookRequest


def installation_identity(payload: dict) -> int:
    installation = payload.get("installation")
    identity = installation.get("id") if isinstance(installation, dict) else None
    if type(identity) is not int or identity <= 0:
        raise GitHubAppError("installation", "Missing GitHub installation identity", 400)
    return identity


async def accept_webhook(db: AsyncSession, app_id: int, body: bytes, headers: dict[str, str]) -> QueuedDeliveries:
    # A shared App lock allows installations to ingest concurrently while key
    # replacement/disconnect takes an exclusive lock before changing authority.
    registration = await db.get(GitHubAppRegistration, 1, with_for_update={"read": True})
    if registration is None or not registration.enabled or registration.app_id != app_id or not registration.encrypted_webhook_secret:
        raise GitHubAppError("not_configured", "GitHub App webhook not found", 404)
    delivery_id, event_type, payload = parse_verified_webhook(body, headers, decrypt_secret(registration.encrypted_webhook_secret))
    digest = hashlib.sha256(body).hexdigest()
    identity = None if event_type == "ping" else installation_identity(payload)
    claimed = (await db.execute(insert(GitHubAppDelivery).values(app_id=app_id, delivery_id=delivery_id,
        body_sha256=digest, event_type=event_type, installation_id=identity,
        received_at=datetime.now(timezone.utc)).on_conflict_do_nothing().returning(GitHubAppDelivery.delivery_id))).scalar_one_or_none()
    if claimed is None:
        existing = (await db.execute(select(GitHubAppDelivery).where(
            GitHubAppDelivery.app_id == app_id, GitHubAppDelivery.delivery_id == delivery_id).with_for_update())).scalar_one()
        if existing.body_sha256 != digest or existing.event_type != event_type:
            raise GitHubAppError("delivery_conflict", "Delivery identity conflicts with an accepted event", 409)
        if existing.event_id:
            # All duplicates and local recovery lock the same Event before
            # accepting its pending runs. No dispatch can precede commit.
            await db.get(Event, existing.event_id, with_for_update=True)
            return await EventProcessor(db).queue_event_deliveries(existing.event_id)
        return QueuedDeliveries()
    delivery = await db.get(GitHubAppDelivery, (app_id, delivery_id))
    assert delivery is not None
    if event_type == "ping":
        return QueuedDeliveries()
    binding = (await db.execute(select(GitHubAppInstallation).where(
        GitHubAppInstallation.installation_id == identity,
        GitHubAppInstallation.app_id == app_id).with_for_update())).scalar_one_or_none()
    if event_type in LIFECYCLE_EVENTS:
        # Lifecycle notifications can only remove authority. Even an old
        # 'unsuspend' is reconciled against GitHub before becoming active.
        if binding and binding.status != "disconnected":
            binding.status = "revoked" if payload.get("action") == "deleted" else "stale"
            binding.generation = uuid4()
            binding.repositories = []
            binding.last_delivery_at = datetime.now(timezone.utc)
        return QueuedDeliveries()
    if binding is None or binding.status != "active" or binding.verified_at is None or binding.verified_at < datetime.now(timezone.utc) - timedelta(minutes=15):
        raise GitHubAppError("binding", "Installation binding is missing, inactive, or stale", 403)
    repository = payload.get("repository")
    if not isinstance(repository, dict) or type(repository.get("id")) is not int or repository["id"] not in {repo["id"] for repo in binding.repositories}:
        raise GitHubAppError("repository", "Repository is outside the verified installation binding", 403)
    required = EVENT_PERMISSIONS[event_type]
    if binding.permissions.get(required) not in {"read", "write"}:
        raise GitHubAppError("permissions", "Installation has not granted this event's required permission", 403)
    action = payload.get("action")
    if event_type != "repository" and (not isinstance(action, str) or not action or len(action) > 60):
        raise GitHubAppError("action", "Missing or invalid GitHub event action", 400)
    source = await db.get(EventSource, binding.event_source_id)
    if source is None or not source.is_active or source.organization_id != binding.organization_id:
        raise GitHubAppError("source", "Installation event source is unavailable", 409)
    webhook_source = (await db.execute(select(WebhookSource).where(WebhookSource.event_source_id == source.id))).scalar_one()
    if webhook_source.rate_limit_enabled and webhook_source.rate_limit_per_minute is not None:
        limiter = RateLimiter(max_requests=webhook_source.rate_limit_per_minute,
                              window_seconds=webhook_source.rate_limit_window_seconds)
        try:
            await limiter.check("webhook_ingress", str(source.id), force=True)
        except HTTPException as exc:
            if exc.status_code != 429:
                raise
            retry = (exc.headers or {}).get("Retry-After", "60")
            raise GitHubAppError("rate_limit", "GitHub App event source rate limit reached", 429,
                                 int(retry) if retry.isdigit() else 60) from None
    normalized = {"provider": "github", "delivery_id": delivery_id, "event": event_type, "action": action,
        "installation_id": identity, "repository_id": repository["id"], "payload": payload}
    # Tenant data is stamped from the binding, never from payload fields.
    request = WebhookRequest("POST", f"/api/hooks/github-app/{app_id}", headers, {}, body)
    result = await EventProcessor(db).accept_verified_webhook(source,
        Deliver(normalized, f"github.{event_type}.{action}" if action else f"github.{event_type}",
                {key: value for key, value in headers.items() if key in {"x-github-delivery", "x-github-event"}}), request)
    delivery.event_id = result.event_id
    binding.last_delivery_at = datetime.now(timezone.utc)
    return await EventProcessor(db).queue_event_deliveries(result.event_id) if result.event_id else QueuedDeliveries()
