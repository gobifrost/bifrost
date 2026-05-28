"""Curated topic registry for the events system."""

COMMON_EXAMPLE_FIELDS = ("schema_version", "occurred_at", "organization", "actor")

_ORGANIZATION = {
    "id": "550e8400-e29b-41d4-a716-446655440010",
    "name": "Acme MSP",
}

_SYSTEM_ACTOR = {
    "type": "system",
    "id": None,
    "email": None,
    "name": "Bifrost",
}

_ADMIN_ACTOR = {
    "type": "user",
    "id": "550e8400-e29b-41d4-a716-446655440020",
    "email": "admin@example.com",
    "name": "Admin User",
}

_BASE_BODY = {
    "schema_version": 1,
    "occurred_at": "2026-05-28T12:34:56Z",
    "organization": _ORGANIZATION,
}


def _body(*, actor: dict, **fields: object) -> dict:
    return {
        **_BASE_BODY,
        "actor": actor,
        **fields,
    }


CURATED_TOPICS = [
    {
        "topic": "user.invited",
        "description": "Fired when a user is invited or an invite is resent.",
        "category": "Users",
        "emitted_by": "Bifrost platform",
        "example_body": _body(
            actor=_ADMIN_ACTOR,
            user={
                "id": "550e8400-e29b-41d4-a716-446655440030",
                "email": "alice@example.com",
                "name": "Alice",
            },
            invite={
                "registration_url": "https://app.example.com/accept-invite?token=...",
                "expires_at": "2026-05-29T12:34:56Z",
                "reason": "created",
            },
        ),
    },
    {
        "topic": "workflow.failed",
        "description": "Fired when a workflow execution attempt fails.",
        "category": "Workflows",
        "emitted_by": "Workflow executor",
        "example_body": _body(
            actor=_SYSTEM_ACTOR,
            workflow={
                "id": "550e8400-e29b-41d4-a716-446655440040",
                "name": "sync_tickets",
            },
            execution={
                "id": "550e8400-e29b-41d4-a716-446655440050",
                "attempt": 1,
                "max_attempts": 3,
            },
            trigger={
                "type": "event",
                "event_type": "ticket.created",
                "event_id": "550e8400-e29b-41d4-a716-446655440060",
            },
            error={
                "type": "ValueError",
                "code": None,
                "message": "Workflow raised an exception.",
                "retryable": True,
            },
        ),
    },
    {
        "topic": "workflow.retry_exhausted",
        "description": "Fired when a workflow has no retry attempts remaining.",
        "category": "Workflows",
        "emitted_by": "Workflow executor",
        "example_body": _body(
            actor=_SYSTEM_ACTOR,
            workflow={
                "id": "550e8400-e29b-41d4-a716-446655440040",
                "name": "sync_tickets",
            },
            execution={
                "id": "550e8400-e29b-41d4-a716-446655440050",
                "attempt": 3,
                "max_attempts": 3,
            },
            trigger={
                "type": "event",
                "event_type": "ticket.created",
                "event_id": "550e8400-e29b-41d4-a716-446655440060",
            },
            error={
                "type": "ValueError",
                "code": None,
                "message": "Workflow failed after all retry attempts.",
                "retryable": False,
            },
        ),
    },
    {
        "topic": "integration.connected",
        "description": "Fired when an integration connection is established.",
        "category": "Integrations",
        "emitted_by": "Integration service",
        "example_body": _body(
            actor=_ADMIN_ACTOR,
            integration={
                "id": "550e8400-e29b-41d4-a716-446655440070",
                "name": "Microsoft Graph",
            },
            connection={
                "id": "550e8400-e29b-41d4-a716-446655440080",
                "external_account_id": "tenant-123",
                "external_account_name": "acme.onmicrosoft.com",
            },
        ),
    },
    {
        "topic": "integration.disconnected",
        "description": "Fired when an integration connection is removed.",
        "category": "Integrations",
        "emitted_by": "Integration service",
        "example_body": _body(
            actor=_ADMIN_ACTOR,
            integration={
                "id": "550e8400-e29b-41d4-a716-446655440070",
                "name": "Microsoft Graph",
            },
            connection={
                "id": "550e8400-e29b-41d4-a716-446655440080",
                "external_account_id": "tenant-123",
                "external_account_name": "acme.onmicrosoft.com",
            },
        ),
    },
    {
        "topic": "integration.refresh_failed",
        "description": "Fired when Bifrost cannot refresh integration credentials.",
        "category": "Integrations",
        "emitted_by": "OAuth refresh service",
        "example_body": _body(
            actor=_SYSTEM_ACTOR,
            integration={
                "id": "550e8400-e29b-41d4-a716-446655440070",
                "name": "Microsoft Graph",
            },
            connection={
                "id": "550e8400-e29b-41d4-a716-446655440080",
                "external_account_id": "tenant-123",
                "external_account_name": "acme.onmicrosoft.com",
            },
            refresh={
                "attempt": 3,
                "last_success_at": "2026-05-28T11:34:56Z",
                "next_retry_at": "2026-05-28T12:49:56Z",
            },
            error={
                "type": "OAuthRefreshError",
                "code": "invalid_grant",
                "message": "Token refresh failed.",
                "retryable": False,
            },
        ),
    },
    {
        "topic": "integration.reauth_required",
        "description": "Fired when an integration needs a human to reconnect it.",
        "category": "Integrations",
        "emitted_by": "OAuth refresh service",
        "example_body": _body(
            actor=_SYSTEM_ACTOR,
            integration={
                "id": "550e8400-e29b-41d4-a716-446655440070",
                "name": "Microsoft Graph",
            },
            connection={
                "id": "550e8400-e29b-41d4-a716-446655440080",
                "external_account_id": "tenant-123",
                "external_account_name": "acme.onmicrosoft.com",
            },
            refresh={
                "attempt": 3,
                "last_success_at": "2026-05-28T11:34:56Z",
                "next_retry_at": None,
            },
            error={
                "type": "OAuthRefreshError",
                "code": "invalid_grant",
                "message": "Reconnect the integration to continue.",
                "retryable": False,
            },
        ),
    },
    {
        "topic": "integration.refresh_recovered",
        "description": "Fired when credential refresh succeeds after a prior failure.",
        "category": "Integrations",
        "emitted_by": "OAuth refresh service",
        "example_body": _body(
            actor=_SYSTEM_ACTOR,
            integration={
                "id": "550e8400-e29b-41d4-a716-446655440070",
                "name": "Microsoft Graph",
            },
            connection={
                "id": "550e8400-e29b-41d4-a716-446655440080",
                "external_account_id": "tenant-123",
                "external_account_name": "acme.onmicrosoft.com",
            },
            refresh={
                "attempt": 1,
                "last_success_at": "2026-05-28T12:34:56Z",
                "next_retry_at": None,
            },
        ),
    },
    {
        "topic": "event.delivery_retry_exhausted",
        "description": "Fired when an event delivery cannot be completed after retries.",
        "category": "Events",
        "emitted_by": "Event processor",
        "example_body": _body(
            actor=_SYSTEM_ACTOR,
            event={
                "id": "550e8400-e29b-41d4-a716-446655440090",
                "type": "ticket.created",
                "source_id": "550e8400-e29b-41d4-a716-4466554400a0",
            },
            delivery={
                "id": "550e8400-e29b-41d4-a716-4466554400b0",
                "target_type": "workflow",
                "target_id": "550e8400-e29b-41d4-a716-446655440040",
                "attempt": 3,
                "max_attempts": 3,
            },
            error={
                "type": "DeliveryError",
                "code": None,
                "message": "Delivery failed after all retry attempts.",
                "retryable": False,
            },
        ),
    },
]
