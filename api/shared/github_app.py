"""GitHub App configuration and verified, tenant-scoped installation state."""

from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4
from typing import Awaitable, cast

from fastapi import HTTPException
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from shared.github_app_auth import EVENT_PERMISSIONS, GitHubAppError, validate_private_key
from shared.github_app_client import AppCredentials, GitHubAppClient
from shared.models import GitHubAppBind, GitHubAppConfigure, GitHubAppConfigView, GitHubAppInstallationView
from src.core.database import get_db_context
from src.core.cache.redis_client import get_redis
from src.core.security import decrypt_secret, encrypt_secret
from src.models.enums import EventSourceType
from src.models.orm.events import EventSource, WebhookSource
from src.models.orm.github_app import GitHubAppInstallation, GitHubAppRegistration
from src.models.orm.organizations import Organization
from src.models.contracts.events import EventSourceUpdate


SOURCE_OWNERSHIP_MESSAGE = "This event source is managed by GitHub App settings; disconnect or rebind the installation there"


async def is_managed_event_source(db: AsyncSession, source: EventSource) -> bool:
    """Keep binding ownership and historical event scope under App administration.

    The adapter marker also protects disconnected historical sources after a
    rebind moves the installation's foreign key to a fresh source.
    """
    if source.source_type != EventSourceType.WEBHOOK:
        return False
    managed = source.webhook_source and source.webhook_source.adapter_name == "github_app"
    return bool(managed or await db.scalar(select(GitHubAppInstallation.id).where(GitHubAppInstallation.event_source_id == source.id)) is not None)


async def assert_source_structure_mutable(db: AsyncSession, source: EventSource) -> None:
    if await is_managed_event_source(db, source):
        raise HTTPException(409, SOURCE_OWNERSHIP_MESSAGE)


async def validate_event_source_update(db: AsyncSession, source: EventSource, request: EventSourceUpdate) -> bool:
    managed = await is_managed_event_source(db, source)
    if not managed:
        return False
    organization_changed = "organization_id" in request.model_fields_set and request.organization_id != source.organization_id
    webhook_changed = request.webhook is not None and (source.webhook_source is None or any(
        field in request.webhook.model_fields_set and getattr(request.webhook, field) != getattr(source.webhook_source, field)
        for field in ("adapter_name", "integration_id", "config")))
    if organization_changed or webhook_changed:
        raise HTTPException(409, SOURCE_OWNERSHIP_MESSAGE)
    return True


async def source_webhook_details(db: AsyncSession, source_id: UUID) -> tuple[str, bool] | None:
    binding = await db.scalar(select(GitHubAppInstallation).where(GitHubAppInstallation.event_source_id == source_id))
    if binding is None:
        return None
    registration = await db.get(GitHubAppRegistration, 1)
    configured = bool(registration and registration.enabled and registration.app_id == binding.app_id and registration.encrypted_webhook_secret)
    return f"/api/hooks/github-app/{binding.app_id}", configured


def config_view(row: GitHubAppRegistration | None) -> GitHubAppConfigView:
    if row is None:
        return GitHubAppConfigView()
    return GitHubAppConfigView(configured=row.enabled, app_id=row.app_id, slug=row.slug,
        private_key_set=bool(row.encrypted_private_key), webhook_secret_set=bool(row.encrypted_webhook_secret),
        webhook_path=f"/api/hooks/github-app/{row.app_id}", last_reconciled_at=row.last_reconciled_at,
        recovery_gap=row.recovery_gap)


def installation_view(row: GitHubAppInstallation) -> GitHubAppInstallationView:
    status = row.status
    if status == "active" and (row.verified_at is None or row.verified_at < datetime.now(timezone.utc) - timedelta(minutes=15)):
        status = "stale"
    return GitHubAppInstallationView(id=row.id, installation_id=row.installation_id,
        organization_id=row.organization_id, account_login=row.account_login, status=status,
        permissions=row.permissions, repositories=row.repositories, event_source_id=row.event_source_id,
        verified_at=row.verified_at, last_delivery_at=row.last_delivery_at, last_error=row.last_error,
        available_events=[event for event, permission in EVENT_PERMISSIONS.items() if row.permissions.get(permission) in {"read", "write"}])


async def list_installations(db: AsyncSession, organization_id: UUID) -> list[GitHubAppInstallationView]:
    rows = (await db.execute(select(GitHubAppInstallation).where(
        GitHubAppInstallation.organization_id == organization_id).order_by(GitHubAppInstallation.account_login))).scalars().all()
    views = []
    async with get_redis() as redis:
        for row in rows:
            view = installation_view(row)
            budget = f"github-app:budget:{row.app_id}:{row.installation_id}"
            health = await cast(Awaitable[dict[str, str]], redis.hgetall(budget + ":health"))
            view.quota_remaining = int(health["remaining"]) if health.get("remaining", "").isdigit() else None
            view.quota_reset = int(health["reset"]) if health.get("reset", "").isdigit() else None
            cooldown = await redis.ttl(budget + ":cooldown")
            view.retry_after = cooldown if cooldown > 0 else None
            views.append(view)
    return views


async def disconnect_installation(db: AsyncSession, binding_id: UUID) -> GitHubAppInstallationView:
    await db.get(GitHubAppRegistration, 1, with_for_update=True)
    row = await db.get(GitHubAppInstallation, binding_id, with_for_update=True)
    if row is None:
        raise GitHubAppError("not_found", "Installation binding not found", 404)
    row.status, row.generation, row.repositories = "disconnected", uuid4(), []
    return installation_view(row)


async def credentials(db: AsyncSession) -> AppCredentials:
    row = await db.get(GitHubAppRegistration, 1, populate_existing=True)
    if row is None or not row.enabled or not row.encrypted_private_key:
        raise GitHubAppError("not_configured", "GitHub App is not configured", 409)
    return AppCredentials(row.app_id, str(row.generation), decrypt_secret(row.encrypted_private_key))


async def fence(db: AsyncSession, snapshot: AppCredentials) -> GitHubAppRegistration:
    row = await db.get(GitHubAppRegistration, 1, populate_existing=True, with_for_update=True)
    if row is None or not row.enabled or str(row.generation) != snapshot.generation or row.app_id != snapshot.app_id:
        raise GitHubAppError("configuration_changed", "GitHub App configuration changed; restart the operation", 409)
    return row


def provider_client(snapshot: AppCredentials, binding_id: UUID | None = None, generation: UUID | None = None) -> GitHubAppClient:
    async def authorize() -> None:
        async with get_db_context() as db:
            row = await db.get(GitHubAppRegistration, 1)
            if row is None or not row.enabled or row.app_id != snapshot.app_id or str(row.generation) != snapshot.generation:
                raise GitHubAppError("configuration_changed", "GitHub App configuration changed; restart the operation", 409)
            if binding_id:
                binding = await db.get(GitHubAppInstallation, binding_id)
                if binding is None or binding.generation != generation or binding.status == "disconnected":
                    raise GitHubAppError("binding_changed", "Installation binding changed during the operation", 409)
    return GitHubAppClient(snapshot, authorize)


async def configure(db: AsyncSession, request: GitHubAppConfigure) -> GitHubAppConfigView:
    previous = await db.get(GitHubAppRegistration, 1)
    previous_generation = previous.generation if previous else None
    private_key = request.private_key.get_secret_value() if request.private_key else (
        decrypt_secret(previous.encrypted_private_key) if previous and previous.enabled and previous.encrypted_private_key else None)
    secret = request.webhook_secret.get_secret_value() if request.webhook_secret else (
        decrypt_secret(previous.encrypted_webhook_secret) if previous and previous.enabled and previous.encrypted_webhook_secret else None)
    if not private_key or not secret:
        raise GitHubAppError("credentials", "A private key and webhook secret are required")
    validate_private_key(private_key)
    await db.commit()  # Do not retain a DB transaction across GitHub I/O.
    verified = await GitHubAppClient(AppCredentials(request.app_id, str(uuid4()), private_key)).request("GET", "/app")
    if not isinstance(verified, dict) or verified.get("id") != request.app_id or not isinstance(verified.get("slug"), str):
        raise GitHubAppError("app_identity", "The private key does not identify this GitHub App", 409)
    # Serialize first configuration too: a missing singleton row cannot take
    # a row lock. Concurrent first saves must fail by generation, not leak
    # encrypted credential parameters through an IntegrityError log.
    await db.execute(select(func.pg_advisory_xact_lock(func.hashtextextended("github-app:registration", 0))))
    row = await db.get(GitHubAppRegistration, 1, populate_existing=True, with_for_update=True)
    if (row.generation if row else None) != previous_generation:
        raise GitHubAppError("configuration_changed", "GitHub App configuration changed; restart the operation", 409)
    if row is None:
        row = GitHubAppRegistration(id=1, app_id=request.app_id, slug=verified["slug"])
        db.add(row)
    else:
        # No existing binding silently survives a key/App identity replacement.
        await db.execute(update(GitHubAppInstallation).where(
            GitHubAppInstallation.status != "disconnected").values(
                status="stale" if row.app_id == request.app_id else "disconnected", generation=uuid4(), repositories=[]))
        if row.app_id != request.app_id:
            row.recovery_cursor = None
            row.redelivery_requests = {}
            row.recovery_gap = False
            row.last_reconciled_at = None
    row.app_id = request.app_id
    row.slug = verified["slug"]
    row.encrypted_private_key = encrypt_secret(private_key)
    row.encrypted_webhook_secret = encrypt_secret(secret)
    row.generation = uuid4()
    row.enabled = True
    row.updated_at = datetime.now(timezone.utc)
    await db.flush()
    return config_view(row)


async def disable(db: AsyncSession) -> None:
    row = await db.get(GitHubAppRegistration, 1, with_for_update=True)
    if row is None:
        return
    row.enabled = False
    row.generation = uuid4()
    row.encrypted_private_key = None
    row.encrypted_webhook_secret = None
    await db.execute(update(GitHubAppInstallation).values(status="disconnected", generation=uuid4(), repositories=[]))


async def bind(db: AsyncSession, request: GitHubAppBind, created_by: str) -> GitHubAppInstallation:
    if await db.get(Organization, request.organization_id) is None:
        raise GitHubAppError("organization", "Bifrost organization not found", 404)
    snapshot = await credentials(db)
    await db.commit()
    verified = await provider_client(snapshot).installation(request.installation_id)
    account = verified.get("account")
    if not isinstance(account, dict) or not isinstance(account.get("id"), int) or not isinstance(account.get("login"), str):
        raise GitHubAppError("response", "GitHub returned an invalid installation account", 502)
    await fence(db, snapshot)
    existing = (await db.execute(select(GitHubAppInstallation).where(
        GitHubAppInstallation.installation_id == request.installation_id).with_for_update())).scalar_one_or_none()
    if existing:
        if existing.status != "disconnected" and (existing.organization_id != request.organization_id or existing.app_id != snapshot.app_id):
            raise GitHubAppError("binding_conflict", "Installation is already claimed; disconnect and explicitly resolve its ownership", 409)
        if existing.status != "disconnected":
            raise GitHubAppError("binding_exists", "Installation is already bound; refresh its inventory", 409)
        previous_source = await db.get(EventSource, existing.event_source_id)
        if previous_source:
            previous_source.is_active = False
    source = EventSource(id=uuid4(), name=f"GitHub: {account['login']} ({request.installation_id})",
        source_type=EventSourceType.WEBHOOK, organization_id=request.organization_id, created_by=created_by)
    db.add(source)
    await db.flush()
    db.add(WebhookSource(event_source_id=source.id, adapter_name="github_app", config={}, state={}))
    row = existing or GitHubAppInstallation(installation_id=request.installation_id)
    row.app_id = snapshot.app_id
    row.organization_id = request.organization_id
    row.event_source_id = source.id
    row.account_login, row.account_id = account["login"], account["id"]
    row.permissions, row.repositories, row.status = verified.get("permissions", {}), [], "stale"
    row.generation = uuid4()
    row.verified_at, row.last_delivery_at, row.last_error = None, None, None
    if existing is None:
        db.add(row)
    await db.flush()
    return row


async def refresh_installation(binding_id: UUID) -> dict:
    async with get_db_context() as db:
        snapshot = await credentials(db)
        row = await db.get(GitHubAppInstallation, binding_id)
        if row is None or row.status == "disconnected" or row.app_id != snapshot.app_id:
            raise GitHubAppError("disconnected", "Installation is disconnected or belongs to a different App", 409)
        generation, installation_id, account_id = row.generation, row.installation_id, row.account_id
    try:
        client = provider_client(snapshot, binding_id, generation)
        verified = await client.installation(installation_id)
        if verified.get("account", {}).get("id") != account_id:
            raise GitHubAppError("account_changed", "Installation account identity changed", 409)
        repos = await client.repositories(installation_id, str(generation))
    except GitHubAppError as exc:
        async with get_db_context() as db:
            current = await db.get(GitHubAppInstallation, binding_id, with_for_update=True)
            if current and current.generation == generation:
                current.last_error = str(exc)
                if exc.code in {"installation_missing", "suspended", "credentials", "permissions", "account_changed"}:
                    current.status = "suspended" if exc.code == "suspended" else "unavailable"
                    current.generation = uuid4()
                    current.repositories = []
                await db.commit()
        raise
    async with get_db_context() as db:
        await fence(db, snapshot)
        current = await db.get(GitHubAppInstallation, binding_id, with_for_update=True, populate_existing=True)
        if current is None or current.generation != generation or current.status == "disconnected":
            raise GitHubAppError("binding_changed", "Installation binding changed during refresh", 409)
        current.permissions = verified.get("permissions", {})
        current.repositories = repos
        current.account_login = verified["account"]["login"]
        current.verified_at = datetime.now(timezone.utc)
        current.status = "active"
        current.last_error = None
        await db.commit()
    return {"installation_id": installation_id, "repository_count": len(repos)}
