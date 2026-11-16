"""Built-in platform event emitters.

These helpers own the canonical body shape for Bifrost-emitted topics. Callers
pass domain objects or primitive identifiers; this module keeps the common
payload keys consistent across event families.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from collections.abc import Mapping
from typing import Any, Literal, Protocol
from uuid import UUID

from src.core.constants import SYSTEM_USER_ID, SYSTEM_USER_EMAIL

logger = logging.getLogger(__name__)


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.isoformat()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _organization(organization_id: str | UUID | None, name: str | None = None) -> dict[str, str | None]:
    return {
        "id": str(organization_id) if organization_id else None,
        "name": name,
    }


def _system_actor() -> dict[str, str | None]:
    return {
        "type": "system",
        "id": SYSTEM_USER_ID,
        "email": SYSTEM_USER_EMAIL,
        "name": "Bifrost",
    }


def _user_actor(
    *,
    user_id: str | UUID | None,
    email: str | None,
    name: str | None,
) -> dict[str, str | None]:
    return {
        "type": "user" if user_id or email else "system",
        "id": str(user_id) if user_id else None,
        "email": email,
        "name": name or ("Bifrost" if not user_id and not email else None),
    }


def _base_body(
    *,
    organization_id: str | UUID | None,
    organization_name: str | None = None,
    actor: dict[str, str | None] | None = None,
    occurred_at: datetime | None = None,
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "occurred_at": _iso(occurred_at) or _now(),
        "organization": _organization(organization_id, organization_name),
        "actor": actor or _system_actor(),
    }


async def _emit(
    topic: str,
    body: dict[str, Any],
    *,
    organization_id: str | UUID | None,
    triggered_by: str | None = None,
) -> None:
    try:
        from src.services.events import emit_event

        await emit_event(
            topic,
            body,
            organization_id=UUID(str(organization_id)) if organization_id else None,
            triggered_by=triggered_by,
        )
    except Exception as exc:
        logger.warning(
            "Failed to emit built-in event %s: %s",
            topic,
            exc,
            exc_info=True,
        )


class EventUser(Protocol):
    """The user attributes the ``user.*`` topics publish."""

    @property
    def id(self) -> UUID: ...

    @property
    def email(self) -> str: ...

    @property
    def name(self) -> str | None: ...

    @property
    def organization_id(self) -> UUID | None: ...


SignInMethod = Literal["password", "passkey", "sso"]
UserCreationMethod = Literal["admin", "sso_provisioning", "registration", "bootstrap"]

# Verified id_token claims that describe the token exchange rather than the
# person: replay and binding values (``nonce``, ``*_hash``), the IdP session
# identifier, and Microsoft's opaque internal claims. Every other verified
# claim reaches workflows so tenant-specific claims (roles, custom attributes)
# work without a Bifrost change. Raw tokens and unverified userinfo are never
# passed to this module.
_EXCLUDED_ID_TOKEN_CLAIMS = frozenset(
    {"nonce", "at_hash", "c_hash", "s_hash", "sid", "aio", "uti", "rh"}
)


def identity_claims_for_event(claims: Mapping[str, Any]) -> dict[str, Any]:
    """Return the ``identity`` object of a ``user.signed_in`` SSO event.

    ``claims`` must be the verified id_token claims. ``groups`` is lifted to the
    top level when the IdP sent a list. Entra replaces the list with
    ``_claim_names``/``hasgroups`` when a user is in too many groups; that is
    reported as ``groups_overage`` so a workflow knows to call Graph instead.
    """
    filtered = {
        key: value for key, value in claims.items() if key not in _EXCLUDED_ID_TOKEN_CLAIMS
    }
    groups = claims.get("groups")
    claim_names = claims.get("_claim_names")
    overage = claims.get("hasgroups") is True or (
        isinstance(claim_names, Mapping) and "groups" in claim_names
    )
    return {
        "claims": filtered,
        "groups": list(groups) if isinstance(groups, list) else None,
        "groups_overage": overage,
    }


def _event_user(user: EventUser) -> dict[str, str | None]:
    return {
        "id": str(user.id),
        "email": user.email,
        "name": user.name,
        "organization_id": str(user.organization_id) if user.organization_id else None,
    }


def _self_actor(user: EventUser) -> dict[str, str | None]:
    return _user_actor(user_id=user.id, email=user.email, name=user.name)


async def emit_user_signed_in(
    user: EventUser,
    *,
    method: SignInMethod,
    provider: str | None = None,
    id_token_claims: Mapping[str, Any] | None = None,
) -> None:
    """Emit ``user.signed_in`` after Bifrost issues a session to ``user``.

    ``id_token_claims`` are the provider's verified id_token claims for an SSO
    sign-in; they are filtered by :func:`identity_claims_for_event`.
    """
    body = {
        **_base_body(organization_id=user.organization_id, actor=_self_actor(user)),
        "user": _event_user(user),
        "sign_in": {"method": method, "provider": provider},
        "identity": identity_claims_for_event(id_token_claims)
        if id_token_claims is not None
        else None,
    }
    await _emit(
        "user.signed_in",
        body,
        organization_id=user.organization_id,
        triggered_by=str(user.id),
    )


async def emit_user_created(
    user: EventUser,
    *,
    method: UserCreationMethod,
    provider: str | None = None,
    actor_user_id: str | UUID | None = None,
    actor_email: str | None = None,
    actor_name: str | None = None,
) -> None:
    """Emit ``user.created`` once the new user row is committed.

    The actor is the administrator for ``admin``, the new user for
    self-service methods, and Bifrost for ``bootstrap``.
    """
    if method == "admin":
        actor = _user_actor(user_id=actor_user_id, email=actor_email, name=actor_name)
    elif method == "bootstrap":
        actor = _system_actor()
    else:
        actor = _self_actor(user)
    body = {
        **_base_body(organization_id=user.organization_id, actor=actor),
        "user": _event_user(user),
        "creation": {"method": method, "provider": provider},
    }
    await _emit(
        "user.created",
        body,
        organization_id=user.organization_id,
        triggered_by=actor["id"],
    )


def _trigger_from_event_context(event: dict[str, Any] | None) -> dict[str, Any]:
    if not event:
        return {"type": "manual", "event_type": None, "event_id": None}

    return {
        "type": "event",
        "event_type": event.get("type"),
        "event_id": event.get("id"),
    }


async def emit_workflow_failure_events(
    *,
    workflow_id: str | UUID | None,
    workflow_name: str | None,
    execution_id: str | UUID,
    organization_id: str | UUID | None,
    user_id: str | UUID | None,
    user_email: str | None,
    user_name: str | None,
    error_type: str,
    error_message: str,
    status: str,
    trigger_event: dict[str, Any] | None = None,
    attempt: int = 1,
    max_attempts: int = 1,
) -> None:
    body = {
        **_base_body(
            organization_id=organization_id,
            actor=_user_actor(user_id=user_id, email=user_email, name=user_name),
        ),
        "workflow": {
            "id": str(workflow_id) if workflow_id else None,
            "name": workflow_name,
        },
        "execution": {
            "id": str(execution_id),
            "attempt": attempt,
            "max_attempts": max_attempts,
            "status": status,
        },
        "trigger": _trigger_from_event_context(trigger_event),
        "error": {
            "type": error_type,
            "code": None,
            "message": error_message,
            "retryable": attempt < max_attempts,
        },
    }

    await _emit(
        "workflow.failed",
        body,
        organization_id=organization_id,
        triggered_by=str(user_id) if user_id else None,
    )
    if attempt >= max_attempts:
        body = {
            **body,
            "error": {
                **body["error"],
                "retryable": False,
            },
        }
        await _emit(
            "workflow.retry_exhausted",
            body,
            organization_id=organization_id,
            triggered_by=str(user_id) if user_id else None,
        )


async def emit_workflow_completed_event(
    *,
    workflow_id: str | UUID | None,
    workflow_name: str | None,
    execution_id: str | UUID,
    organization_id: str | UUID | None,
    user_id: str | UUID | None,
    user_email: str | None,
    user_name: str | None,
    started_at: datetime | None,
    completed_at: datetime,
    duration_ms: int,
    attempt: int = 1,
    max_attempts: int = 1,
    trigger_event: dict[str, Any] | None = None,
) -> None:
    """Emit the metadata-only terminal-success event for a workflow run."""
    execution: dict[str, Any] = {
        "id": str(execution_id),
        "status": "Success",
        "attempt": attempt,
        "max_attempts": max_attempts,
        "started_at": _iso(started_at),
        "completed_at": _iso(completed_at),
        "duration_ms": duration_ms,
    }

    body = {
        **_base_body(
            organization_id=organization_id,
            actor=_user_actor(user_id=user_id, email=user_email, name=user_name),
            occurred_at=completed_at,
        ),
        "workflow": {
            "id": str(workflow_id) if workflow_id else None,
            "name": workflow_name,
        },
        "execution": execution,
        "trigger": _trigger_from_event_context(trigger_event),
    }
    await _emit(
        "workflow.completed",
        body,
        organization_id=organization_id,
        triggered_by=str(user_id) if user_id else None,
    )


def _integration_body(
    *,
    integration_id: str | UUID | None,
    integration_name: str | None,
    organization_id: str | UUID | None,
    organization_name: str | None,
    actor: dict[str, str | None] | None = None,
    connection_id: str | UUID | None = None,
    external_account_id: str | None = None,
    external_account_name: str | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        **_base_body(
            organization_id=organization_id,
            organization_name=organization_name,
            actor=actor,
        ),
        "integration": {
            "id": str(integration_id) if integration_id else None,
            "name": integration_name,
        },
        "connection": {
            "id": str(connection_id) if connection_id else None,
            "external_account_id": external_account_id,
            "external_account_name": external_account_name,
        },
        **(extra or {}),
    }


async def emit_integration_connected(
    *,
    integration_id: str | UUID | None,
    integration_name: str | None,
    organization_id: str | UUID | None,
    organization_name: str | None = None,
    connection_id: str | UUID | None = None,
    external_account_id: str | None = None,
    external_account_name: str | None = None,
    actor_user_id: str | UUID | None = None,
    actor_email: str | None = None,
    actor_name: str | None = None,
) -> None:
    body = _integration_body(
        integration_id=integration_id,
        integration_name=integration_name,
        organization_id=organization_id,
        organization_name=organization_name,
        connection_id=connection_id,
        external_account_id=external_account_id,
        external_account_name=external_account_name,
        actor=_user_actor(user_id=actor_user_id, email=actor_email, name=actor_name),
    )
    await _emit(
        "integration.connected",
        body,
        organization_id=organization_id,
        triggered_by=str(actor_user_id) if actor_user_id else None,
    )


async def emit_integration_disconnected(
    *,
    integration_id: str | UUID | None,
    integration_name: str | None,
    organization_id: str | UUID | None,
    organization_name: str | None = None,
    connection_id: str | UUID | None = None,
    external_account_id: str | None = None,
    external_account_name: str | None = None,
    actor_user_id: str | UUID | None = None,
    actor_email: str | None = None,
    actor_name: str | None = None,
) -> None:
    body = _integration_body(
        integration_id=integration_id,
        integration_name=integration_name,
        organization_id=organization_id,
        organization_name=organization_name,
        connection_id=connection_id,
        external_account_id=external_account_id,
        external_account_name=external_account_name,
        actor=_user_actor(user_id=actor_user_id, email=actor_email, name=actor_name),
    )
    await _emit(
        "integration.disconnected",
        body,
        organization_id=organization_id,
        triggered_by=str(actor_user_id) if actor_user_id else None,
    )


async def emit_integration_refresh_failed(
    *,
    integration_id: str | UUID | None,
    integration_name: str | None,
    organization_id: str | UUID | None,
    organization_name: str | None = None,
    connection_id: str | UUID | None = None,
    external_account_id: str | None = None,
    external_account_name: str | None = None,
    attempt: int = 1,
    last_success_at: datetime | None = None,
    next_retry_at: datetime | None = None,
    error_type: str = "OAuthRefreshError",
    error_code: str | None = None,
    error_message: str = "Token refresh failed.",
    retryable: bool = False,
    reauth_required: bool = False,
) -> None:
    body = _integration_body(
        integration_id=integration_id,
        integration_name=integration_name,
        organization_id=organization_id,
        organization_name=organization_name,
        connection_id=connection_id,
        external_account_id=external_account_id,
        external_account_name=external_account_name,
        extra={
            "refresh": {
                "attempt": attempt,
                "last_success_at": _iso(last_success_at),
                "next_retry_at": _iso(next_retry_at),
            },
            "error": {
                "type": error_type,
                "code": error_code,
                "message": error_message,
                "retryable": retryable,
            },
        },
    )
    await _emit("integration.refresh_failed", body, organization_id=organization_id)
    if reauth_required:
        await _emit("integration.reauth_required", body, organization_id=organization_id)


async def emit_integration_refresh_recovered(
    *,
    integration_id: str | UUID | None,
    integration_name: str | None,
    organization_id: str | UUID | None,
    organization_name: str | None = None,
    connection_id: str | UUID | None = None,
    external_account_id: str | None = None,
    external_account_name: str | None = None,
    attempt: int = 1,
    last_success_at: datetime | None = None,
) -> None:
    body = _integration_body(
        integration_id=integration_id,
        integration_name=integration_name,
        organization_id=organization_id,
        organization_name=organization_name,
        connection_id=connection_id,
        external_account_id=external_account_id,
        external_account_name=external_account_name,
        extra={
            "refresh": {
                "attempt": attempt,
                "last_success_at": _iso(last_success_at),
                "next_retry_at": None,
            },
        },
    )
    await _emit("integration.refresh_recovered", body, organization_id=organization_id)


async def emit_solution_update_available(
    *,
    solution_id: str | UUID | None,
    slug: str,
    organization_id: str | UUID | None,
    installed_version: str | None,
    available_version: str,
) -> None:
    """Fired when a git-connected install newly detects a newer descriptor
    version at its repo HEAD (Task 11 scheduler). Subscribable so an operator
    can wire a workflow/notification to 'an update is available'."""
    body = {
        **_base_body(organization_id=organization_id),
        "solution": {
            "id": str(solution_id) if solution_id else None,
            "slug": slug,
            "installed_version": installed_version,
            "available_version": available_version,
        },
    }
    await _emit("solution.update_available", body, organization_id=organization_id)


async def emit_event_delivery_retry_exhausted(
    *,
    event_id: str | UUID,
    event_type: str | None,
    source_id: str | UUID | None,
    organization_id: str | UUID | None,
    delivery_id: str | UUID,
    target_type: str,
    target_id: str | UUID | None,
    attempt: int,
    max_attempts: int,
    error_type: str = "DeliveryError",
    error_message: str = "Delivery failed after all retry attempts.",
) -> None:
    if event_type == "event.delivery_retry_exhausted":
        return

    body = {
        **_base_body(organization_id=organization_id),
        "event": {
            "id": str(event_id),
            "type": event_type,
            "source_id": str(source_id) if source_id else None,
        },
        "delivery": {
            "id": str(delivery_id),
            "target_type": target_type,
            "target_id": str(target_id) if target_id else None,
            "attempt": attempt,
            "max_attempts": max_attempts,
        },
        "error": {
            "type": error_type,
            "code": None,
            "message": error_message,
            "retryable": False,
        },
    }
    await _emit(
        "event.delivery_retry_exhausted",
        body,
        organization_id=organization_id,
    )
