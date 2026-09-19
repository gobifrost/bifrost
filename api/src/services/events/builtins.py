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


async def emit_event_delivery_retry_exhausted(    *,
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


# ---------------------------------------------------------------------------
# AgentRun and workflow completion events (durable runtime outbox)
# ---------------------------------------------------------------------------

AGENT_TOPIC_FOR_STATUS = {
    "completed": "agent.completed",
    "failed": "agent.failed",
    "cancelled": "agent.cancelled",
    "timeout": "agent.timeout",
    "budget_exceeded": "agent.failed",
    "contract_failed": "agent.contract_failed",
}
"""Terminal AgentRun status -> built-in topic. Non-success statuses without a
dedicated topic map to ``agent.failed`` with the exact status in the payload."""

MAX_EVENT_OUTPUT_CHARS = 32_000


def _bounded_output(output: dict[str, Any] | None) -> dict[str, Any] | None:
    """Bound success payloads; consumers fetch the full run if they need it."""
    if output is None:
        return None
    import json as _json

    serialized = _json.dumps(output, default=str)
    if len(serialized) <= MAX_EVENT_OUTPUT_CHARS:
        return output
    return {
        "truncated": True,
        "head": serialized[:MAX_EVENT_OUTPUT_CHARS],
        "chars": len(serialized),
    }


def agent_completion_topic(status: str) -> str:
    """Return the built-in topic for a terminal AgentRun status."""
    return AGENT_TOPIC_FOR_STATUS.get(status, "agent.failed")


def agent_completion_payload(run) -> tuple[str, dict[str, Any]]:
    """Build the canonical (topic, body) for a terminal AgentRun.

    Bounded identifiers, status, timestamps, correlation, counters, contract
    validity — plus output for success or a structured error for failure.
    Never the full transcript: consumers fetch the authoritative run.
    """
    topic = agent_completion_topic(run.status)
    body = {
        **_base_body(
            organization_id=run.org_id,
            actor=_user_actor(
                user_id=run.caller_user_id,
                email=run.caller_email,
                name=run.caller_name,
            ),
        ),
        "run": {
            "id": str(run.id),
            "agent_id": str(run.agent_id) if run.agent_id else None,
            "root_run_id": str(run.root_run_id) if run.root_run_id else None,
            "parent_run_id": str(run.parent_run_id) if run.parent_run_id else None,
            "status": run.status,
            "attempt": run.attempt or 0,
            "trigger_type": run.trigger_type,
        },
        "correlation": dict(run.correlation or {}),
        "counters": {
            "iterations_used": run.iterations_used or 0,
            "tokens_used": run.tokens_used or 0,
            "duration_ms": run.duration_ms,
        },
        "timestamps": {
            "created_at": _iso(run.created_at),
            "started_at": _iso(run.started_at),
            "completed_at": _iso(run.completed_at),
        },
        "contract": {
            "valid": run.contract_valid,
            "errors": list(run.contract_errors or []),
        },
    }
    if run.status == "completed":
        body["output"] = _bounded_output(run.output)
    else:
        body["error"] = {
            "type": run.status,
            "code": None,
            "message": (run.error or f"Agent run {run.status}.")[:2000],
            "retryable": False,
        }
    return topic, body


async def emit_agent_run_event(*, run) -> None:
    """Emit one terminal AgentRun event through the standard pipeline."""
    topic, body = agent_completion_payload(run)
    await _emit(
        topic,
        body,
        organization_id=run.org_id,
        triggered_by=str(run.caller_user_id) if run.caller_user_id else None,
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
    status: str,
    trigger_event: dict[str, Any] | None = None,
    duration_ms: int | None = None,
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
            "status": status,
            "duration_ms": duration_ms,
        },
        "trigger": _trigger_from_event_context(trigger_event),
    }
    await _emit(
        "workflow.completed",
        body,
        organization_id=organization_id,
        triggered_by=str(user_id) if user_id else None,
    )
