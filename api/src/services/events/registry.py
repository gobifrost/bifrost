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

_USER_ACTOR = {
    "type": "user",
    "id": "550e8400-e29b-41d4-a716-446655440030",
    "email": "alice@example.com",
    "name": "Alice",
}

_EVENT_USER = {
    "id": "550e8400-e29b-41d4-a716-446655440030",
    "email": "alice@example.com",
    "name": "Alice",
    "organization_id": "550e8400-e29b-41d4-a716-446655440010",
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
        "topic": "user.created",
        "description": (
            "Fired after a user account is created: by an administrator (admin), on a first SSO "
            "sign-in that provisions the account (sso_provisioning, with the provider), by "
            "self-registration (registration), or by the configured default-user bootstrap "
            "(bootstrap). Accepting an invite completes an existing account and does not fire it. "
            "Delivered asynchronously; it never blocks account creation."
        ),
        "category": "Users",
        "emitted_by": "Bifrost platform",
        "example_body": _body(
            actor=_USER_ACTOR,
            user=_EVENT_USER,
            creation={"method": "sso_provisioning", "provider": "microsoft"},
        ),
    },
    {
        "topic": "user.signed_in",
        "description": (
            "Fired after Bifrost signs a user in with a password (including MFA), a passkey, or "
            "SSO (microsoft, google, oidc). SSO events include the identity provider's verified "
            "id_token claims, minus token-binding values (nonce, at_hash, c_hash, s_hash), token and "
            "IdP session ids (jti, origin_jti, sid, session_state) and Microsoft's opaque aio/uti/rh; access, refresh and id tokens and "
            "unverified userinfo are never included. identity.groups carries the groups claim when "
            "it is a list. When Entra sends _claim_names/hasgroups instead (groups overage), "
            "identity.groups_overage is true and the workflow must read the user's groups from "
            "Microsoft Graph. Map roles on the tenant (tid or iss) plus group object ID, never a group "
            "name alone. Delivered asynchronously: a workflow can update roles or deactivate the "
            "user, but cannot block the sign-in that fired it. CLI device and MCP client "
            "authorizations by an already signed-in user do not fire it."
        ),
        "category": "Users",
        "emitted_by": "Bifrost platform",
        "example_body": _body(
            actor=_USER_ACTOR,
            user=_EVENT_USER,
            sign_in={"method": "sso", "provider": "microsoft"},
            identity={
                "claims": {
                    "iss": "https://login.microsoftonline.com/7f3c0d5e-1b2a-4c3d-9e8f-0a1b2c3d4e5f/v2.0",
                    "sub": "AAAAAAAAAAAAAAAAAAAAAIkzqFVrSaSaFHy782bbtaQ",
                    "aud": "6731de76-14a6-49ae-97bc-6eba6914391e",
                    "tid": "7f3c0d5e-1b2a-4c3d-9e8f-0a1b2c3d4e5f",
                    "oid": "00000000-0000-0000-66f3-3332eca7ea81",
                    "preferred_username": "alice@example.com",
                    "name": "Alice",
                    "groups": ["5d8f3c1e-2b4a-4c6d-8e9f-1a2b3c4d5e6f"],
                },
                "groups": ["5d8f3c1e-2b4a-4c6d-8e9f-1a2b3c4d5e6f"],
                "groups_overage": False,
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

# Topics only Bifrost emits. Custom emission (REST, SDK, workflows) refuses
# them so a subscriber can trust that one of these events came from Bifrost.
RESERVED_TOPICS = frozenset(entry["topic"] for entry in CURATED_TOPICS)
