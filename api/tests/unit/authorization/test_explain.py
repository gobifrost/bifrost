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
    check_operation,
    check_permission,
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


def test_operation_without_workflow_is_the_persons_own_decision() -> None:
    trace = check_operation(_user(), None, CONTOSO, WRITE_TABLES, workflow_access=None)

    assert [s.key for s in trace.steps] == ["run_user", "powers", "target", "permission"]
    assert trace.steps[1].status == "not_applicable" and trace.steps[1].reason == "no_workflow"
    # The user base role holds no tables.readwrite, so the person alone is stopped.
    assert "tables.readwrite" not in USER_BASE_PERMISSIONS
    assert trace.steps[3].status == "stopped"
    assert trace.steps[3].facts == {"permission": "tables.readwrite"}


def test_operation_outside_reach_stops_at_target() -> None:
    trace = check_operation(_user(), None, FABRIKAM, WRITE_TABLES, workflow_access=None)

    assert _stopped_at(trace) == "target"
    assert trace.outcome == "failure" and trace.steps[-1].status == "not_reached"


def test_operator_reaches_customer_org_through_managed_boundary() -> None:
    trace = check_operation(_operator(), None, FABRIKAM, WRITE_TABLES, workflow_access=None)

    assert trace.steps[2].status == "passed" and trace.steps[2].reason.endswith("@managed_organizations")


def test_workflow_access_denied_stops_first() -> None:
    trace = check_operation(_user(), FULL, CONTOSO, WRITE_TABLES, workflow_access=False)

    assert [s.key for s in trace.steps] == ["workflow_access", "run_user", "powers", "target", "permission"]
    assert _stopped_at(trace) == "workflow_access"
    assert trace.steps[0].reason == "no_access"


def test_workflow_access_granted_to_a_person_passes() -> None:
    trace = check_operation(_user(), FULL, CONTOSO, WRITE_TABLES, workflow_access=True)

    assert trace.steps[0].label == "Workflow access"
    assert trace.steps[0].status == "passed" and trace.steps[0].reason == "access"


def test_identity_workflow_access_not_applicable() -> None:
    trace = check_operation(_user(kind="org_default"), FULL, CONTOSO, WRITE_TABLES, workflow_access=None)

    assert trace.steps[0].status == "not_applicable" and trace.steps[0].reason == "unattended"
    assert trace.outcome == "success"


def test_full_workflow_passes_permission() -> None:
    trace = check_operation(_user(), FULL, CONTOSO, WRITE_TABLES, workflow_access=True)

    assert trace.steps[-1].status == "passed" and trace.steps[-1].reason == "full"


def test_restricted_workflow_without_grant_follows_the_roles() -> None:
    trace = check_operation(_user(), RESTRICTED, CONTOSO, WRITE_TABLES, workflow_access=True)

    assert trace.steps[-1].status == "stopped"
    assert trace.steps[-1].reason != "full"


def test_restricted_workflow_grant_adds_the_permission() -> None:
    granted = Powers(WorkflowPermissionMode.RESTRICTED, (WorkflowGrant(permission="tables.readwrite"),))

    trace = check_operation(_user(), granted, CONTOSO, WRITE_TABLES, workflow_access=True)

    assert trace.steps[-1].status == "passed"


def test_global_target_is_in_everyones_reach() -> None:
    trace = check_operation(_user(), None, None, WRITE_TABLES, workflow_access=None)

    assert trace.steps[2].status == "passed" and trace.steps[2].reason == "global"


def test_a_person_uses_a_permission_their_roles_hold_at_home() -> None:
    trace = check_permission(_user(), None, "workflows.execute", CONTOSO)

    assert trace.outcome == "success"
    assert [step.status for step in trace.steps] == ["passed", "not_applicable", "passed", "passed"]


def test_a_persons_permission_outside_their_reach_stops_at_target() -> None:
    trace = check_permission(_user(), None, "agents.read", FABRIKAM)

    assert _stopped_at(trace) == "target"
    assert trace.steps[-1].status == "not_reached"
    assert trace.steps[-1].facts["permission"] == "agents.read"


def test_a_person_without_the_permission_stops_at_permission() -> None:
    trace = check_permission(_user(), None, "agents.read", CONTOSO)

    assert _stopped_at(trace) == "permission"
    assert trace.steps[-1].reason == "denied:missing:agents.read"


def test_the_permission_step_names_the_permission() -> None:
    step = check_permission(_user(), None, "agents.read.all", CONTOSO).steps[-1]

    assert step.facts == {"permission": "agents.read.all", "permission_display_name": "Read All Agents"}


def test_a_platform_admin_holds_every_permission_but_secrets() -> None:
    admin = _user(platform_admin_grant(), home=PROVIDER_ORG_ID)

    assert check_permission(admin, None, "agents.read.all", FABRIKAM).outcome == "success"
    assert check_permission(admin, None, "platformjobs.read.all", ALL_ORGS).outcome == "success"
    assert _stopped_at(check_permission(admin, None, "secrets.read", FABRIKAM)) == "permission"


def test_an_operator_holds_no_elevated_permission_in_managed_orgs() -> None:
    assert _stopped_at(check_permission(_operator(), None, "agents.read", CONTOSO)) == "permission"


def test_a_run_adds_the_workflows_powers() -> None:
    grant = WorkflowGrant(permission="agents.read")

    assert check_permission(_user(), FULL, "agents.read", CONTOSO).outcome == "success"
    assert _stopped_at(check_permission(_user(), RESTRICTED, "agents.read", CONTOSO)) == "permission"
    granted = Powers(WorkflowPermissionMode.RESTRICTED, (grant,))
    assert check_permission(_user(), granted, "agents.read", CONTOSO).outcome == "success"
