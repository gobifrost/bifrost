"""API-owned Cloudflare setup through the canonical durable job runner."""

from __future__ import annotations

import asyncio
from dataclasses import asdict
from typing import Any, Mapping

import httpx

from shared.models import CloudflareCapacityAccount, CloudflareCapacityApplyPayload
from shared.version import get_version
from shared.worker_compute import APP_COMPILE_CAPABILITY_NAME
from src.config import get_settings
from src.core.database import get_db_context
from src.jobs.platform.base import (
    PlatformJobCancelled, PlatformJobContext, PlatformJobFailure,
    PlatformJobRequiresAction, define_platform_job,
)
from src.services.worker_pool.cloudflare_deploy import (
    CLOUDFLARE_WORKER_ARTIFACT_DIRECTORY, CapacityToken, CloudflareApiToken,
    CloudflareDeployment, CloudflareDeploymentAdapter, CloudflareDeploymentLimits,
    CloudflareProvisioningError, CloudflareProvisioningUncertain,
)
from src.services.worker_pool.cloudflare_image import CloudflareImageCopier, CloudflareImageError
from src.services.worker_pool.connection import (
    CapacityConnectionError, activate_cloudflare_connection,
    load_cloudflare_connection, update_cloudflare_connection_metadata,
)
from src.services.worker_pool.external_client import ExternalCapacityClient

MEMORY_LIMIT_BYTES = 4 * 1024**3
EXECUTION_HANDLERS = ("workflow.execute", "agent.run", "agent.summarize", "agent.summarize_backfill")
COMPUTE_CAPABILITIES = (APP_COMPILE_CAPABILITY_NAME, "agent.loop.v1", "summary.compute.v1")


async def run_cloudflare_capacity_apply(
    context: PlatformJobContext, payload: CloudflareCapacityApplyPayload,
) -> dict[str, Any]:
    async with get_db_context() as db:
        connection = await load_cloudflare_connection(db)
    if connection is None or connection.generation != payload.generation:
        raise PlatformJobCancelled

    async def state(changes: dict[str, Any]) -> None:
        async with get_db_context() as db:
            if not await context.authorize_effect(db):
                raise PlatformJobCancelled
            if not await update_cloudflare_connection_metadata(db, payload.generation, changes):
                raise PlatformJobCancelled
            await db.commit()

    receipt: dict[str, Any] = dict(context.checkpoint or {})

    async def checkpoint(step: str, data: Mapping[str, Any]) -> None:
        receipt.update(data)
        await context.save_checkpoint(dict(receipt), phase=step.replace("_", " ").capitalize())

    try:
        settings = get_settings()
        await context.report("Checking Cloudflare account access", percent=5)
        async with httpx.AsyncClient() as http:
            adapter = CloudflareDeploymentAdapter(http, CLOUDFLARE_WORKER_ARTIFACT_DIRECTORY, checkpoint=checkpoint)
            accounts = [CloudflareCapacityAccount.model_validate({"id": item.get("id"), "name": item.get("name")})
                        for item in await adapter.list_accounts(CloudflareApiToken(connection.api_key))]
            if not accounts:
                raise CapacityConnectionError("The API token cannot access any Cloudflare accounts")
            account_id = connection.metadata.get("requested_account_id") or connection.metadata.get("account_id")
            if account_id is None and len(accounts) == 1:
                account_id = accounts[0].id
            if account_id is None:
                await state({"status": "account_selection_required", "accounts": [account.model_dump() for account in accounts]})
                raise PlatformJobRequiresAction("Choose a Cloudflare account", {
                    "accounts": [account.model_dump() for account in accounts],
                })
            if not any(account.id == account_id for account in accounts):
                raise CapacityConnectionError("The API token cannot access the selected Cloudflare account")
            if connection.metadata.get("account_id") not in (None, account_id):
                raise CapacityConnectionError("Drain the existing Cloudflare account before replacing it")
            if not isinstance(account_id, str):
                raise CapacityConnectionError("A Cloudflare account is required")
            await state({"accounts": [account.model_dump() for account in accounts]})
            await context.report("Preparing the matching worker image", percent=15)
            # This subprocess belongs to the API's shared maintenance executor.
            # Streamed image I/O never runs in the HTTP server's event loop.
            image = await asyncio.to_thread(
                CloudflareImageCopier().copy,
                source=settings.worker_capacity_image, expected_version=get_version(),
                account_id=account_id, repository="bifrost-" + connection.scope.split(":")[-1],
                api_token=connection.api_key,
            )
            receipt.update(account_id=account_id, registry_image=image.reference,
                           image_digest=image.digest, worker_version=image.worker_version)
            await checkpoint("worker_image_verified", {})
            maximum = connection.metadata["desired_max_workers"]
            spec = CloudflareDeployment(
                account_id=account_id, script_name="bifrost-" + connection.scope.split(":")[-1],
                scope=connection.scope, bifrost_api_origin=connection.metadata["desired_bifrost_api_origin"],
                worker_version=image.worker_version, registry_image=image.reference,
                limits=CloudflareDeploymentLimits(
                    memory_limit_bytes=MEMORY_LIMIT_BYTES, max_workers=maximum,
                    max_lifetime_seconds=settings.pool_max_lifetime_seconds, starting_grace_ms=30_000,
                    execution_handlers=EXECUTION_HANDLERS, compute_capabilities=COMPUTE_CAPABILITIES,
                ),
            )
            await context.report("Provisioning Cloudflare workers", percent=40)
            deployed = await adapter.reconcile(
                spec, api_token=CloudflareApiToken(connection.api_key),
                capacity_token=CapacityToken(connection.capacity_token),
            )
            await context.report("Verifying the worker provider", percent=90)
            profile = await ExternalCapacityClient(deployed.worker_url, connection.capacity_token, http=http).profile()
            if (profile.scope != connection.scope or profile.worker_version != image.worker_version
                    or profile.memory_limit_bytes != MEMORY_LIMIT_BYTES or profile.max_workers != maximum
                    or set(profile.execution_handlers) != set(EXECUTION_HANDLERS)
                    or set(profile.compute_capabilities) != set(COMPUTE_CAPABILITIES)):
                raise CapacityConnectionError("The provisioned worker provider does not match this installation")
        public_deployment = {
            **asdict(deployed), "image_digest": image.digest, "registry_image": image.reference,
            "worker_version": image.worker_version, "max_workers": maximum,
            "memory_limit_bytes": MEMORY_LIMIT_BYTES,
        }
        async with get_db_context() as db:
            if not await context.authorize_effect(db) or not await activate_cloudflare_connection(
                db, payload.generation, endpoint=deployed.worker_url, account_id=account_id,
                deployment=public_deployment,
            ):
                raise PlatformJobCancelled
            await db.commit()
        await context.report("Cloudflare workers are ready to scale", percent=100)
        return public_deployment
    except PlatformJobRequiresAction:
        raise
    except (CloudflareProvisioningError, CloudflareImageError, CapacityConnectionError) as error:
        await state({"status": "failed", "error": str(error)})
        raise PlatformJobFailure(
            "cloudflare_setup_failed", str(error), retryable=isinstance(error, CloudflareProvisioningUncertain),
        ) from None
    except httpx.HTTPError:
        message = "The deployed Cloudflare provider could not be verified. Retry setup to reconcile its resources."
        await state({"status": "failed", "error": message})
        raise PlatformJobFailure("cloudflare_provider_unavailable", message, retryable=True) from None


CLOUDFLARE_CAPACITY_APPLY_DEFINITION = define_platform_job(
    "cloudflare.capacity.apply", CloudflareCapacityApplyPayload, run_cloudflare_capacity_apply,
)
