"""Persona x operation matrix: the R2c evaluator against today's behaviour.

For every access-list entry, every persona and every target, the evaluator
must decide what today's code decides (``legacy_oracle``), except the two
listed differences:

- a provider-org member on an entry that records an ``intended_change`` (the
  provider-org non-admin path goes away at R3), and
- a Platform Admin on an entry gated by a permission the wildcard does not
  satisfy (``WILDCARD_EXCLUDED_PERMISSIONS``: secret decryption must be
  assigned explicitly, never implied by the admin base role).

Routes already cut over to the evaluator (``current_gate=evaluator``) are
also checked through the enforced decision itself, with execution
credentials and the personas whose access the cutover approves (see the
section at the end).
"""

from __future__ import annotations

from collections import defaultdict
from uuid import uuid4

from shared.builtin_roles import (
    PLATFORM_OPERATOR_PERMISSIONS,
    PLATFORM_OPERATOR_ROLE_ID,
    USER_BASE_PERMISSIONS,
    USER_ROLE_ID,
)
from src.core.constants import PROVIDER_ORG_ID, SYSTEM_USER_UUID
from src.core.principal import UserPrincipal
from src.models.contracts.access_list import AccessEntry, CurrentGate
from src.models.contracts.permissions import WILDCARD_EXCLUDED_PERMISSIONS
from src.services.access_list import ACCESS_LIST, effective_entries
from src.services.authorization.context import (
    AuthorizationContext,
    Boundary,
    BoundaryKind,
    RoleGrant,
)
from src.services.authorization.enforce import (
    NARROWER_PERMISSIONS,
    Caller,
    decide_for,
    entry_for_operation,
    operation_key,
)
from src.services.authorization.evaluator import GLOBAL, HOME, Target, cross_org, decide
from tests.unit.authorization.legacy_oracle import (
    CUSTOMER_ORG_ID,
    OTHER_CUSTOMER_ORG_ID,
    PERSONAS,
    Persona,
    legacy_decide,
)

TARGETS: tuple[tuple[str, Target, bool], ...] = (
    ("home", HOME, False),
    ("cross_org", cross_org(OTHER_CUSTOMER_ORG_ID), True),
    ("cross_global", GLOBAL, True),
)

_DECIDERS = dict(zip((e.key for e in ACCESS_LIST), effective_entries(ACCESS_LIST)))


def _label(entry: AccessEntry) -> str:
    return entry.mcp_tool or f"{entry.method} {entry.path}"


def _cells():
    for entry in ACCESS_LIST:
        decider = _DECIDERS[entry.key]
        for persona in PERSONAS:
            for target_name, target, cross in TARGETS:
                legacy = legacy_decide(persona, decider, cross)
                decision = decide(persona.ctx, decider, target)
                yield entry, decider, persona, target_name, legacy, decision


def _is_provider_difference(decider: AccessEntry, persona: Persona, legacy: bool, allowed: bool) -> bool:
    return persona.name == "provider_member" and decider.intended_change is not None and legacy and not allowed


def _is_wildcard_exclusion_difference(decider: AccessEntry, persona: Persona, legacy: bool, allowed: bool) -> bool:
    return (
        persona.name == "platform_admin"
        and decider.permission in WILDCARD_EXCLUDED_PERMISSIONS
        and legacy
        and not allowed
    )


def _is_listed_difference(decider: AccessEntry, persona: Persona, legacy: bool, allowed: bool) -> bool:
    return _is_provider_difference(decider, persona, legacy, allowed) or _is_wildcard_exclusion_difference(
        decider, persona, legacy, allowed
    )


def _row(entry: AccessEntry, persona: Persona, target_name: str, legacy: bool, decision) -> str:
    return f"{persona.name} / {_label(entry)} / {target_name} / legacy={legacy} / new={decision.allowed} / {decision.rule}"


def test_evaluator_matches_legacy_except_listed_differences() -> None:
    mismatches = []
    for entry, decider, persona, target_name, legacy, decision in _cells():
        if legacy == decision.allowed:
            continue
        if _is_listed_difference(decider, persona, legacy, decision.allowed):
            continue
        mismatches.append(_row(entry, persona, target_name, legacy, decision))
    assert not mismatches, f"{len(mismatches)} unexpected differences:\n" + "\n".join(mismatches)


def test_every_intended_change_marker_produces_a_difference() -> None:
    differing: dict = defaultdict(bool)
    for entry, decider, persona, _, legacy, decision in _cells():
        if _is_provider_difference(decider, persona, legacy, decision.allowed):
            differing[entry.key] = True
    stale = [_label(e) for e in ACCESS_LIST if _DECIDERS[e.key].intended_change and not differing[e.key]]
    assert not stale, f"intended_change set but no difference produced: {stale}"


def test_every_wildcard_excluded_entry_produces_a_difference() -> None:
    differing: dict = defaultdict(bool)
    for entry, decider, persona, _, legacy, decision in _cells():
        if _is_wildcard_exclusion_difference(decider, persona, legacy, decision.allowed):
            differing[entry.key] = True
    excluded = [e for e in ACCESS_LIST if _DECIDERS[e.key].permission in WILDCARD_EXCLUDED_PERMISSIONS]
    assert excluded, "expected at least one entry gated by a wildcard-excluded permission"
    stale = [_label(e) for e in excluded if not differing[e.key]]
    assert not stale, f"wildcard-excluded permission but no difference produced: {stale}"


# ---------------------------------------------------------------------------
# Routes cut over to the evaluator (R3a): the enforced decision
# (``enforce.decide_for``, including the execution-credential branch) against
# today's superuser gate, for every persona that exists today plus the
# Platform Operator and a custom identity role, which are the only NEW rows.
# Row-level rules (protected targets, the grant ceiling) are pinned in
# tests/unit/test_identity_authorization.py and test_privilege.py.
# ---------------------------------------------------------------------------



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

_CUTOVER_CALLERS: tuple[tuple[Persona, Caller], ...] = (
    *(
        (p, _caller(p, is_superuser=p.is_superuser, is_provider_org=p.is_provider_org))
        for p in PERSONAS
        if p.ctx
    ),
    (_OPERATOR, _caller(_OPERATOR, is_provider_org=True)),
    (_IDENTITY_CUSTOM, _caller(_IDENTITY_CUSTOM)),
    (
        Persona("engine_token", PERSONAS[2].ctx, is_superuser=True),
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
    ),
    (
        Persona("service_token", PERSONAS[2].ctx),
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
    ),
)

# The approved NEW rows: operations (by key, and the permission that
# decides them) the Platform Operator may perform at a customer organization
# (never at its own provider org, never at Global), and a custom role with
# users.read + users.readwrite at its holder's own organization may perform
# there.
_OPERATOR_AT_CUSTOMER_ORG = {
    ("users.list", "users.read"),
    ("users.get", "users.read"),
    ("users.create", "users.readwrite"),
    ("users.update", "users.readwrite"),
    ("users.bulk_update", "users.readwrite"),
    ("users.bulk_update", "roleassignments.readwrite"),
    ("users.invites.resend", "users.readwrite"),
    ("users.invites.send", "users.readwrite"),
    ("users.invites.regenerate", "users.readwrite"),
    ("users.invites.revoke", "users.readwrite"),
    ("users.mfa.reset", "users.readwrite"),
    ("POST /auth/admin/revoke-user", "users.readwrite"),
    ("users.roles.list", "roleassignments.read"),
    ("users.forms.list", "roleassignments.read"),
    ("GET /api/users/{user_id}/role-assignments", "roleassignments.read"),
    ("PUT /api/users/{user_id}/role-assignments", "roleassignments.readwrite"),
    ("roles.users.list", "roleassignments.read"),
    ("roles.users.assign", "roleassignments.readwrite"),
    ("roles.users.remove", "roleassignments.readwrite"),
    ("roles.users.bulk_remove", "roleassignments.readwrite"),
    ("organizations.list", "organizations.read"),
    ("organizations.get", "organizations.read"),
}
_IDENTITY_CUSTOM_AT_HOME = {
    (key, permission)
    for key, permission in _OPERATOR_AT_CUSTOMER_ORG
    if permission in ("users.read", "users.readwrite")
}
_APPROVED_NEW = {
    "platform_operator": {(key, perm, "cross_org") for key, perm in _OPERATOR_AT_CUSTOMER_ORG},
    "identity_custom_role": {(key, perm, "home") for key, perm in _IDENTITY_CUSTOM_AT_HOME},
}


def _cutover_cells():
    for entry in ACCESS_LIST:
        if entry.current_gate != CurrentGate.EVALUATOR:
            continue
        key = operation_key(entry)
        for permission in {entry.permission, *NARROWER_PERMISSIONS.get(key, ())}:
            variant = entry_for_operation(key, permission)
            for persona, caller in _CUTOVER_CALLERS:
                for target_name, target, cross in TARGETS:
                    legacy = legacy_decide(persona, variant, cross)
                    decision = decide_for(caller, variant, target)
                    yield key, permission, persona, target_name, legacy, decision


def test_cutover_routes_change_only_the_approved_rows() -> None:
    unexpected = []
    for key, permission, persona, target_name, legacy, decision in _cutover_cells():
        approved = (key, permission, target_name) in _APPROVED_NEW.get(persona.name, set())
        expected = True if approved else legacy
        if decision.allowed != expected:
            unexpected.append(
                f"{persona.name} / {key} [{permission}] / {target_name} / "
                f"legacy={legacy} / new={decision.allowed} / {decision.rule}"
            )
    assert not unexpected, f"{len(unexpected)} unexpected decisions:\n" + "\n".join(unexpected)


def test_every_approved_new_row_is_a_real_change() -> None:
    changed = {
        (persona.name, key, permission, target_name)
        for key, permission, persona, target_name, legacy, decision in _cutover_cells()
        if decision.allowed and not legacy
    }
    stale = [
        (name, *row)
        for name, rows in _APPROVED_NEW.items()
        for row in rows
        if (name, *row) not in changed
    ]
    assert not stale, f"approved rows that change nothing (stale table): {stale}"


def test_existing_personas_and_credentials_decide_exactly_as_today() -> None:
    changed = [
        (persona.name, key, permission, target_name)
        for key, permission, persona, target_name, legacy, decision in _cutover_cells()
        if persona.name not in _APPROVED_NEW and decision.allowed != legacy
    ]
    assert not changed, changed
