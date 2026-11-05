from uuid import uuid4

import pytest

from shared.workflow_conversion_access import recorded_permission_requirements


@pytest.mark.asyncio
async def test_observed_permissions_follow_successful_decisions_and_not_route_defaults(
    db_session,
) -> None:
    from src.models.orm.audit import AuditLog

    workflow_id = uuid4()

    def step(key, permission, status="passed"):
        return {"key": key, "status": status, "facts": {"permission": permission}}

    for outcome, steps in [
        ("success", [
            step("permission", "executions.read.all"),
            step("privileged_target", "privilegedaccess.readwrite"),
            step("secret", "secrets.read", "not_reached"),
            step("permission", "*"),
        ]),
        ("failure", [step("permission", "users.impersonate")]),
    ]:
        db_session.add(AuditLog(
            action="access.check",
            outcome=outcome,
            details={"workflow_id": str(workflow_id), "enforced": True, "trace": {"steps": steps}},
        ))
    await db_session.flush()

    assert await recorded_permission_requirements(
        db_session, workflow_id, window_days=30
    ) == ["executions.read.all", "privilegedaccess.readwrite"]


@pytest.mark.asyncio
@pytest.mark.parametrize("compact", [False, True])
@pytest.mark.parametrize("in_reach", [False, True])
@pytest.mark.parametrize("enforced", [False, True])
async def test_full_access_decisions_retain_observed_named_permission(
    db_session, compact, in_reach, enforced,
):
    from shared.access_trace_storage import compact_access_check_details
    from src.models.contracts.workflow_permissions import WorkflowPermissionMode
    from src.models.orm.audit import AuditLog
    from src.services.authorization.context import AuthorizationContext
    from src.services.authorization.explain import Powers, RunUser, check_target, permission_entry

    workflow_id, user_id, org_id = uuid4(), uuid4(), uuid4()
    ctx = AuthorizationContext(
        user_id=user_id, home_organization_id=org_id,
        base_role_id=uuid4(), is_external=False, base_permissions=frozenset(),
    )
    trace = check_target(
        RunUser(user_id, ctx, None, is_active=True),
        Powers(WorkflowPermissionMode.FULL, ()), org_id if in_reach else uuid4(),
        permission_entry("executions.read.all"),
    )
    assert trace.outcome == ("success" if in_reach else "failure")
    details = {
        "workflow_id": str(workflow_id), "enforced": enforced, "direct": False,
        "trace": {**trace.as_dict(), "enforced": enforced},
        "inputs": {"operation": "executions.list", "target": str(org_id)},
    }
    if compact:
        details = compact_access_check_details(details)
    db_session.add(AuditLog(action="access.check", outcome=trace.outcome, details=details))
    await db_session.flush()

    assert await recorded_permission_requirements(
        db_session, workflow_id, window_days=30,
    ) == (["executions.read.all"] if in_reach and enforced else [])


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome,enforced,status,expected", [
    ("success", True, "passed", ["executions.read.all"]),
    ("failure", True, "stopped", []),
    ("success", True, "not_reached", []),
    ("success", False, "passed", []),
])
async def test_recorded_named_enforcement_inputs_identify_historical_full_access_check(
    db_session, outcome, enforced, status, expected,
):
    from src.models.orm.audit import AuditLog

    workflow_id = uuid4()
    db_session.add(AuditLog(
        action="access.check", outcome=outcome,
        details={
            "workflow_id": str(workflow_id), "enforced": enforced,
            "inputs": {"permission": "executions.read.all"},
            "trace": {"steps": [
                {"key": "permission", "status": status, "reason": "full", "facts": {}},
            ]},
        },
    ))
    await db_session.flush()
    assert await recorded_permission_requirements(
        db_session, workflow_id, window_days=30,
    ) == expected
