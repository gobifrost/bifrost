"""Encrypted installation-owned capacity credentials, separate from pool policy."""

from __future__ import annotations

import hashlib
import json
import secrets
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from shared.models import CloudflareCapacityRules
from src.config import Settings
from src.core.security import decrypt_secret, encrypt_secret
from src.models.orm.config import SystemConfig
from src.models.orm.platform_jobs import PlatformJob
from src.services.platform_jobs import ACTIVE_PLATFORM_JOB_STATUSES
from src.services.worker_pool.external_client import ExternalCapacityClient

CATEGORY = "worker_capacity"
KEY = "cloudflare"
LOCK_ID = int.from_bytes(hashlib.sha256(b"bifrost:cloudflare-capacity-connection").digest()[:8], signed=True)


class CapacityConnectionError(ValueError):
    """A sanitized configuration error; never contains credential material."""


@dataclass(frozen=True)
class CapacityConnection:
    endpoint: str
    token: str = field(repr=False)
    generation: UUID | None = None
    rules: CloudflareCapacityRules = field(default_factory=CloudflareCapacityRules)


@dataclass(frozen=True)
class CloudflareConnection:
    generation: UUID
    scope: str
    metadata: dict[str, Any] = field(repr=False)
    api_key: str = field(repr=False)
    capacity_token: str = field(repr=False)


_METADATA_UPDATE_FIELDS = frozenset({
    "status", "accounts", "last_job_id", "error", "requested_account_id",
    "desired_bifrost_api_origin", "desired_max_workers", "rules",
})
_SECRET_METADATA_FIELDS = frozenset({"api_key", "token", "capacity_token", "authorization", "password", "secret"})


async def lock_connection(db: AsyncSession) -> None:
    await db.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": LOCK_ID})


async def _row(db: AsyncSession) -> SystemConfig | None:
    result = await db.execute(select(SystemConfig).where(
        SystemConfig.organization_id.is_(None),
        SystemConfig.category == CATEGORY,
        SystemConfig.key == KEY,
    ).execution_options(populate_existing=True))
    return result.scalar_one_or_none()


async def _active_apply_exists(db: AsyncSession) -> bool:
    """Whether the canonical connection still has an unfinished apply job."""
    return (await db.scalar(select(PlatformJob.id).where(
        PlatformJob.job_type == "cloudflare.capacity.apply",
        PlatformJob.resource_type == "worker_capacity",
        PlatformJob.resource_id == "cloudflare",
        PlatformJob.status.in_(ACTIVE_PLATFORM_JOB_STATUSES),
    ).limit(1))) is not None


def _contains_secret_field(value: object) -> bool:
    if isinstance(value, dict):
        return any(
            isinstance(key, str) and key.lower() in _SECRET_METADATA_FIELDS
            or _contains_secret_field(nested)
            for key, nested in value.items()
        )
    if isinstance(value, (list, tuple)):
        return any(_contains_secret_field(item) for item in value)
    return False


def _validate_metadata_changes(changes: dict[str, Any]) -> None:
    if set(changes) - _METADATA_UPDATE_FIELDS or _contains_secret_field(changes):
        raise CapacityConnectionError("Invalid Cloudflare connection metadata update")
    string_fields = {
        "status", "last_job_id", "error", "requested_account_id", "desired_bifrost_api_origin",
    }
    if any(
        value is not None and not isinstance(value, str)
        for key, value in changes.items() if key in string_fields
    ):
        raise CapacityConnectionError("Invalid Cloudflare connection metadata update")
    if "desired_max_workers" in changes and (
        not isinstance(changes["desired_max_workers"], int)
        or isinstance(changes["desired_max_workers"], bool)
    ):
        raise CapacityConnectionError("Invalid Cloudflare connection metadata update")
    if "accounts" in changes and (
        not isinstance(changes["accounts"], list)
        or any(
            not isinstance(account, dict)
            or set(account) != {"id", "name"}
            or not all(isinstance(value, str) and value for value in account.values())
            for account in changes["accounts"]
        )
    ):
        raise CapacityConnectionError("Invalid Cloudflare connection metadata update")
    if "rules" in changes:
        try:
            CloudflareCapacityRules.model_validate(changes["rules"])
        except ValueError:
            raise CapacityConnectionError("Invalid Cloudflare capacity rules") from None


def _decode(row: SystemConfig) -> CloudflareConnection:
    try:
        if not row.value_bytes or not isinstance(row.value_json, dict):
            raise ValueError
        secret = json.loads(decrypt_secret(row.value_bytes.decode("ascii")))
        metadata = dict(row.value_json)
        api_key, token = secret["api_key"], secret["capacity_token"]
        generation, scope = UUID(metadata["generation"]), metadata["scope"]
        if (secret["version"] != 1 or not isinstance(api_key, str) or not api_key
                or not isinstance(token, str) or not isinstance(scope, str) or not scope):
            raise ValueError
        ExternalCapacityClient.check_configuration("https://capacity.invalid", token)
    except Exception:
        raise CapacityConnectionError("Stored Cloudflare capacity connection is invalid") from None
    return CloudflareConnection(generation, scope, metadata, api_key, token)


async def load_cloudflare_connection(db: AsyncSession) -> CloudflareConnection | None:
    row = await _row(db)
    return _decode(row) if row is not None else None


async def stage_cloudflare_api_key(
    db: AsyncSession, api_key: str, *, updated_by: str,
) -> CloudflareConnection:
    """Stage a key under the caller's enqueue transaction; retain control identity.

    A failed replacement key does not alter an existing endpoint or its worker
    authentication. The provisioning job must activate its exact generation.
    """
    if not api_key or len(api_key) > 512 or not api_key.isascii() or any(c.isspace() for c in api_key):
        raise CapacityConnectionError("A valid Cloudflare API token is required")
    await lock_connection(db)
    if await _active_apply_exists(db):
        raise CapacityConnectionError("A Cloudflare capacity apply is already in progress")
    row = await _row(db)
    previous = _decode(row) if row is not None else None
    metadata = dict(previous.metadata) if previous is not None else {
        "scope": f"bifrost:{uuid4()}", "enabled": False,
    }
    metadata.update(generation=str(uuid4()), status="provisioning", error=None)
    token = previous.capacity_token if previous is not None else secrets.token_urlsafe(48)
    if row is None:
        row = SystemConfig(category=CATEGORY, key=KEY, organization_id=None, created_by=updated_by)
        db.add(row)
    row.value_json = metadata
    row.value_bytes = encrypt_secret(json.dumps({
        "version": 1, "api_key": api_key, "capacity_token": token,
    })).encode("ascii")
    row.updated_by = updated_by
    await db.flush()
    return _decode(row)


async def activate_cloudflare_connection(
    db: AsyncSession, generation: UUID, *, endpoint: str, account_id: str,
    deployment: dict[str, Any],
) -> bool:
    """Publish verified provider metadata only for the current apply operation."""
    if _contains_secret_field(deployment):
        raise CapacityConnectionError("Connection deployment metadata cannot contain credentials")
    await lock_connection(db)
    row = await _row(db)
    if row is None:
        return False
    connection = _decode(row)
    if connection.generation != generation:
        return False
    endpoint = ExternalCapacityClient.check_configuration(endpoint, connection.capacity_token)
    previous_account = connection.metadata.get("account_id")
    if previous_account is not None and previous_account != account_id:
        raise CapacityConnectionError("Drain the existing Cloudflare account before replacing it")
    row.value_json = {
        **connection.metadata, "endpoint": endpoint, "account_id": account_id,
        "enabled": True, "status": "ready", "error": None, "deployment": deployment,
        "active_generation": str(generation),
    }
    await db.flush()
    return True


async def update_cloudflare_connection_metadata(
    db: AsyncSession, generation: UUID, changes: dict[str, Any],
) -> bool:
    """Checkpoint non-secret apply status only while its staged generation is current."""
    _validate_metadata_changes(changes)
    await lock_connection(db)
    row = await _row(db)
    if row is None:
        return False
    connection = _decode(row)
    if connection.generation != generation:
        return False
    row.value_json = {**connection.metadata, **changes}
    await db.flush()
    return True


async def load_capacity_provider_connection(
    db: AsyncSession, settings: Settings,
) -> CapacityConnection | None:
    """One source of truth for provisioning and external worker authentication."""
    stored = await load_cloudflare_connection(db)
    env_url, env_token = settings.worker_capacity_url, settings.worker_capacity_token
    if stored is not None and (env_url or env_token):
        raise CapacityConnectionError("Remove deployment capacity credentials before using the stored connection")
    if env_url or env_token:
        if not env_url or env_token is None:
            raise CapacityConnectionError("Capacity provider requires both endpoint and dedicated token")
        token = env_token.get_secret_value()
        endpoint = ExternalCapacityClient.check_configuration(env_url, token)
        return CapacityConnection(endpoint, token)
    if stored is None or not stored.metadata.get("enabled"):
        return None
    endpoint = stored.metadata.get("endpoint")
    if not isinstance(endpoint, str):
        raise CapacityConnectionError("Stored capacity provider has no active endpoint")
    endpoint = ExternalCapacityClient.check_configuration(endpoint, stored.capacity_token)
    try:
        generation = UUID(stored.metadata["active_generation"])
    except (KeyError, TypeError, ValueError):
        raise CapacityConnectionError("Stored capacity provider has no active generation") from None
    rules = CloudflareCapacityRules.model_validate(stored.metadata.get("rules", {}))
    return CapacityConnection(endpoint, stored.capacity_token, generation, rules)
