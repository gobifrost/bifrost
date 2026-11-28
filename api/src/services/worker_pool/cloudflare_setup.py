"""Canonical settings and PlatformJob submission for Cloudflare capacity."""

from __future__ import annotations

import ipaddress
from urllib.parse import urlsplit

from sqlalchemy.ext.asyncio import AsyncSession

from shared.models import (
    CloudflareCapacityAccount,
    CloudflareCapacityApplyPayload,
    CloudflareCapacityConnectAccepted,
    CloudflareCapacityConnectRequest,
    CloudflareCapacityRules,
    CloudflareCapacityStatus,
)
from src.config import Settings
from src.core.principal import UserPrincipal
from src.models.orm.platform_jobs import PlatformJob
from src.services.platform_jobs import enqueue_platform_job
from src.services.worker_pool.connection import (
    CapacityConnectionError,
    lock_connection,
    load_cloudflare_connection,
    stage_cloudflare_api_key,
    update_cloudflare_connection_metadata,
)


def public_api_origin(value: str) -> str:
    """Cloud workers need the installation's externally reachable HTTPS origin."""
    try:
        parsed = urlsplit(value)
        host = parsed.hostname
        _ = parsed.port
        if (parsed.scheme != "https" or not host or parsed.username or parsed.password
                or parsed.query or parsed.fragment or parsed.path not in ("", "/")
                or host == "localhost" or host.endswith(".localhost")):
            raise ValueError
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            address = None
        if address is not None and not address.is_global:
            raise ValueError
        return value.rstrip("/")
    except ValueError:
        raise CapacityConnectionError("Cloudflare workers need a public HTTPS URL for this Bifrost installation") from None


async def describe_cloudflare_capacity(db: AsyncSession) -> CloudflareCapacityStatus:
    connection = await load_cloudflare_connection(db)
    if connection is None:
        return CloudflareCapacityStatus()
    metadata = connection.metadata
    status, error = metadata.get("status", "provisioning"), metadata.get("error")
    job_id = metadata.get("last_job_id")
    if job_id is not None:
        from uuid import UUID

        job = await db.get(PlatformJob, UUID(job_id))
        if job is not None and status == "provisioning" and job.status in ("failed", "cancelled"):
            status = "failed"
            error = "Cloudflare setup did not complete. Retry setup to reconcile its resources."
    deployment = metadata.get("deployment", {})
    return CloudflareCapacityStatus(
        configured=True, active=metadata.get("enabled") is True,
        status=status, account_id=metadata.get("account_id"),
        accounts=[CloudflareCapacityAccount.model_validate(item) for item in metadata.get("accounts", [])],
        endpoint=metadata.get("endpoint"), scope=connection.scope,
        image_digest=deployment.get("image_digest"), worker_version=deployment.get("worker_version"),
        max_workers=deployment.get("max_workers"), memory_limit_bytes=deployment.get("memory_limit_bytes"),
        last_job_id=job_id, error=error,
        rules=CloudflareCapacityRules.model_validate(metadata.get("rules", {})),
    )


async def update_cloudflare_rules(
    db: AsyncSession, changes: CloudflareCapacityRules,
) -> CloudflareCapacityStatus:
    await lock_connection(db)
    connection = await load_cloudflare_connection(db)
    if connection is None:
        raise CapacityConnectionError("Connect Cloudflare before editing its capacity rules")
    current = CloudflareCapacityRules.model_validate(connection.metadata.get("rules", {}))
    rules = CloudflareCapacityRules.model_validate({
        **current.model_dump(), **changes.model_dump(exclude_unset=True),
    })
    if not await update_cloudflare_connection_metadata(db, connection.generation, {"rules": rules.model_dump()}):
        raise CapacityConnectionError("The Cloudflare connection changed; refresh its settings")
    return await describe_cloudflare_capacity(db)


async def enqueue_cloudflare_setup(
    db: AsyncSession, request: CloudflareCapacityConnectRequest, *,
    user: UserPrincipal, settings: Settings,
) -> tuple[CloudflareCapacityConnectAccepted, PlatformJob]:
    """Save the write-only key and durable apply reference in one transaction."""
    if settings.worker_capacity_url or settings.worker_capacity_token:
        raise CapacityConnectionError("Remove deployment capacity credentials before connecting Cloudflare in Settings")
    await lock_connection(db)
    previous = await load_cloudflare_connection(db)
    # A runtime's authenticated control channel must terminate at the
    # deployment-owned origin, never at a caller-selected HTTPS endpoint.
    origin = public_api_origin(settings.public_url)
    if request.bifrost_api_origin is not None and public_api_origin(request.bifrost_api_origin) != origin:
        raise CapacityConnectionError("Use this installation's configured public HTTPS URL")
    maximum = request.max_workers if request.max_workers is not None else (
        previous.metadata.get("desired_max_workers", 2) if previous is not None else 2
    )
    key = request.api_key.get_secret_value() if request.api_key is not None else (
        previous.api_key if previous is not None else None
    )
    if key is None:
        raise CapacityConnectionError("A Cloudflare API token is required")
    connection = await stage_cloudflare_api_key(db, key, updated_by=user.email)
    from src.jobs.platform.cloudflare_capacity import CLOUDFLARE_CAPACITY_APPLY_DEFINITION

    job, _ = await enqueue_platform_job(
        db, CLOUDFLARE_CAPACITY_APPLY_DEFINITION,
        CloudflareCapacityApplyPayload(generation=connection.generation),
        dedupe_key=str(connection.generation), organization_id=None,
        requested_by_user_id=user.user_id, requested_by_email=user.email,
        requested_by_name=user.name or user.email, resource_type="worker_capacity",
        resource_id="cloudflare", title="Setting Up Cloudflare Workers", action_url="/settings/kubernetes-executions",
    )
    await update_cloudflare_connection_metadata(db, connection.generation, {
        "requested_account_id": request.account_id,
        "desired_bifrost_api_origin": origin, "desired_max_workers": maximum,
        "last_job_id": str(job.id), "error": None,
    })
    return CloudflareCapacityConnectAccepted(
        job_id=job.id, connection=await describe_cloudflare_capacity(db),
    ), job
