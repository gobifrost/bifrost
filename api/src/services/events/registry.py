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

_IDENTITY_PROVIDER = {
    "id": "550e8400-e29b-41d4-a716-446655440050",
    "name": "Contoso Entra ID",
    "vendor": "entra",
}

_IDENTITY_PROVIDER_ACTOR = {
    "type": "identity_provider",
    "id": _IDENTITY_PROVIDER["id"],
    "email": None,
    "name": _IDENTITY_PROVIDER["name"],
}

_PROVISIONED_USER = {
    "id": "550e8400-e29b-41d4-a716-446655440051",
    "email": "alice@contoso.com",
    "name": "Alice Example",
    "is_active": True,
    "external_id": "6f1c2d3e-0000-4000-8000-000000000001",
    "attributes": {
        "userName": "alice@contoso.com",
        "urn:ietf:params:scim:schemas:extension:enterprise:2.0:User": {"department": "Support"},
    },
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
        "topic": "user.provisioned",
        "description": "Fired when an identity provider creates a user or adopts an existing one.",
        "category": "Users",
        "emitted_by": "Identity provisioning",
        "example_body": _body(
            actor=_IDENTITY_PROVIDER_ACTOR,
            identity_provider=_IDENTITY_PROVIDER,
            user=_PROVISIONED_USER,
            adopted=False,
        ),
    },
    {
        "topic": "user.updated",
        "description": "Fired when an identity provider changes a user it manages.",
        "category": "Users",
        "emitted_by": "Identity provisioning",
        "example_body": _body(
            actor=_IDENTITY_PROVIDER_ACTOR,
            identity_provider=_IDENTITY_PROVIDER,
            user=_PROVISIONED_USER,
        ),
    },
    {
        "topic": "user.deprovisioned",
        "description": (
            "Fired when an identity provider deactivates or deletes a user, or the "
            "user loses their last mapped group."
        ),
        "category": "Users",
        "emitted_by": "Identity provisioning",
        "example_body": _body(
            actor=_IDENTITY_PROVIDER_ACTOR,
            identity_provider=_IDENTITY_PROVIDER,
            user={**_PROVISIONED_USER, "is_active": False},
            deprovision={"reason": "inactive", "account_removed": False},
        ),
    },
    {
        "topic": "user.groups_changed",
        "description": "Fired when an identity provider adds a user to a group or removes them.",
        "category": "Users",
        "emitted_by": "Identity provisioning",
        "example_body": _body(
            actor=_IDENTITY_PROVIDER_ACTOR,
            identity_provider=_IDENTITY_PROVIDER,
            user=_PROVISIONED_USER,
            group={
                "id": "550e8400-e29b-41d4-a716-446655440052",
                "display_name": "Support Techs",
                "change": "added",
            },
            roles_added=["550e8400-e29b-41d4-a716-446655440053"],
            roles_removed=[],
        ),
    },
    {
        "topic": "identity_provider.token_expiry",
        "description": (
            "Fired once when an identity provider connection's token comes within "
            "14 days of expiry, and once more when it expires. Provisioning stops "
            "until the token is regenerated and updated in the identity provider."
        ),
        "category": "Users",
        "emitted_by": "Identity provisioning",
        "example_body": _body(
            actor=_SYSTEM_ACTOR,
            identity_provider=_IDENTITY_PROVIDER,
            token={
                "hint": "x7Qa",
                "expires_at": "2026-06-11T12:34:56Z",
                "stage": "expiring",
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
        "topic": "workflow.completed",
        "description": "Fired after a workflow execution is durably recorded as successful.",
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
                "status": "Success",
                "attempt": 1,
                "max_attempts": 1,
                "started_at": "2026-05-28T12:34:54Z",
                "completed_at": "2026-05-28T12:34:56Z",
                "duration_ms": 1234,
            },
            trigger={
                "type": "event",
                "event_type": "ticket.created",
                "event_id": "550e8400-e29b-41d4-a716-446655440060",
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
        "topic": "solution.update_available",
        "description": "Fired when a git-connected Solution install detects a newer version at its repo HEAD.",
        "category": "Solutions",
        "emitted_by": "Solution update-check scheduler",
        "example_body": _body(
            actor=_SYSTEM_ACTOR,
            solution={
                "id": "550e8400-e29b-41d4-a716-446655440090",
                "slug": "acme-tenant-manager",
                "installed_version": "1.0.0",
                "available_version": "1.1.0",
            },
        ),
    },
    {
        "topic": "solution.permission_request",
        "description": "Fired when a Solution install has a new or changed workflow permission request awaiting admin approval.",
        "category": "Solutions",
        "emitted_by": "Solution deploy",
        "example_body": _body(
            actor=_SYSTEM_ACTOR,
            solution={
                "id": "550e8400-e29b-41d4-a716-446655440090",
                "slug": "acme-tenant-manager",
            },
            workflow={
                "id": "550e8400-e29b-41d4-a716-446655440040",
                "name": "sync_tenant_users",
            },
            request={
                "mode": "restricted",
                "grants": [{"permission": "organizations.read", "boundary": "managed_organizations"}],
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
