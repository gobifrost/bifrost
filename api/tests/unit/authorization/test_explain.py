"""The report-only decision core: what the managed-identity model would decide."""

from __future__ import annotations

from uuid import UUID, uuid4

from shared.builtin_roles import (
    PLATFORM_OPERATOR_PERMISSIONS,
    PLATFORM_OPERATOR_ROLE_ID,
    USER_BASE_PERMISSIONS,
    USER_ROLE_ID,
)
from src.core.constants import PROVIDER_ORG_ID
from src.models.contracts.access_list import AccessClass, AccessEntry, CurrentGate
from src.models.contracts.workflow_permissions import WorkflowGrant, WorkflowPermissionMode
from src.services.authorization.context import (
    AuthorizationContext,
    Boundary,
    BoundaryKind,
    RoleGrant,
)
from src.services.authorization.explain import (
    ALL_ORGS,
    Powers,
    RunUser,
    check_entry,
    check_policy,
    check_run_as,
    check_secret,
    check_target,
    in_reach,
)
from tests.helpers.authorization import platform_admin_grant

CONTOSO = UUID("00000000-0000-0000-0000-00000000c001")
FABRIKAM = UUID("00000000-0000-0000-0000-00000000f001")
FULL = Powers(WorkflowPermissionMode.FULL, ())
RESTRICTED = Powers(WorkflowPermissionMode.RESTRICTED, ())
WRITE_TABLES = AccessEntry(
    method="POST",
    path="/api/tables/{table}/documents",
    access_class=AccessClass.PERMISSION,
    current_gate=CurrentGate.AUTHENTICATED,
    permission="tables.readwrite",
    boundary="organization",
    reason="test",
)


def _user(*grants: RoleGrant, home: UUID | None = CONTOSO, kind: str | None = None) -> RunUser:
    ctx = AuthorizationContext(
        user_id=uuid4(),
        home_organization_id=home,
        base_role_id=USER_ROLE_ID,
        is_external=False,
        base_permissions=USER_BASE_PERMISSIONS,
        role_grants=tuple(grants),
    )
    return RunUser(user_id=ctx.user_id, ctx=ctx, identity_kind=kind)


def _operator() -> RunUser:
    return _user(
        RoleGrant(
            PLATFORM_OPERATOR_ROLE_ID,
            PLATFORM_OPERATOR_PERMISSIONS,
            (Boundary(BoundaryKind.MANAGED_ORGANIZATIONS),),
        ),
        home=PROVIDER_ORG_ID,
    )


def _stopped_at(trace) -> str | None:
    stopped = [step.key for step in trace.steps if step.status == "stopped"]
    return stopped[0] if stopped else None


def test_customer_reaches_home_and_global_only() -> None:
    customer = _user()

    assert in_reach(customer, CONTOSO) == (True, "home")
    assert in_reach(customer, None) == (True, "global")
    assert in_reach(customer, FABRIKAM) == (False, "outside")
    assert in_reach(customer, ALL_ORGS) == (False, "outside")


def test_a_full_run_for_a_customer_may_write_home_and_global() -> None:
    customer = _user()

    assert check_target(customer, FULL, CONTOSO, WRITE_TABLES).outcome == "success"
    assert check_target(customer, FULL, None, WRITE_TABLES).outcome == "success"


def test_a_full_run_for_a_customer_stops_at_another_org() -> None:
    trace = check_target(_user(), FULL, FABRIKAM, WRITE_TABLES)

    assert trace.outcome == "failure"
    assert trace.enforced is False
    assert _stopped_at(trace) == "target"
    assert [step.status for step in trace.steps] == ["passed", "passed", "stopped", "not_reached"]


def test_an_operator_reaches_customer_orgs_through_the_role() -> None:
    operator = _operator()

    ok, via = in_reach(operator, CONTOSO)
    assert ok and via == f"role:{PLATFORM_OPERATOR_ROLE_ID}@managed_organizations"
    assert in_reach(operator, PROVIDER_ORG_ID) == (True, "home")
    assert check_target(operator, FULL, FABRIKAM, WRITE_TABLES).outcome == "success"


def test_a_platform_admin_reaches_every_org() -> None:
    admin = _user(platform_admin_grant(), home=PROVIDER_ORG_ID, kind="org_default")

    assert in_reach(admin, FABRIKAM) == (True, "platform_admin")
    assert in_reach(admin, ALL_ORGS) == (True, "platform_admin")


def test_the_global_identity_reaches_global_only() -> None:
    global_identity = _user(home=None, kind="global_default")

    assert check_target(global_identity, FULL, None, WRITE_TABLES).outcome == "success"
    assert _stopped_at(check_target(global_identity, FULL, CONTOSO, WRITE_TABLES)) == "target"


def test_restricted_without_grants_uses_only_the_users_own_roles() -> None:
    trace = check_target(_user(), RESTRICTED, CONTOSO, WRITE_TABLES)

    assert trace.outcome == "failure"
    assert _stopped_at(trace) == "permission"


def test_restricted_grants_add_permissions_within_reach() -> None:
    granted = Powers(
        WorkflowPermissionMode.RESTRICTED,
        (WorkflowGrant(permission="tables.readwrite", boundary="managed_organizations"),),
    )

    assert check_target(_operator(), granted, CONTOSO, WRITE_TABLES).outcome == "success"
    assert _stopped_at(check_target(_user(), granted, FABRIKAM, WRITE_TABLES)) == "target"


def test_restricted_without_an_entry_cannot_judge_the_permission() -> None:
    trace = check_target(_user(), RESTRICTED, CONTOSO, None)

    assert trace.outcome == "success"
    assert trace.steps[-1].status == "not_applicable"


def test_run_as_is_a_power_full_adds_and_restricted_lacks() -> None:
    other = uuid4()

    assert check_run_as(_user(), FULL, other).outcome == "success"
    trace = check_run_as(_user(), RESTRICTED, other)
    assert trace.outcome == "failure"
    assert trace.steps[-1].reason == "restricted:no_grant"


def test_entry_follows_the_run_users_access() -> None:
    assert check_entry(_user(), allowed=True, subject="workflow").outcome == "success"
    assert _stopped_at(check_entry(_user(), allowed=False, subject="workflow")) == "entry"


def test_policy_records_the_model_outcome_and_what_is_missing() -> None:
    trace = check_policy(_user(), FULL, today=True, model=False, missing=["role:HR"])

    assert trace.outcome == "failure"
    assert trace.steps[-1].facts == {"today": True, "missing": ["role:HR"]}


def test_secrets_come_with_full_and_need_a_grant_when_restricted() -> None:
    assert check_secret(_user(), FULL, CONTOSO, kind="config", name="api_key").outcome == "success"
    assert check_secret(_user(), RESTRICTED, CONTOSO, kind="config", name="api_key").outcome == "failure"
    secrets = Powers(WorkflowPermissionMode.RESTRICTED, (WorkflowGrant(permission="secrets.read"),))
    assert check_secret(_user(), secrets, CONTOSO, kind="config", name="api_key").outcome == "success"


def test_trace_serialises_for_the_audit_log() -> None:
    data = check_target(_user(), FULL, FABRIKAM, WRITE_TABLES).as_dict()

    assert data["outcome"] == "failure"
    assert data["enforced"] is False
    assert data["steps"][2]["key"] == "target"
