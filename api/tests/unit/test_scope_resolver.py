"""SDK scopes select only targets in the original user's reach."""

from uuid import uuid4

import pytest

from shared import access_checks
from shared.builtin_roles import USER_BASE_PERMISSIONS, USER_ROLE_ID
from shared.scope_resolver import ScopeNotAllowed, UNSET, resolve_effective_scope, shared_read_organization
from src.core.principal import UserPrincipal
from src.services.authorization.context import AuthorizationContext, Boundary, BoundaryKind, RoleGrant
from src.services.authorization.enforce import Caller
from src.services.authorization.explain import RunUser
from src.services.authorization.runtime import RuntimeAuthority

ORG_A = uuid4()
ORG_B = uuid4()


def _caller(*, home=ORG_A, runtime_org=None, placed_at_b=False, managed=False):
    grants = ()
    if placed_at_b or managed:
        boundary = Boundary(BoundaryKind.MANAGED_ORGANIZATIONS) if managed else Boundary(BoundaryKind.ORGANIZATION, ORG_B)
        grants = (RoleGrant(uuid4(), frozenset(), (boundary,)),)
    user_id = uuid4()
    context = AuthorizationContext(
        user_id=user_id, home_organization_id=home, base_role_id=USER_ROLE_ID,
        base_permissions=USER_BASE_PERMISSIONS, is_external=False, role_grants=grants,
    )
    principal = UserPrincipal(user_id=user_id, email="scope@example.test", organization_id=home)
    if runtime_org is not None:
        principal.runtime_authority = RuntimeAuthority(
            kind="workflow_execution", credential_id=uuid4(),
            run_user=RunUser(user_id, context, None, True), powers=None,
            acting_user_id=user_id, organization_id=runtime_org, workflow_id=None,
            started_by_user_id=user_id, root_execution_id=uuid4(),
        )
    return Caller(principal, context)


def test_unset_uses_human_home_when_it_is_in_reach() -> None:
    assert resolve_effective_scope(caller=_caller(), requested_scope=UNSET) == ORG_A


def test_unset_uses_typed_runtime_target() -> None:
    assert resolve_effective_scope(caller=_caller(runtime_org=ORG_B, placed_at_b=True), requested_scope=UNSET) == ORG_B


def test_explicit_global_is_a_reachable_target() -> None:
    assert resolve_effective_scope(caller=_caller(), requested_scope=None) is None


def test_other_organization_outside_reach_is_refused_and_recorded_as_enforced() -> None:
    caller = _caller()
    token = access_checks.collect_person(caller.principal.user_id)
    try:
        with pytest.raises(ScopeNotAllowed):
            resolve_effective_scope(caller=caller, requested_scope=ORG_B)
        collector = access_checks.current()
        assert collector is not None
        [note] = collector.notes
        assert note.facts["enforced"] is True
        assert note.decision is not None and note.decision[1].outcome == "failure"
        assert note.target == ORG_B
    finally:
        access_checks.stop_collecting(token)


def test_managed_reach_selects_customer_scope_without_provider_bypass() -> None:
    assert resolve_effective_scope(caller=_caller(managed=True), requested_scope=ORG_B) == ORG_B


def test_unset_is_singleton_and_falsy() -> None:
    assert not UNSET
    assert repr(UNSET) == "UNSET"


def test_shared_global_read_uses_selected_organization_but_explicit_global_does_not() -> None:
    caller = _caller()
    assert shared_read_organization(caller, None) == ORG_A
    assert shared_read_organization(caller, None, selected_organization_id=ORG_B) == ORG_B
    assert shared_read_organization(caller, None, selected_organization_id=None) is None
    assert shared_read_organization(caller, ORG_B) == ORG_B


def test_runtime_shared_read_uses_runtime_scope_without_changing_original_home() -> None:
    caller = _caller(runtime_org=ORG_B, placed_at_b=True)
    assert shared_read_organization(caller, None) == ORG_B
    assert caller.ctx.home_organization_id == ORG_A
