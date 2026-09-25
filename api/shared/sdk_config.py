"""Shared business service for SDK ``config`` operations.

Single implementation used by both entry points:

- the HTTP handlers (``api/src/routers/cli.py::cli_*_config``) serving
  external SDK/CLI callers, and
- the engine-local dispatcher
  (``api/src/services/execution/sdk_local_dispatch.py``) serving workflow
  and ``@service`` children through the parent-side local transport.

Both paths share scope resolution input (an already-resolved ``org_id``),
the global+org cascade, external-user behavior, secret handling, type
coercion, audit attribution, and commit/cache ordering — so HTTP and
local results are identical by construction.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.log_safety import log_safe
from src.models.contracts.cli import CLIConfigValue
from shared.scope_resolver import (
    RequestedScope,
    ScopeNotAllowed,
    UNSET,
    resolve_effective_scope,
)

import src.repositories.config as config_repo_module

logger = logging.getLogger(__name__)


class ScopeResolutionError(Exception):
    """SDK scope grammar/authorization failure with an HTTP-style status.

    Raised by :func:`resolve_sdk_scope` so the HTTP handler (422/403
    ``HTTPException``) and the local dispatcher (``ok: false`` frames) can
    map the same failure to their own transport.
    """

    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


async def resolve_sdk_scope(
    scope: str | None,
    *,
    caller_org_id: UUID | None,
    is_platform_admin: bool,
    is_provider_org: bool | None = None,
    session: AsyncSession | None = None,
) -> UUID | None:
    """Shared scope grammar + authorization for SDK config reads.

    The C2 gate: platform admins AND provider-org members can bypass scope
    restrictions. The caller's "own org" comes from the auth-verified
    principal — never from a mutable per-user default and never from child
    claims. ``None``/``""`` is UNSET (caller's own org, no bypass check);
    ``"global"`` is an explicit global request (bypass required); an org
    UUID is always allowed for the caller's own org, else bypass required.

    Args:
        scope: Requested scope string from the request body / child frame.
        caller_org_id: The originating caller's organization.
        is_platform_admin: Whether the caller is a platform admin.
        is_provider_org: Known provider-org membership, when the caller
            already resolved it (local dispatch from parent context). When
            None and a session is given, membership is looked up only when
            the requested scope actually needs a bypass check.
        session: Database session for the provider-org lookup (HTTP path).

    Returns:
        Organization UUID, or None for global scope.

    Raises:
        ScopeResolutionError: 422 for a malformed scope, 403 for a scope
            the caller may not use. Details match the historical messages.
    """
    if scope is None or scope == "":
        # Empty string preserved as "unset" for backwards compat with
        # CLI clients that pass `--scope ''` to mean "use my default."
        requested: RequestedScope = UNSET
    elif scope == "global":
        requested = None
    elif isinstance(scope, str):
        try:
            requested = UUID(scope)
        except ValueError:
            raise ScopeResolutionError(
                422,
                f"scope must be 'global', a UUID, or null; got {scope!r}",
            ) from None
    else:
        # Only reachable from local child frames (HTTP pydantic coerces
        # scope to str | None first, 422ing anything else).
        raise ScopeResolutionError(
            422,
            f"scope must be 'global', a UUID, or null; got {scope!r}",
        ) from None

    provider = bool(is_provider_org)
    needs_bypass_check = requested is not UNSET and requested != caller_org_id
    if (
        needs_bypass_check
        and not is_platform_admin
        and not provider
        and session is not None
        and caller_org_id is not None
    ):
        from sqlalchemy import select

        from src.models import Organization

        org_row = await session.execute(
            select(Organization.is_provider).where(Organization.id == caller_org_id)
        )
        provider = bool(org_row.scalar_one_or_none())

    try:
        return resolve_effective_scope(
            caller_org_id=caller_org_id,
            is_platform_admin=is_platform_admin,
            is_provider_org=provider,
            requested_scope=requested,
        )
    except ScopeNotAllowed as e:
        raise ScopeResolutionError(403, str(e)) from None


async def get_sdk_config_value(
    session: AsyncSession,
    *,
    key: str,
    org_id: UUID | None,
    external: bool,
) -> CLIConfigValue | None:
    """Resolve one config value for an SDK caller.

    Args:
        session: Short-lived parent/HTTP database session.
        key: Configuration key to look up.
        org_id: Already-resolved effective scope (None for global).
        external: True for a direct EXTERNAL portal caller — drops the
            global tier so a global secret is never returned nor decrypted.
            Engine executions always pass False (sentinel path keeps the
            full org+global merge).

    Returns:
        The config value with its type, or None when the key is not set
        (the caller maps None to its default).
    """
    # Canonical SDK config load: cascade (global + org-specific) merged.
    # An EXTERNAL portal caller gets org-only — no global tier — so a global
    # secret value is never returned (and never decrypted below). EXT-1 NEW-1.
    # NOTE: attribute access at call time (not a top-level from-import) keeps
    # the `src.repositories.config.ConfigRepository` seam patchable for both
    # entry points.
    repo = config_repo_module.ConfigRepository(
        session, org_id=org_id, is_superuser=True
    )
    all_config = await repo.merged_for_sdk(external=external)

    if key not in all_config:
        return None

    entry = all_config[key]
    raw_value = entry.get("value")
    config_type = entry.get("type", "string")

    if config_type == "secret" and raw_value:
        from src.core.security import decrypt_secret

        try:
            raw_value = decrypt_secret(raw_value)
        except Exception:
            raw_value = None
    elif config_type == "json" and isinstance(raw_value, str):
        try:
            raw_value = json.loads(raw_value)
        except json.JSONDecodeError as e:
            # Stored value is not valid JSON — return raw string as fallback
            logger.debug(
                f"config {log_safe(key)} stored as json but failed to parse, returning raw: {log_safe(e)}"
            )
    elif config_type == "bool":
        raw_value = (
            str(raw_value).lower() == "true"
            if isinstance(raw_value, str)
            else bool(raw_value)
        )
    elif config_type == "int":
        try:
            raw_value = int(raw_value)
        except (ValueError, TypeError) as e:
            # Stored value isn't coercible to int — return raw value
            logger.debug(
                f"config {log_safe(key)} stored as int but failed to coerce, returning raw: {log_safe(e)}"
            )

    return CLIConfigValue(
        key=key,
        value=raw_value,
        config_type=config_type,
    )


async def get_sdk_config_dict(
    session: AsyncSession,
    *,
    key: str,
    org_id: UUID | None,
    external: bool,
) -> dict[str, Any] | None:
    """JSON-serializable form of :func:`get_sdk_config_value` for transports."""
    value = await get_sdk_config_value(
        session, key=key, org_id=org_id, external=external
    )
    if value is None:
        return None
    return value.model_dump()


def _classify_set_value(value: Any):
    """Split a set payload into (config_type, stored_value).

    Mirrors the historical HTTP handler exactly: dicts/lists become JSON;
    bools precede ints because ``bool`` subclasses ``int``. Secret values
    are handled before this helper and encrypted in the async service.
    """
    from src.models.enums import ConfigType as ConfigTypeEnum

    if isinstance(value, dict) or isinstance(value, list):
        return ConfigTypeEnum.JSON, value
    if isinstance(value, bool):
        return ConfigTypeEnum.BOOL, value
    if isinstance(value, int):
        return ConfigTypeEnum.INT, value
    return ConfigTypeEnum.STRING, value


async def set_sdk_config_value(
    session: AsyncSession,
    *,
    key: str,
    value: Any,
    is_secret: bool,
    org_id: UUID | None,
    actor_email: str,
) -> None:
    """Upsert one config value (HTTP handler and local dispatcher share this).

    Args:
        session: Short-lived parent/HTTP database session.
        key: Configuration key.
        value: JSON-serializable value (already JSON-decoded on the local
            path; validated by pydantic on the HTTP path).
        is_secret: Encrypt the stringified value before storage.
        org_id: Already-resolved effective scope (None for global).
        actor_email: Parent-derived actor email for the ``updated_by`` audit
            column (``Config.updated_by`` is non-nullable).

    Ordering matches the historical handler: DB commit first, then the
    best-effort cache upsert (org writes drop the merged hash; global
    writes HSET plus bump the global version).
    """
    from src.models import Config as ConfigModel

    now = datetime.now(timezone.utc)

    if is_secret:
        from src.core.security import encrypt_secret
        from src.models.enums import ConfigType as ConfigTypeEnum

        config_type = ConfigTypeEnum.SECRET
        stored_value = await asyncio.to_thread(encrypt_secret, str(value))
    else:
        config_type, stored_value = _classify_set_value(value)

    config_value = {"value": stored_value}

    stmt = select(ConfigModel).where(
        ConfigModel.key == key,
        ConfigModel.organization_id == org_id,
    )
    result = await session.execute(stmt)
    existing = result.scalar_one_or_none()

    if existing:
        existing.value = config_value
        existing.config_type = config_type
        existing.updated_at = now
        existing.updated_by = actor_email
    else:
        config = ConfigModel(
            key=key,
            value=config_value,
            config_type=config_type,
            organization_id=org_id,
            created_at=now,
            updated_at=now,
            updated_by=actor_email,
        )
        session.add(config)

    await session.commit()

    try:
        from src.core.cache import upsert_config

        config_type_str = config_type.value
        await upsert_config(str(org_id) if org_id else None, key, stored_value, config_type_str)
    except ImportError as e:
        # cache module is optional in some deploys; DB write already committed
        logger.debug(f"cache module unavailable, skipping config cache upsert: {e}")

    logger.info(f"CLI set config {log_safe(key)} for user {actor_email}")


async def list_sdk_config_values(
    session: AsyncSession,
    *,
    org_id: UUID | None,
    external: bool,
) -> dict[str, Any]:
    """List merged config values with secrets redacted (shared by both paths).

    An EXTERNAL portal caller gets org-only (no global tier). Secret values
    are never decrypted here — they surface as ``"[SECRET]"`` exactly as
    the historical handler returned. JSON/bool/int coercion matches
    ``get_sdk_config_value`` (raw fallback when the stored value does not
    parse).
    """
    repo = config_repo_module.ConfigRepository(
        session, org_id=org_id, is_superuser=True
    )
    all_config = await repo.merged_for_sdk(external=external)

    if not all_config:
        return {}

    config_dict: dict[str, Any] = {}
    for config_key, entry in all_config.items():
        raw_value = entry.get("value")
        config_type = entry.get("type", "string")

        if config_type == "secret":
            config_dict[config_key] = "[SECRET]"
        elif config_type == "json" and isinstance(raw_value, str):
            try:
                config_dict[config_key] = json.loads(raw_value)
            except json.JSONDecodeError:
                config_dict[config_key] = raw_value
        elif config_type == "bool":
            config_dict[config_key] = (
                str(raw_value).lower() == "true"
                if isinstance(raw_value, str)
                else bool(raw_value)
            )
        elif config_type == "int":
            try:
                config_dict[config_key] = int(raw_value)
            except (ValueError, TypeError):
                config_dict[config_key] = raw_value
        else:
            config_dict[config_key] = raw_value

    return config_dict


async def delete_sdk_config_value(
    session: AsyncSession,
    *,
    key: str,
    org_id: UUID | None,
    actor_email: str,
) -> bool:
    """Delete one config value (shared by both paths).

    Returns False when the key is not set (both transports surface that as
    data, not an error). DB commit precedes the best-effort cache
    invalidation, matching the historical handler.
    """
    from src.models import Config as ConfigModel

    stmt = select(ConfigModel).where(
        ConfigModel.key == key,
        ConfigModel.organization_id == org_id,
    )
    result = await session.execute(stmt)
    config = result.scalar_one_or_none()

    if not config:
        return False

    await session.delete(config)
    await session.commit()

    try:
        from src.core.cache import invalidate_config

        await invalidate_config(str(org_id) if org_id else None, key)
    except ImportError as e:
        # cache module is optional; DB delete already committed
        logger.debug(f"cache module unavailable, skipping config cache invalidate: {e}")

    logger.info(f"CLI deleted config {log_safe(key)} for user {actor_email}")
    return True
