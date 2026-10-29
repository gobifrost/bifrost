"""Service principal detection (mint_service_token consumers)."""

from uuid import UUID

from src.core.principal import UserPrincipal
from src.core.security import mint_service_token
from src.services.authorization.context import AuthorizationContext
from src.services.authorization.explain import RunUser
from src.services.authorization.runtime import RuntimeAuthority, RuntimeKind
from src.services.solution_scope import is_engine_user, is_service_principal


def _principal_for(token: str) -> UserPrincipal:
    from src.core.security import decode_token

    payload = decode_token(token, expected_type="access")
    assert payload is not None
    return UserPrincipal(
        user_id=UUID(payload["sub"]),
        email="unresolved@example.test",
        organization_id=UUID(payload["org_id"]) if payload.get("org_id") else None,
        name=payload.get("name", ""),
        is_active=True,
        is_superuser=payload.get("is_superuser", False),
        is_verified=True,
        service_id=payload.get("service_id"),
        service_attempt_id=payload.get("service_attempt_id"),
        engine_execution_id=payload.get("engine_execution_id"),
        engine_solution_id=payload.get("engine_solution_id"),
    )


def _service_token():
    token, _ = mint_service_token(
        service_id="aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
        attempt_id="11111111-1111-4111-8111-111111111111",
    )
    return token


def _runtime_principal(kind: RuntimeKind) -> UserPrincipal:
    user = UserPrincipal(
        user_id=UUID("33333333-3333-4333-8333-333333333333"),
        email="runtime@example.com",
        organization_id=UUID("22222222-2222-4222-8222-222222222222"),
    )
    context = AuthorizationContext(
        user_id=user.user_id,
        home_organization_id=user.organization_id,
        base_role_id=UUID("44444444-4444-4444-8444-444444444444"),
        is_external=False,
        base_permissions=frozenset(),
    )
    user.runtime_authority = RuntimeAuthority(
        kind=kind,
        credential_id=UUID("11111111-1111-4111-8111-111111111111"),
        run_user=RunUser(user.user_id, context, None, True),
        powers=None,
        acting_user_id=user.user_id,
        organization_id=user.organization_id,
        workflow_id=None,
        started_by_user_id=None,
        root_execution_id=None,
    )
    return user


def test_untyped_service_token_is_not_a_service_principal():
    user = _principal_for(_service_token())
    assert is_service_principal(user) is False
    assert is_engine_user(user) is False
    assert user.is_superuser is False
    assert user.is_platform_admin is False
    assert user.is_system_account is False


def test_workflow_runtime_is_engine_but_not_service_principal():
    user = _runtime_principal("workflow_execution")
    assert is_service_principal(user) is False
    assert is_engine_user(user) is True


def test_service_runtime_is_service_but_not_engine_principal():
    user = _runtime_principal("service_attempt")
    assert is_service_principal(user) is True
    assert is_engine_user(user) is False


def test_ordinary_user_is_not_service_principal():
    user = UserPrincipal(
        user_id=UUID("33333333-3333-4333-8333-333333333333"),
        email="op@example.com",
        organization_id=UUID("22222222-2222-4222-8222-222222222222"),
    )
    assert is_service_principal(user) is False
    assert is_engine_user(user) is False


def test_malformed_principal_is_not_service_principal():
    assert is_service_principal(object()) is False
    assert is_service_principal(None) is False
