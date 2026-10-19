"""Enforced access-check contracts outside the identity-matrix scenarios."""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

import pytest

from tests.e2e.conftest import execute_workflow_sync, poll_until, write_and_register

pytestmark = pytest.mark.e2e

PROBE = '''"""Access-check contract probe."""
from bifrost import config, tables, workflow


@workflow(name="{name}", description="Access-check contract probe")
async def {name}(hr_table: str, admin_table: str, secret_key: str):
    out = {{}}
    for attempt in ("first", "second"):
        try:
            await tables.insert(hr_table, {{"attempt": attempt}})
            out[attempt] = "inserted"
        except Exception as exc:
            out[attempt] = type(exc).__name__
    try:
        # By name, no scope: resolves to the Global table (the pattern
        # customer-started runs use in production).
        await tables.insert(admin_table, {{"by": "run"}})
        out["admin_table"] = "inserted"
    except Exception as exc:
        out["admin_table"] = type(exc).__name__
    out["secret"] = (await config.get(secret_key)) is not None
    return out
'''


def _ok(response) -> Any:
    assert response.status_code in (200, 201, 204), f"{response.request.url}: {response.status_code} {response.text}"
    return response.json() if response.content else None


async def _checks(session_factory, **where) -> list[dict]:
    from sqlalchemy import select

    from src.core.database import close_db
    from src.models.orm.audit import AuditLog

    try:
        async with session_factory() as session:
            query = select(AuditLog).where(AuditLog.action == "access.check")
            for column, value in where.items():
                query = query.where(getattr(AuditLog, column) == value)
            rows = (await session.execute(query)).scalars().all()
            return [
                {"resource_type": r.resource_type, "outcome": r.outcome, "user_id": r.user_id, "details": r.details}
                for r in rows
            ]
    finally:
        await close_db()


def _person(e2e_client, admin, org_id: str, *, tag: str, key: str):
    """A person of our own, so these runs never count against shared fixture users."""
    from tests.e2e.fixtures.setup import _register_and_authenticate_user
    from tests.e2e.fixtures.users import E2EUser

    created = _ok(
        e2e_client.post(
            "/api/users",
            headers=admin,
            json={"email": f"ac-{key}-{tag}@contoso.example", "name": f"AC {key}", "organization_id": org_id},
        )
    )
    person = _register_and_authenticate_user(
        E2EUser(email=created["email"], password=f"Ac-{tag}-Pass1!", name=f"AC {key}", organization_id=uuid.UUID(org_id)),
        skip_registration=False,
    )
    return created, person


@pytest.fixture(scope="module")
def world(e2e_client, platform_admin, org1, async_session_factory):
    tag = uuid.uuid4().hex[:8]
    admin = platform_admin.headers
    role = _ok(e2e_client.post("/api/roles", headers=admin, json={"name": f"AC HR {tag}"}))
    hr, hr_user = _person(e2e_client, admin, org1["id"], tag=tag, key="hr")
    customer, customer_user = _person(e2e_client, admin, org1["id"], tag=tag, key="customer")
    _ok(e2e_client.post(f"/api/roles/{role['id']}/users", headers=admin, json={"user_ids": [hr["id"]]}))
    hr_table = _ok(
        e2e_client.post(
            "/api/tables",
            headers=admin,
            json={
                "name": f"ac_hr_{tag}",
                "organization_id": org1["id"],
                "policies": {
                    "policies": [
                        {"name": "hr_writes", "actions": ["create", "read"], "when": {"call": "has_role", "args": [role["name"]]}}
                    ]
                },
            },
        ),
    )
    admin_table = _ok(
        e2e_client.post(
            "/api/tables",
            headers=admin,
            json={
                "name": f"ac_admin_{tag}",
                "organization_id": None,
                "policies": {
                    "policies": [
                        {
                            "name": "full_workflow_authority",
                            "actions": ["create"],
                            "when": {"authority": "platform_admin"},
                        }
                    ]
                },
            },
        ),
    )
    secret_key = f"ac_secret_{tag}"
    config = _ok(
        e2e_client.post(
            "/api/config",
            headers=admin,
            json={"key": secret_key, "value": f"s-{tag}", "type": "secret", "organization_id": org1["id"]},
        )
    )
    name = f"ac_probe_{tag}"
    path = f"workflows/{name}.py"
    probe = write_and_register(e2e_client, admin, path, PROBE.format(name=name), name, organization_id=org1["id"])
    _ok(e2e_client.patch(f"/api/workflows/{probe['id']}", headers=admin, json={"access_level": "authenticated"}))
    inputs = {"hr_table": hr_table["name"], "admin_table": admin_table["name"], "secret_key": secret_key}
    runs = {
        who: execute_workflow_sync(e2e_client, user.headers, probe["id"], inputs, max_wait=60)
        for who, user in (("hr", hr_user), ("customer", customer_user))
    }
    yield {
        "runs": runs,
        "hr_id": uuid.UUID(hr["id"]),
        "customer_id": uuid.UUID(customer["id"]),
        "hr_table": hr_table["id"],
        "admin_table": admin_table["id"],
        "role": role["name"],
        "secret_key": secret_key,
        "session_factory": async_session_factory,
    }
    e2e_client.delete(f"/api/files/editor?path={path}", headers=admin)
    for table in (hr_table, admin_table):
        e2e_client.delete(f"/api/tables/{table['id']}", headers=admin)
    e2e_client.delete(f"/api/config/{config['id']}", headers=admin)
    for person in (hr, customer):
        e2e_client.delete(f"/api/users/{person['id']}", headers=admin)
    e2e_client.delete(f"/api/roles/{role['id']}", headers=admin)


def _run_checks(world, who: str) -> list[dict]:
    execution = world["runs"][who]
    assert execution["status"] == "Success", execution
    return asyncio.run(_checks(world["session_factory"], execution_id=uuid.UUID(execution["execution_id"])))


def _policy(checks: list[dict], table_id: str) -> list[dict]:
    return [c for c in checks if c["resource_type"] == "policy" and c["details"]["inputs"].get("table") == table_id]


def test_a_role_policy_follows_the_run_users_own_roles(world) -> None:
    """The policy sees the run user's own roles and enforces its decision."""
    hr = _policy(_run_checks(world, "hr"), world["hr_table"])
    customer = _policy(_run_checks(world, "customer"), world["hr_table"])

    assert [(c["outcome"], c["details"]["inputs"]["today"], c["user_id"]) for c in hr] == [
        ("success", True, world["hr_id"])
    ]
    assert hr[0]["details"]["enforced"] is True
    assert [(c["outcome"], c["details"]["inputs"]["today"], c["user_id"]) for c in customer] == [
        ("failure", False, world["customer_id"])
    ]
    assert customer[0]["details"]["enforced"] is True
    assert world["runs"]["hr"]["result"]["first"] == "inserted"
    assert world["runs"]["customer"]["result"]["first"] != "inserted"


def test_a_full_run_keeps_the_default_admin_policy_authority(world) -> None:
    assert world["runs"]["customer"]["result"]["admin_table"] == "inserted"
    # The by-name insert reached the Global table itself, where Full Access
    # satisfies its platform_admin authority policy.
    [check] = _policy(_run_checks(world, "customer"), world["admin_table"])

    assert (check["outcome"], check["details"]["enforced"]) == ("success", True)


def test_a_secret_read_is_recorded_without_its_value(world) -> None:
    secrets = [c for c in _run_checks(world, "customer") if c["resource_type"] == "secret"]

    assert [(c["outcome"], c["details"]["inputs"]["name"]) for c in secrets] == [("success", world["secret_key"])]
    assert secrets[0]["details"]["enforced"] is True
    assert world["runs"]["customer"]["result"]["secret"] is True
    assert "s-" not in str(secrets[0]["details"])


def test_an_autonomous_identity_cannot_enter_an_unassigned_role_based_agent(
    e2e_client, platform_admin, org1
) -> None:
    """A denied autonomous entry fails before it can create an agent run."""
    tag = uuid.uuid4().hex[:8]
    admin = platform_admin.headers
    agent = _ok(
        e2e_client.post(
            "/api/agents",
            headers=admin,
            json={
                "name": f"AC agent {tag}",
                "system_prompt": "Reply briefly.",
                "channels": ["chat"],
                "access_level": "role_based",
                "organization_id": org1["id"],
            },
        ),
    )
    topic = f"ac.agent_{tag}"
    source = _ok(
        e2e_client.post(
            "/api/events/sources",
            headers=admin,
            json={"name": f"AC {tag}", "source_type": "topic", "event_type": topic, "organization_id": org1["id"]},
        ),
    )
    try:
        subscription = _ok(
            e2e_client.post(
                f"/api/events/sources/{source['id']}/subscriptions",
                headers=admin,
                json={"target_type": "agent", "agent_id": agent["id"], "event_type": topic},
            ),
        )
        event = _ok(
            e2e_client.post("/api/events/emit", headers=admin, json={"topic": topic, "data": {}})
        )

        def denied_delivery():
            deliveries = _ok(
                e2e_client.get(f"/api/events/{event['event_id']}/deliveries", headers=admin)
            )["items"]
            denied = [
                delivery
                for delivery in deliveries
                if delivery["event_subscription_id"] == subscription["id"]
                and delivery["status"] == "failed"
            ]
            return denied[0] if denied else None

        delivery = poll_until(denied_delivery, max_wait=30, interval=0.5)
        assert delivery is not None, "role-based agent entry was not denied"
        assert delivery["agent_run_id"] is None
        assert "Agent is no longer available" in delivery["error_message"]
    finally:
        e2e_client.delete(f"/api/events/sources/{source['id']}", headers=admin)
        e2e_client.delete(f"/api/agents/{agent['id']}", headers=admin)


LEGACY_ADMIN_PROBE = '''"""Legacy admin-rule probe."""
from bifrost import tables, workflow


@workflow(name="{name}", description="Legacy admin-rule probe")
async def {name}(table: str):
    result = await tables.query(table)
    return {{"rows": len(result.documents)}}
'''


def test_a_full_access_run_satisfies_a_legacy_admin_rule_that_its_person_does_not(
    e2e_client, platform_admin, org1
) -> None:
    """``user.is_platform_admin`` rules admitted every workflow before Zero
    Trust Automation; a non-admin's Full Access run still reads the table
    while the same person calling directly is refused."""
    tag = uuid.uuid4().hex[:8]
    admin = platform_admin.headers
    person, person_user = _person(e2e_client, admin, org1["id"], tag=tag, key="legacy")
    table = _ok(
        e2e_client.post(
            "/api/tables",
            headers=admin,
            json={
                "name": f"ac_legacy_{tag}",
                "organization_id": None,
                "policies": {
                    "policies": [
                        {
                            "name": "admin_bypass",
                            "actions": ["read", "create", "update", "delete"],
                            "when": {"user": "is_platform_admin"},
                        }
                    ]
                },
            },
        )
    )
    _ok(e2e_client.post(f"/api/tables/{table['id']}/documents", headers=admin, json={"data": {"k": 1}}))
    name = f"ac_legacy_probe_{tag}"
    path = f"workflows/{name}.py"
    probe = write_and_register(
        e2e_client, admin, path, LEGACY_ADMIN_PROBE.format(name=name), name, organization_id=org1["id"]
    )
    try:
        _ok(e2e_client.patch(f"/api/workflows/{probe['id']}", headers=admin, json={"access_level": "authenticated"}))
        run = execute_workflow_sync(
            e2e_client, person_user.headers, probe["id"], {"table": table["name"]}, max_wait=60
        )
        assert run["status"] == "Success", run
        assert run["result"] == {"rows": 1}

        direct = e2e_client.post(
            f"/api/tables/{table['id']}/documents/query", headers=person_user.headers, json={}
        )
        assert direct.status_code == 403 or direct.json()["documents"] == [], direct.text
    finally:
        e2e_client.delete(f"/api/files/editor?path={path}", headers=admin)
        e2e_client.delete(f"/api/tables/{table['id']}", headers=admin)
        e2e_client.delete(f"/api/users/{person['id']}", headers=admin)
