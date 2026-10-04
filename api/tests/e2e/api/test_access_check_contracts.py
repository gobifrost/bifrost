"""Report-only access checks for the decisions the identity matrix leaves out.

Table policies and secrets are judged against the run's user (their own
roles, plus what a Full workflow adds); an agent run nobody started opens its
agent as its organization's identity. Each check lands in the audit log as
``access.check`` with ``enforced: false``; nothing about the run changes.
"""

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


@pytest.fixture(scope="module")
def world(e2e_client, platform_admin, org1, org1_user, async_session_factory):
    tag = uuid.uuid4().hex[:8]
    admin = platform_admin.headers
    role = _ok(e2e_client.post("/api/roles", headers=admin, json={"name": f"AC HR {tag}"}))
    hr = _ok(
        e2e_client.post(
            "/api/users",
            headers=admin,
            json={"email": f"ac-hr-{tag}@contoso.example", "name": "AC HR", "organization_id": org1["id"]},
        )
    )
    from tests.e2e.fixtures.setup import _register_and_authenticate_user
    from tests.e2e.fixtures.users import E2EUser

    hr_user = _register_and_authenticate_user(
        E2EUser(email=hr["email"], password=f"Ac-{tag}-Pass1!", name="AC HR", organization_id=uuid.UUID(org1["id"])),
        skip_registration=False,
    )
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
                    "policies": [{"name": "admins", "actions": ["create", "read"], "when": {"user": "is_platform_admin"}}]
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
        for who, user in (("hr", hr_user), ("customer", org1_user))
    }
    yield {
        "runs": runs,
        "hr_id": uuid.UUID(hr["id"]),
        "customer_id": org1_user.user_id,
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
    e2e_client.delete(f"/api/users/{hr['id']}", headers=admin)
    e2e_client.delete(f"/api/roles/{role['id']}", headers=admin)


def _run_checks(world, who: str) -> list[dict]:
    execution = world["runs"][who]
    assert execution["status"] == "Success", execution
    return asyncio.run(_checks(world["session_factory"], execution_id=uuid.UUID(execution["execution_id"])))


def _policy(checks: list[dict], table_id: str) -> list[dict]:
    return [c for c in checks if c["resource_type"] == "policy" and c["details"]["inputs"].get("table") == table_id]


def test_a_role_policy_follows_the_run_users_own_roles(world) -> None:
    """Today the run is judged as the execution credential (no roles); the model as the person."""
    hr = _policy(_run_checks(world, "hr"), world["hr_table"])
    customer = _policy(_run_checks(world, "customer"), world["hr_table"])

    assert [(c["outcome"], c["details"]["today"], c["user_id"]) for c in hr] == [("success", "denied", world["hr_id"])]
    assert [(c["outcome"], c["details"]["inputs"]["missing"]) for c in customer] == [
        ("failure", [f"role:{world['role']}"])
    ]


def test_a_full_run_passes_an_admin_only_global_table(world) -> None:
    [check] = _policy(_run_checks(world, "customer"), world["admin_table"])

    assert (check["outcome"], check["details"]["enforced"]) == ("success", False)


def test_a_secret_read_is_recorded_without_its_value(world) -> None:
    secrets = [c for c in _run_checks(world, "customer") if c["resource_type"] == "secret"]

    assert [(c["outcome"], c["details"]["inputs"]["name"]) for c in secrets] == [("success", world["secret_key"])]
    assert "s-" not in str(secrets[0]["details"])


def test_an_agent_nobody_started_opens_its_agent_as_its_identity(
    e2e_client, platform_admin, org1, async_session_factory
) -> None:
    """A role-based agent its org's identity holds no role for: the model would not start it."""
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
        _ok(
            e2e_client.post(
                f"/api/events/sources/{source['id']}/subscriptions",
                headers=admin,
                json={"target_type": "agent", "agent_id": agent["id"], "event_type": topic},
            ),
        )
        _ok(e2e_client.post("/api/events/emit", headers=admin, json={"topic": topic, "data": {}}))

        def entry_checks():
            rows = asyncio.run(_checks(async_session_factory, resource_type="entry"))
            found = [r for r in rows if r["details"]["inputs"].get("subject") == f"agent:{agent['id']}"]
            return found or None

        found = poll_until(entry_checks, max_wait=30, interval=0.5)
        assert found is not None, "no entry check recorded for the event-started agent run"
        assert [(r["outcome"], r["details"]["enforced"]) for r in found] == [("failure", False)]
    finally:
        e2e_client.delete(f"/api/events/sources/{source['id']}", headers=admin)
        e2e_client.delete(f"/api/agents/{agent['id']}", headers=admin)
