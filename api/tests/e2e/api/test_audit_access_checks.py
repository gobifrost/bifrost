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
    # Would-deny checks the Access Checks drill-in filters by workflow and organization.
    drill = uuid.uuid4()
    first, second = str(uuid.uuid4()), str(uuid.uuid4())
    denied = {**check, "outcome": "failure", "execution_id": drill}
    asyncio.run(
        _seed(
            async_session_factory,
            [
                {**denied, "organization_id": uuid.UUID(customer["id"]), "details": {"workflow_id": first}},
                {**denied, "organization_id": uuid.UUID(customer["id"]), "details": {"workflow_id": second}},
                {**denied, "organization_id": None, "details": {"inputs": {}}},
            ],
        )
    )
    yield {
        "customer": customer,
        "operator": operator,
        "run": str(run),
        "workflow": workflow,
        "drill": {"run": str(drill), "first": first, "second": second},
    }
    e2e_client.delete(f"/api/users/{created['id']}", headers=platform_admin.headers)
    e2e_client.delete(f"/api/organizations/{customer['id']}", headers=platform_admin.headers)


async def _seed_workflow(session_factory, name: str, display_name: str) -> uuid.UUID:
    from src.core.database import close_db
    from src.models.orm.workflows import Workflow

    try:
        async with session_factory() as session:
            workflow = Workflow(name=name, function_name=name, display_name=display_name, path=f"workflows/{name}.py")
            session.add(workflow)
            await session.commit()
            return workflow.id
    finally:
        await close_db()


async def _delete_workflow(session_factory, workflow_id: uuid.UUID) -> None:
    from sqlalchemy import delete

    from src.core.database import close_db
    from src.models.orm.workflows import Workflow

    try:
        async with session_factory() as session:
            await session.execute(delete(Workflow).where(Workflow.id == workflow_id))
            await session.commit()
    finally:
        await close_db()


@pytest.fixture(scope="module")
def named(e2e_client, platform_admin, async_session_factory, world):
    """A Fabrikam person's run whose access check targets the customer org, naming a real workflow."""
    tag = uuid.uuid4().hex[:8]
    admin = platform_admin.headers
    fabrikam = _ok(e2e_client.post("/api/organizations", headers=admin, json={"name": f"Fabrikam-{tag}"}), 201)
    person = _ok(
        e2e_client.post(
            "/api/users",
            headers=admin,
            json={"email": f"named-{tag}@fabrikam.example", "name": f"Named Person {tag}", "organization_id": fabrikam["id"]},
        ),
        201,
    )
    workflow_id = asyncio.run(_seed_workflow(async_session_factory, f"nightly_{tag}", f"Nightly Report {tag}"))
    run = uuid.uuid4()
    asyncio.run(
        _seed(
            async_session_factory,
            [
                {
                    "action": "access.check",
                    "resource_type": "scope_switch",
                    "outcome": "failure",
                    "execution_id": run,
                    "user_id": uuid.UUID(person["id"]),
                    "organization_id": uuid.UUID(world["customer"]["id"]),
                    "details": {"workflow_id": str(workflow_id)},
                }
            ],
        )
    )
    yield {"fabrikam": fabrikam, "run": str(run), "workflow_name": f"Nightly Report {tag}"}
    asyncio.run(_delete_workflow(async_session_factory, workflow_id))
    e2e_client.delete(f"/api/users/{person['id']}", headers=admin)
    e2e_client.delete(f"/api/organizations/{fabrikam['id']}", headers=admin)


def test_operators_see_the_workflow_and_the_run_user_s_own_organization(e2e_client, world, named) -> None:
    """Named server-side: Operators cannot list workflows, and the row's organization is the target."""
    params = {"action": "access.check", "execution_id": named["run"]}
    operator = world["operator"].headers

    [entry] = _ok(e2e_client.get("/api/audit", headers=operator, params=params))["entries"]
    [group] = _ok(e2e_client.get("/api/audit", headers=operator, params={**params, "group_by": "workflow"}))["groups"]

    for shown in (entry, group["sample"]):
        assert shown["workflow_name"] == named["workflow_name"]
        assert (shown["actor"]["home_organization_id"], shown["actor"]["home_organization_name"]) == (
            named["fabrikam"]["id"],
            named["fabrikam"]["name"],
        )
        assert shown["actor"]["organization_name"] == world["customer"]["name"]


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
    assert group["sample"]["workflow_name"] is None


def _drill_in(e2e_client, headers: dict, world, **filters: str) -> list[dict]:
    params = {"action": "access.check", "outcome": "failure", "execution_id": world["drill"]["run"], **filters}
    return _ok(e2e_client.get("/api/audit", headers=headers, params=params))["entries"]


def test_access_checks_filter_by_workflow(e2e_client, platform_admin, world) -> None:
    admin, drill = platform_admin.headers, world["drill"]

    [entry] = _drill_in(e2e_client, admin, world, workflow_id=drill["first"])
    assert entry["details"]["workflow_id"] == drill["first"]
    [entry] = _drill_in(e2e_client, admin, world, workflow_id="none")
    assert "workflow_id" not in entry["details"]
    bad = e2e_client.get("/api/audit", headers=admin, params={"action": "access.check", "workflow_id": "nowhere"})
    assert bad.status_code == 422

    body = _ok(
        e2e_client.get(
            "/api/audit",
            headers=admin,
            params={"action": "access.check", "execution_id": drill["run"], "workflow_id": drill["second"], "group_by": "workflow"},
        )
    )
    assert [(group["key"], group["count"]) for group in body["groups"]] == [(drill["second"], 1)]


def test_access_checks_filter_by_organization(e2e_client, platform_admin, world) -> None:
    admin, customer = platform_admin.headers, world["customer"]["id"]

    in_customer = _drill_in(e2e_client, admin, world, organization_id=customer)
    assert sorted(entry["details"]["workflow_id"] for entry in in_customer) == sorted(
        (world["drill"]["first"], world["drill"]["second"])
    )
    [entry] = _drill_in(e2e_client, admin, world, organization_id="none")
    assert entry["actor"]["organization_id"] is None


def test_operators_filter_inside_their_reach(e2e_client, world) -> None:
    operator = world["operator"].headers

    [entry] = _drill_in(e2e_client, operator, world, workflow_id=world["drill"]["first"])
    assert entry["actor"]["organization_id"] == world["customer"]["id"]
    assert _drill_in(e2e_client, operator, world, organization_id="none") == []


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
