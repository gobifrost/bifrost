"""What a workflow's identity lacks, computed from the access checks it recorded."""

from __future__ import annotations

from uuid import UUID, uuid4

from shared.builtin_roles import USER_BASE_PERMISSIONS, USER_ROLE_ID
from src.models.contracts.role_assignments import RoleBoundaryInput
from src.services.authorization.context import AuthorizationContext, Boundary, BoundaryKind, RoleGrant
from src.services.authorization.explain import ALL_ORGS, RunUser
from src.services.workflow_requirements import (
    CheckedAction,
    Holder,
    RoleRef,
    build_requirements,
)

CONTOSO = UUID("00000000-0000-0000-0000-00000000c001")
FABRIKAM = UUID("00000000-0000-0000-0000-00000000f001")
NAMES = {CONTOSO: "Contoso", FABRIKAM: "Fabrikam"}
HR = RoleRef(id=UUID("00000000-0000-0000-0000-0000000000a1"), name="HR", builtin=False)
OPERATOR = RoleRef(id=UUID("00000000-0000-0000-0000-000000000007"), name="Platform Operator", builtin=True)
USER = RoleRef(id=USER_ROLE_ID, name="User", builtin=True)
ROLES = {ref: role for role in (HR, OPERATOR, USER) for ref in (role.name, str(role.id))}


def _holder(*held: RoleRef, home: UUID | None = CONTOSO, placed_at: tuple[UUID, ...] = ()) -> Holder:
    boundaries = tuple(Boundary(BoundaryKind.ORGANIZATION, org) for org in placed_at)
    placed = (RoleGrant(uuid4(), frozenset(), boundaries),) if boundaries else ()
    ctx = AuthorizationContext(
        user_id=uuid4(),
        home_organization_id=home,
        base_role_id=USER_ROLE_ID,
        is_external=False,
        base_permissions=USER_BASE_PERMISSIONS,
        role_grants=(
            *(RoleGrant(role.id, frozenset(), (Boundary(BoundaryKind.PLATFORM),)) for role in held),
            *placed,
        ),
    )
    return Holder(
        run_user=RunUser(user_id=ctx.user_id, ctx=ctx, identity_kind="custom"),
        held_roles=frozenset(ref for role in held for ref in (role.name, str(role.id))),
    )


def _build(actions, holder: Holder, *, workflow_roles: dict[UUID, str] | None = None, may_open: bool = False):
    return build_requirements(
        actions,
        holder=holder,
        roles_by_ref=ROLES,
        organization_names=NAMES,
        workflow_roles=workflow_roles or {},
        may_open_workflow=may_open,
    )


def test_no_recorded_checks_means_no_requirements() -> None:
    assert _build([], _holder(), workflow_roles={uuid4(): "Dispatchers"}) == []


def test_scope_switch_outside_the_identitys_reach_is_a_reach_requirement() -> None:
    (item,) = _build([CheckedAction("scope_switch", FABRIKAM, ())], _holder())

    assert (item.kind, item.label) == ("reach", "Fabrikam")
    assert "switches into" in item.detail
    assert item.organization_id == FABRIKAM
    assert item.grant is None


def test_targets_in_reach_need_nothing() -> None:
    actions = [
        CheckedAction("scope_switch", CONTOSO, ()),
        CheckedAction("child_run", None, ()),
        CheckedAction("scope_switch", FABRIKAM, ()),
    ]

    assert _build(actions, _holder(placed_at=(FABRIKAM,), home=CONTOSO)) == []


def test_one_requirement_per_target_names_every_way_the_workflow_acts_there() -> None:
    actions = [
        CheckedAction("scope_switch", FABRIKAM, ()),
        CheckedAction("child_run", FABRIKAM, ()),
        CheckedAction("scope_switch", FABRIKAM, ()),
    ]

    (item,) = _build(actions, _holder())

    assert "switches into" in item.detail and "starts child runs in" in item.detail


def test_reading_every_organization_is_a_reach_requirement() -> None:
    (item,) = _build([CheckedAction("scope_switch", ALL_ORGS, ())], _holder())

    assert (item.kind, item.label) == ("reach", "All Organizations")
    assert item.organization_id is None


def test_missing_policy_role_is_granted_at_the_target_organization() -> None:
    (item,) = _build([CheckedAction("policy", FABRIKAM, ("role:HR",))], _holder())

    assert (item.kind, item.label) == ("policy_role", "HR")
    assert item.organization_id == FABRIKAM
    assert item.grant is not None
    assert item.grant.role_id == HR.id
    assert item.grant.boundaries == [RoleBoundaryInput(kind="organization", organization_id=FABRIKAM)]


def test_policy_role_on_a_global_table_is_granted_at_the_identitys_home() -> None:
    (item,) = _build([CheckedAction("policy", None, ("role:HR",))], _holder())

    assert item.organization_id == CONTOSO
    assert item.grant is not None
    assert item.grant.boundaries == [RoleBoundaryInput(kind="organization", organization_id=CONTOSO)]


def test_policy_role_for_a_global_identity_is_granted_at_the_platform() -> None:
    (item,) = _build([CheckedAction("policy", None, ("role:HR",))], _holder(home=None))

    assert item.organization_id is None
    assert item.grant is not None
    assert item.grant.boundaries == [RoleBoundaryInput(kind="platform")]


def test_policy_role_the_identity_now_holds_is_resolved() -> None:
    assert _build([CheckedAction("policy", CONTOSO, ("role:HR",))], _holder(HR)) == []


def test_policy_role_named_by_id_is_matched_to_the_role() -> None:
    (item,) = _build([CheckedAction("policy", CONTOSO, (f"role:{HR.id}",))], _holder())

    assert (item.label, item.grant is not None) == ("HR", True)


def test_builtin_and_unknown_policy_roles_are_listed_without_a_grant() -> None:
    actions = [CheckedAction("policy", CONTOSO, ("role:Platform Operator", "role:Ghost"))]

    items = _build(actions, _holder())

    assert [(item.label, item.grant) for item in items] == [("Ghost", None), ("Platform Operator", None)]


def test_missing_claims_are_not_requirements() -> None:
    assert _build([CheckedAction("policy", CONTOSO, ("claim:department",))], _holder()) == []


def test_the_same_role_at_the_same_place_is_listed_once() -> None:
    actions = [CheckedAction("policy", FABRIKAM, ("role:HR",)), CheckedAction("policy", FABRIKAM, ("role:HR",))]

    assert len(_build(actions, _holder())) == 1


def test_role_based_workflow_needs_the_identity_to_hold_one_of_its_roles() -> None:
    dispatchers, auditors = uuid4(), uuid4()
    actions = [CheckedAction("run_as", None, ())]

    (item,) = _build(actions, _holder(), workflow_roles={dispatchers: "Dispatchers", auditors: "Auditors"})

    assert (item.kind, item.label, item.organization_id, item.grant) == (
        "workflow_role",
        "Auditors, Dispatchers",
        None,
        None,
    )


def test_identity_that_may_open_the_workflow_needs_no_workflow_role() -> None:
    actions = [CheckedAction("run_as", None, ())]

    assert _build(actions, _holder(), workflow_roles={uuid4(): "Dispatchers"}, may_open=True) == []


def test_the_base_role_is_not_a_held_role_unless_it_is_a_membership() -> None:
    (item,) = _build([CheckedAction("policy", CONTOSO, ("role:User",))], _holder())

    assert (item.kind, item.label, item.grant) == ("policy_role", "User", None)


def test_requirements_list_reach_first_then_policy_roles_then_workflow_roles() -> None:
    actions = [
        CheckedAction("policy", CONTOSO, ("role:HR",)),
        CheckedAction("scope_switch", FABRIKAM, ()),
    ]

    items = _build(actions, _holder(), workflow_roles={uuid4(): "Dispatchers"})

    assert [item.kind for item in items] == ["reach", "policy_role", "workflow_role"]
