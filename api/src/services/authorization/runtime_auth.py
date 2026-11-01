"""Turn a typed runtime credential into a database-backed principal.

``load_runtime_principal`` reads every authority fact for a runtime
credential from the database. In-process callers (agent runs, MCP contexts,
caller refresh) use it with the credential identity they already hold; it is
also the parity oracle for claims minted by ``tokens.issue_access_token``.

Signed runtime tokens minted by ``issue_access_token`` (``ver: 1``) are
validated by ``tokens.authenticate_runtime_token`` instead. Older tokens,
which carry no ``ver``, are bridged: they are validated exactly as before,
through ``load_runtime_principal``, so they work only while their run is
live. Token PR1c removes the bridge.
"""

from __future__ import annotations

import logging
from typing import Any, cast
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload, load_only

from shared.builtin_roles import PLATFORM_ADMIN_ROLE_ID
from src.core.principal import UserPrincipal
from src.models.orm.organizations import Organization
from src.models.orm.users import User
from shared.role_cache import get_user_roles
from src.services.authorization.runtime import (
    RuntimeAuthority,
    RuntimeIdentityError,
    RuntimeKind,
    load_runtime_authority,
)

_RUNTIME_KINDS: frozenset[str] = frozenset({"workflow_execution", "service_attempt", "agent_run"})
_LEGACY_RUNTIME_CLAIMS: frozenset[str] = frozenset(
    {"engine_execution_id", "service_id", "service_attempt_id", "engine_agent_run_id"}
)


logger = logging.getLogger(__name__)


def is_runtime_credential_payload(payload: dict[str, Any]) -> bool:
    """Whether a payload claims, or resembles, a runtime credential."""
    return "credential_kind" in payload or any(key in payload for key in _LEGACY_RUNTIME_CLAIMS)


def is_legacy_runtime_payload(payload: dict[str, Any]) -> bool:
    """A runtime-shaped payload from before claims: it has no ``ver``.

    Any payload with ``ver`` is validated only as claims, never through the
    bridge, whatever its shape.
    """
    return is_runtime_credential_payload(payload) and "ver" not in payload


def log_legacy_runtime_token_accepted(payload: dict[str, Any]) -> None:
    """Record one acceptance of a bridged legacy runtime token (counted from
    the log until token PR1c removes the bridge)."""
    logger.info(
        "authz.legacy_runtime_token_accepted kind=%s exp=%s",
        payload.get("credential_kind"),
        payload.get("exp"),
    )


async def load_runtime_principal(
    db: AsyncSession, payload: dict[str, Any]
) -> UserPrincipal | None:
    """Resolve a typed runtime credential from the database, trusting none of
    its authority claims.

    ``None`` means the payload is not a valid typed runtime credential. The
    caller must reject it when :func:`is_runtime_credential_payload` is true;
    ordinary human and embed tokens simply continue through their own path.
    """
    kind = payload.get("credential_kind")
    if kind not in _RUNTIME_KINDS:
        return None
    try:
        credential_id = UUID(str(payload.get("sub")))
    except (TypeError, ValueError, AttributeError):
        return None
    service_id: UUID | None = None
    if kind == "service_attempt":
        try:
            service_id = UUID(str(payload.get("service_id")))
        except (TypeError, ValueError, AttributeError):
            return None

    try:
        authority = await load_runtime_authority(
            db, cast(RuntimeKind, kind), credential_id, service_id=service_id
        )
    except RuntimeIdentityError:
        return None
    return await _principal_from_authority(db, authority)


async def _principal_from_authority(
    db: AsyncSession, authority: RuntimeAuthority
) -> UserPrincipal | None:
    actor = await db.get(
        User,
        authority.acting_user_id,
        options=[
            load_only(
                User.id,
                User.email,
                User.name,
                User.organization_id,
                User.is_external,
                raiseload=True,
            ),
            joinedload(User.organization).load_only(Organization.is_provider, raiseload=True),
        ],
        populate_existing=True,
    )
    run_user = (
        actor
        if authority.acting_user_id == authority.run_user.user_id
        else await db.get(
            User,
            authority.run_user.user_id,
            options=[load_only(User.id, raiseload=True)],
            populate_existing=True,
        )
    )
    if actor is None or run_user is None:
        return None

    role_ids, role_names = await get_user_roles(actor.id, db)
    return UserPrincipal(
        user_id=actor.id,
        email=actor.email,
        name=actor.name or "",
        # Runtime authority already established the current durable run. An
        # account deactivation does not retroactively cancel it.
        is_active=True,
        # SDK-visible actor facts stay separate from original-user authority.
        # Every authorization decision uses runtime_authority.run_user instead.
        is_superuser=PLATFORM_ADMIN_ROLE_ID in role_ids,
        is_verified=True,
        is_external=actor.is_external,
        is_provider_org=bool(actor.organization and actor.organization.is_provider),
        organization_id=authority.organization_id,
        role_ids=role_ids,
        role_names=role_names,
        roles=role_names,
        engine_execution_id=(
            str(authority.credential_id)
            if authority.kind == "workflow_execution"
            else str(authority.credential_id) if authority.kind == "service_attempt" else None
        ),
        engine_solution_id=None if authority.solution_id is None else str(authority.solution_id),
        service_id=None if authority.service_id is None else str(authority.service_id),
        service_attempt_id=(
            str(authority.credential_id) if authority.kind == "service_attempt" else None
        ),
        run_user_id=authority.run_user.user_id,
        started_by_user_id=authority.started_by_user_id,
        root_execution_id=authority.root_execution_id,
        workflow_id=authority.workflow_id,
        impersonated_by_user_id=authority.impersonated_by_user_id,
        impersonation_session_id=authority.impersonation_session_id,
        runtime_authority=authority,
    )
