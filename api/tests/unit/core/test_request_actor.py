"""Unit tests for the shared audit-actor builder.

Human and embed claims provide attribution; runtime attribution requires
a verified durable principal, never legacy or forged token fields.
"""

from uuid import uuid4

import pytest

from src.core.constants import SYSTEM_USER_ID
from src.core.request_actor import actor_from_runtime_principal, actor_from_token_payload, resolve_self_reported_surface


def _actor(payload, surface_header=None):
    return actor_from_token_payload(
        payload,
        ip_address="10.0.0.1",
        user_agent="pytest",
        surface_header=surface_header,
    )


class TestPayloadAbsent:
    def test_none_payload_returns_none(self):
        assert _actor(None) is None


class TestHumanToken:
    def test_sub_org_and_identity_map_to_http_actor(self):
        user_id = uuid4()
        org_id = uuid4()
        actor = _actor(
            {
                "sub": str(user_id),
                "org_id": str(org_id),
                "email": "user@example.com",
                "name": "User Example",
            }
        )
        assert actor is not None
        assert actor.user_id == user_id
        assert actor.organization_id == org_id
        assert actor.email == "user@example.com"
        assert actor.name == "User Example"
        assert actor.source == "http"
        assert actor.execution_id is None
        assert actor.ip_address == "10.0.0.1"
        assert actor.user_agent == "pytest"

    def test_invalid_uuid_claims_become_none(self):
        actor = _actor(
            {
                "sub": "not-a-uuid",
                "org_id": "also-not-a-uuid",
                "email": "user@example.com",
            }
        )
        assert actor is not None
        assert actor.user_id is None
        assert actor.organization_id is None
        assert actor.source == "http"
        assert actor.execution_id is None

    def test_impersonation_claims_map_to_audit_provenance(self):
        target_id, operator_id, session_id = (uuid4() for _ in range(3))

        actor = _actor(
            {
                "sub": str(target_id),
                "org_id": str(uuid4()),
                "impersonated_by_user_id": str(operator_id),
                "impersonation_session_id": str(session_id),
            }
        )

        assert actor is not None
        assert actor.user_id == target_id
        assert actor.impersonated_by_user_id == operator_id
        assert actor.impersonation_session_id == session_id


@pytest.mark.parametrize("claims", [
    {"credential_kind": "workflow_execution", "sub": str(uuid4())},
    {"service_id": str(uuid4()), "engine_execution_id": str(uuid4())},
    {"engine_execution_id": str(uuid4()), "engine_caller_user_id": str(uuid4())},
    {"engine_execution_id": "not-a-uuid", "engine_caller_user_id": SYSTEM_USER_ID},
    {"engine_agent_run_id": str(uuid4())},
])
def test_runtime_claims_cannot_supply_an_actor(claims):
    assert _actor(claims, surface_header="mcp") is None


@pytest.mark.parametrize("kind,surface", [
    ("workflow_execution", "workflow"),
    ("service_attempt", "service"),
    ("agent_run", "agent"),
])
def test_runtime_actor_uses_verified_identity_and_scope(kind, surface):
    from src.core.principal import UserPrincipal
    from src.services.authorization.context import AuthorizationContext
    from src.services.authorization.explain import RunUser
    from src.services.authorization.runtime import RuntimeAuthority

    actor_id, run_user_id, credential_id, scope = (uuid4() for _ in range(4))
    context = AuthorizationContext(user_id=run_user_id, home_organization_id=None,
        base_role_id=uuid4(), base_permissions=frozenset(), is_external=False)
    operator_id, session_id = uuid4(), uuid4()
    authority = RuntimeAuthority(kind=kind, credential_id=credential_id,
        run_user=RunUser(run_user_id, context, None, is_active=True), powers=None,
        acting_user_id=actor_id, organization_id=scope, workflow_id=None,
        started_by_user_id=None, root_execution_id=None,
        impersonated_by_user_id=operator_id, impersonation_session_id=session_id)
    principal = UserPrincipal(user_id=actor_id, email="actor@example.test",
        organization_id=uuid4(), name="Contoso Actor", runtime_authority=authority)
    actor = actor_from_runtime_principal(principal, ip_address=None, user_agent="pytest")
    assert (actor.user_id, actor.organization_id) == (actor_id, scope)
    assert (actor.name, actor.email) == ("Contoso Actor", "actor@example.test")
    assert (actor.source, actor.surface) == (surface, surface)
    assert actor.execution_id == (None if kind == "agent_run" else credential_id)
    assert actor.ip_address is None
    assert actor.user_agent == "pytest"
    assert actor.impersonated_by_user_id == operator_id
    assert actor.impersonation_session_id == session_id


def test_runtime_actor_requires_verified_authority():
    from src.core.principal import UserPrincipal
    principal = UserPrincipal(user_id=uuid4(), email="actor@example.test", organization_id=None)
    with pytest.raises(ValueError, match="Runtime authority required"):
        actor_from_runtime_principal(principal, ip_address=None, user_agent=None)


class TestSurfaceResolution:
    """R2a-2: surface is resolved per token type, independent of `source`."""

    def test_human_token_defaults_to_web(self):
        actor = _actor({"sub": str(uuid4()), "org_id": str(uuid4())})
        assert actor is not None
        assert actor.surface == "web"

    def test_human_token_self_reports_cli(self):
        actor = _actor(
            {"sub": str(uuid4()), "org_id": str(uuid4())}, surface_header="cli"
        )
        assert actor is not None
        assert actor.surface == "cli"

    def test_human_token_self_reports_mcp(self):
        actor = _actor(
            {"sub": str(uuid4()), "org_id": str(uuid4())}, surface_header="mcp"
        )
        assert actor is not None
        assert actor.surface == "mcp"

    def test_unrecognized_header_value_falls_back_to_web(self):
        actor = _actor(
            {"sub": str(uuid4()), "org_id": str(uuid4())}, surface_header="bogus"
        )
        assert actor is not None
        assert actor.surface == "web"

    def test_embed_token_surface_is_embed(self):
        actor = _actor(
            {
                "sub": SYSTEM_USER_ID,
                "org_id": str(uuid4()),
                "embed": True,
                "embed_kind": "app",
            },
            surface_header="cli",
        )
        assert actor is not None
        assert actor.source == "http"
        assert actor.surface == "embed"


class TestResolveSelfReportedSurface:
    def test_known_values_pass_through(self):
        assert resolve_self_reported_surface("cli") == "cli"
        assert resolve_self_reported_surface("mcp") == "mcp"

    def test_unknown_or_missing_defaults_to_web(self):
        assert resolve_self_reported_surface(None) == "web"
        assert resolve_self_reported_surface("web") == "web"
        assert resolve_self_reported_surface("anything-else") == "web"
