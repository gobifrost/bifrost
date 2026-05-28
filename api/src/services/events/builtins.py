"""Built-in platform event emitters.

These helpers own the canonical body shape for Bifrost-emitted topics. Callers
pass domain objects or primitive identifiers; this module keeps the common
payload keys consistent across event families.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any
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
