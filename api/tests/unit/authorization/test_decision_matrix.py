"""Live evaluator matrix for named permissions and original-user reach.

The former matrix compared the evaluator to retired superuser, provider, and
report-only access paths. These checks instead pin the live contract: human
callers are decided from their current authorization context, named role grants
apply only at their boundaries, and unverified credentials carry no authority.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import NAMESPACE_OID, UUID, uuid4, uuid5

from shared.builtin_roles import (
    PLATFORM_OPERATOR_PERMISSIONS,
    PLATFORM_OPERATOR_ROLE_ID,
    USER_BASE_PERMISSIONS,
    USER_ROLE_ID,
)
from src.core.constants import PROVIDER_ORG_ID, SYSTEM_USER_UUID
from src.core.principal import UserPrincipal
from src.models.contracts.access_list import AccessClass, AccessEntry, CurrentGate
from src.services.access_list import ACCESS_LIST
from src.services.authorization.context import AuthorizationContext, Boundary, BoundaryKind, RoleGrant
from src.services.authorization.enforce import Caller, NARROWER_PERMISSIONS, decide_for, entry_for_operation, operation_key
from src.services.authorization.evaluator import GLOBAL, HOME, Target, cross_org, decide
from tests.helpers.authorization import platform_admin_grant


CUSTOMER_ORG_ID = UUID("00000000-0000-0000-0000-00000000c001")
OTHER_CUSTOMER_ORG_ID = UUID("00000000-0000-0000-0000-00000000c002")


@dataclass(frozen=True)
class Persona:
    """A current authorization context paired with harmless principal facts."""

    name: str
    ctx: AuthorizationContext | None
    is_superuser: bool = False
    is_provider_org: bool = False


def _persona_ctx(name: str, home: UUID, *, admin: bool = False, external: bool = False) -> AuthorizationContext:
    return AuthorizationContext(
        user_id=uuid5(NAMESPACE_OID, name),
        home_organization_id=home,
        base_role_id=USER_ROLE_ID,
        is_external=external,
        base_permissions=USER_BASE_PERMISSIONS,
        role_grants=(platform_admin_grant(),) if admin else (),
    )


PERSONAS: tuple[Persona, ...] = (
    Persona("platform_admin", _persona_ctx("platform_admin", PROVIDER_ORG_ID, admin=True), is_superuser=True),
    Persona("provider_member", _persona_ctx("provider_member", PROVIDER_ORG_ID), is_provider_org=True),
    Persona("regular", _persona_ctx("regular", CUSTOMER_ORG_ID)),
    Persona("external", _persona_ctx("external", CUSTOMER_ORG_ID, external=True)),
    Persona("anonymous", None),
)


TARGETS: tuple[tuple[str, Target], ...] = (
    ("home", HOME),
    ("cross_org", cross_org(OTHER_CUSTOMER_ORG_ID)),
    ("cross_global", GLOBAL),
)


def _caller(persona: Persona, **principal) -> Caller:
    principal.setdefault("user_id", uuid4())
    return Caller(
        UserPrincipal(email=f"{persona.name}@example.com", organization_id=None, **principal),
        persona.ctx,
    )


def _user_ctx(home, *grants: RoleGrant) -> AuthorizationContext:
    return AuthorizationContext(
        user_id=uuid4(),
        home_organization_id=home,
        base_role_id=USER_ROLE_ID,
        is_external=False,
        base_permissions=USER_BASE_PERMISSIONS,
        role_grants=grants,
    )


_OPERATOR = Persona(
    "platform_operator",
    _user_ctx(
        PROVIDER_ORG_ID,
        RoleGrant(
            PLATFORM_OPERATOR_ROLE_ID,
            PLATFORM_OPERATOR_PERMISSIONS,
            (Boundary(BoundaryKind.MANAGED_ORGANIZATIONS),),
        ),
    ),
    is_provider_org=True,
)
_IDENTITY_CUSTOM = Persona(
    "identity_custom_role",
    _user_ctx(
        CUSTOMER_ORG_ID,
        RoleGrant(
            uuid4(),
            frozenset({"users.read", "users.readwrite"}),
            (Boundary(BoundaryKind.ORGANIZATION, CUSTOMER_ORG_ID),),
        ),
    ),
)


_HUMAN_CALLERS: tuple[tuple[Persona, Caller], ...] = (
    *((p, _caller(p, is_superuser=p.is_superuser, is_provider_org=p.is_provider_org)) for p in PERSONAS if p.ctx),
    (_OPERATOR, _caller(_OPERATOR, is_provider_org=True)),
    (_IDENTITY_CUSTOM, _caller(_IDENTITY_CUSTOM)),
)
_OPAQUE_CREDENTIALS: tuple[Caller, ...] = (
    Caller(
        UserPrincipal(
            user_id=SYSTEM_USER_UUID,
            email="system@internal.gobifrost.com",
            organization_id=None,
            is_superuser=True,
            engine_execution_id=str(uuid4()),
        ),
        None,
    ),
    Caller(
        UserPrincipal(
            user_id=uuid4(),
            email="service@example.com",
            organization_id=CUSTOMER_ORG_ID,
            service_id=str(uuid4()),
            engine_execution_id=str(uuid4()),
        ),
        None,
    ),
)


def _cutover_entries():
    for entry in ACCESS_LIST:
        if entry.current_gate != CurrentGate.EVALUATOR:
            continue
        key = operation_key(entry)
        for permission in {entry.permission, *NARROWER_PERMISSIONS.get(key, ())}:
            assert permission is not None
            yield key, permission, entry_for_operation(key, permission)


def test_cutover_routes_follow_current_named_permissions_and_original_reach() -> None:
    """``decide_for`` dispatches human callers to the canonical evaluator."""
    mismatches = []
    for key, permission, entry in _cutover_entries():
        for persona, caller in _HUMAN_CALLERS:
            for target_name, target in TARGETS:
                enforced = decide_for(caller, entry, target)
                expected = decide(persona.ctx, entry, target)
                if enforced != expected:
                    mismatches.append(
                        f"{persona.name} / {key} [{permission}] / {target_name} / "
                        f"expected={expected.allowed}:{expected.rule} / "
                        f"actual={enforced.allowed}:{enforced.rule}"
                    )
    assert not mismatches, "enforced evaluator decision drift:\n" + "\n".join(mismatches)


def test_existing_personas_and_credentials_require_current_authority() -> None:
    """Flag-shaped engine/service principals no longer authorize on their own."""
    failures = []
    for key, permission, entry in _cutover_entries():
        for target_name, target in TARGETS:
            for credential in _OPAQUE_CREDENTIALS:
                decision = decide_for(credential, entry, target)
                if decision.allowed or decision.rule != "denied:unknown_user":
                    failures.append(f"{key} [{permission}] / {target_name} / {decision}")
    assert not failures, "opaque credentials bypassed current authorization:\n" + "\n".join(failures)


def test_named_role_permission_is_limited_to_its_original_reach() -> None:
    entry = AccessEntry(
        method="GET",
        path="/test",
        access_class=AccessClass.PERMISSION,
        current_gate=CurrentGate.EVALUATOR,
        permission="users.read",
        boundary="organization",
        reason="test",
    )
    caller = _caller(_OPERATOR, is_provider_org=True)

    assert decide_for(caller, entry, cross_org(CUSTOMER_ORG_ID)).allowed
    assert not decide_for(caller, entry, cross_org(PROVIDER_ORG_ID)).allowed
    assert not decide_for(caller, entry, GLOBAL).allowed
