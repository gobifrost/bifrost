"""Access checks in the audit log: grouping, and who may read them.

Platform Admins read the whole audit log. Platform Operators read only
``access.check`` events, and only those in organizations their role reaches.
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

import pytest

from tests.e2e.fixtures.setup import PROVIDER_ORG_ID, _register_and_authenticate_user
from tests.e2e.fixtures.users import E2EUser

pytestmark = pytest.mark.e2e

PLATFORM_OPERATOR_ROLE_ID = "00000000-0000-0000-0000-000000000007"
USER_ROLE_ID = "00000000-0000-0000-0000-000000000006"


def _ok(response, status: int = 200) -> Any:
    assert response.status_code == status, f"{response.request.url}: {response.status_code} {response.text}"
    return response.json() if response.content else None


async def _seed(session_factory, rows: list[dict]) -> None:
    from src.core.database import close_db
    from src.models.orm.audit import AuditLog

    try:
        async with session_factory() as session:
            session.add_all(AuditLog(source="http", **row) for row in rows)
            await session.commit()
    finally:
        await close_db()


@pytest.fixture(scope="module")
def world(e2e_client, platform_admin, async_session_factory):
    tag = uuid.uuid4().hex[:8]
    customer = _ok(
        e2e_client.post("/api/organizations", headers=platform_admin.headers, json={"name": f"audit-org-{tag}"}), 201
    )
    operator = E2EUser(
        email=f"audit-operator-{tag}@example.com",
        password="AuditOperator123!",
        name="Audit Operator",
        organization_id=PROVIDER_ORG_ID,
    )
    created = _ok(
        e2e_client.post(
            "/api/users",
            headers=platform_admin.headers,
            json={"email": operator.email, "name": operator.name, "organization_id": str(PROVIDER_ORG_ID)},
        ),
        201,
    )
    operator = _register_and_authenticate_user(operator)
    _ok(
        e2e_client.put(
            f"/api/users/{created['id']}/role-assignments",
            headers=platform_admin.headers,
            json={
                "base_role_id": USER_ROLE_ID,
                "additional": [{"role_id": PLATFORM_OPERATOR_ROLE_ID, "boundaries": [{"kind": "managed_organizations"}]}],
            },
        )
    )
    run = uuid.uuid4()
    workflow = str(uuid.uuid4())
    check = {"action": "access.check", "resource_type": "scope_switch", "execution_id": run}
    asyncio.run(
        _seed(
            async_session_factory,
            [
                {**check, "outcome": "failure", "organization_id": uuid.UUID(customer["id"]), "details": {"workflow_id": workflow}},
                {**check, "outcome": "failure", "organization_id": uuid.UUID(customer["id"]), "details": {"workflow_id": workflow}},
                {**check, "outcome": "success", "organization_id": PROVIDER_ORG_ID, "details": {"workflow_id": workflow}},
                {**check, "outcome": "failure", "organization_id": None, "details": {"workflow_id": workflow}},
                {"action": "user.create", "outcome": "success", "organization_id": uuid.UUID(customer["id"]), "execution_id": run},
            ],
        )
    )
    yield {"customer": customer, "operator": operator, "run": str(run), "workflow": workflow}
    e2e_client.delete(f"/api/users/{created['id']}", headers=platform_admin.headers)
    e2e_client.delete(f"/api/organizations/{customer['id']}", headers=platform_admin.headers)


def test_admins_group_access_checks_by_workflow(e2e_client, platform_admin, world) -> None:
    body = _ok(
        e2e_client.get(
            "/api/audit",
            headers=platform_admin.headers,
            params={"action": "access.check", "execution_id": world["run"], "group_by": "workflow"},
        )
    )

    assert body["entries"] == []
    [group] = body["groups"]
    assert (group["key"], group["count"]) == (world["workflow"], 4)
    assert group["sample"]["action"] == "access.check"


def test_operators_see_access_checks_in_their_reach_only(e2e_client, world) -> None:
    """Where their role is placed (customer orgs): not the provider org, not Global."""
    body = _ok(
        e2e_client.get(
            "/api/audit",
            headers=world["operator"].headers,
            params={"action": "access.check", "execution_id": world["run"]},
        )
    )

    organizations = sorted(entry["actor"]["organization_id"] for entry in body["entries"])
    assert organizations == [world["customer"]["id"]] * 2


def test_operators_do_not_see_the_retention_window(e2e_client, world) -> None:
    body = _ok(
        e2e_client.get("/api/audit", headers=world["operator"].headers, params={"action": "access.check"})
    )

    assert body["retention"] is None


def test_operators_read_nothing_else_in_the_audit_log(e2e_client, world) -> None:
    for params in ({"execution_id": world["run"]}, {"action": "user.", "execution_id": world["run"]}):
        assert e2e_client.get("/api/audit", headers=world["operator"].headers, params=params).status_code == 403


def test_customers_read_no_audit_events(e2e_client, org1_user) -> None:
    response = e2e_client.get("/api/audit", headers=org1_user.headers, params={"action": "access.check"})
    assert response.status_code == 403
