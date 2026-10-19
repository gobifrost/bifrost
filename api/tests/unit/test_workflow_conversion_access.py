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
            details={"workflow_id": str(workflow_id), "trace": {"steps": steps}},
        ))
    await db_session.flush()

    assert await recorded_permission_requirements(
        db_session, workflow_id, window_days=30
    ) == ["executions.read.all", "privilegedaccess.readwrite"]
