"""E2E: ``run_as`` on ``POST /api/agent-runs/enqueue``.

A Contoso person holding Impersonate Users at Contoso launches a Contoso
agent as a Contoso colleague; a Contoso person without the permission and a
Fabrikam target are refused. Nothing here waits for a run to finish: the
agent's ``max_run_timeout`` is small and only the queued row is read.
"""
from __future__ import annotations

import asyncio
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select

from tests.e2e.conftest import poll_until
from tests.e2e.fixtures.setup import _register_and_authenticate_user
from tests.e2e.fixtures.users import E2EUser


pytestmark = pytest.mark.e2e

USER_ROLE_ID = "00000000-0000-0000-0000-000000000006"
RUN_AS_DENIED = "You don't have permission to run as this user"


def _ok(response, status: int = 200):
    assert response.status_code == status, f"{response.request.url}: {response.status_code} {response.text}"
    return response.json() if response.content else None


def _person(e2e_client, admin: dict, organization: dict, tag: str, label: str, *, additional: list[dict]) -> E2EUser:
    person = E2EUser(
        email=f"agent-run-as-{label}-{tag}@contoso.example",
        password="AgentRunAs123!",
        name=f"Agent Run As {label} {tag}",
        organization_id=UUID(organization["id"]),
    )
    created = _ok(
        e2e_client.post(
            "/api/users",
            headers=admin,
            json={"email": person.email, "name": person.name, "organization_id": organization["id"]},
        ),
        201,
    )
    person = _register_and_authenticate_user(person)
    person.user_id = UUID(created["id"])
    _ok(
        e2e_client.put(
            f"/api/users/{created['id']}/role-assignments",
            headers=admin,
            json={"base_role_id": USER_ROLE_ID, "additional": additional},
        )
    )
    return person


@pytest.fixture(scope="module")
def agent_run_as_world(e2e_client, platform_admin):
    """A Contoso person holding Impersonate Users at Contoso, a Contoso person
    without it, a Contoso colleague, a Fabrikam person, and a Contoso agent."""
    tag = uuid4().hex[:8]
    admin = platform_admin.headers
    contoso = _ok(e2e_client.post("/api/organizations", headers=admin, json={"name": f"Contoso-{tag}"}), 201)
    fabrikam = _ok(e2e_client.post("/api/organizations", headers=admin, json={"name": f"Fabrikam-{tag}"}), 201)
    role = _ok(e2e_client.post("/api/roles", headers=admin, json={"name": f"Contoso Agent Impersonation {tag}"}), 201)
    _ok(
        e2e_client.put(
            f"/api/roles/{role['id']}/permissions",
            headers=admin,
            json={"permissions": ["users.impersonate"]},
        )
    )
    initiator = _person(
        e2e_client,
        admin,
        contoso,
        tag,
        "initiator",
        additional=[{"role_id": role["id"], "boundaries": [{"kind": "organization", "organization_id": contoso["id"]}]}],
    )
    ungranted = _person(e2e_client, admin, contoso, tag, "ungranted", additional=[])
    colleague = _person(e2e_client, admin, contoso, tag, "colleague", additional=[])
    outsider = _person(e2e_client, admin, fabrikam, tag, "outsider", additional=[])
    agent = _ok(
        e2e_client.post(
            "/api/agents",
            headers=admin,
            json={
                "name": f"Contoso Run As Agent {tag}",
                "description": "Agent run_as test",
                "system_prompt": "Return a short acknowledgement.",
                "channels": ["chat"],
                "access_level": "authenticated",
                "organization_id": contoso["id"],
                "max_run_timeout": 5,
            },
        ),
        201,
    )

    yield {
        "initiator": initiator,
        "ungranted": ungranted,
        "colleague": colleague,
        "outsider": outsider,
        "agent": agent,
        "contoso": contoso,
    }

    e2e_client.delete(f"/api/agents/{agent['id']}", headers=admin)
    for person in (initiator, ungranted, colleague, outsider):
        e2e_client.delete(f"/api/users/{person.user_id}", headers=admin)
    e2e_client.delete(f"/api/roles/{role['id']}", headers=admin)
    e2e_client.delete(f"/api/organizations/{contoso['id']}", headers=admin)
    e2e_client.delete(f"/api/organizations/{fabrikam['id']}", headers=admin)


def _enqueue(e2e_client, person: E2EUser, agent: dict, run_as: UUID):
    return e2e_client.post(
        "/api/agent-runs/enqueue",
        headers=person.headers,
        json={"agent_name": agent["name"], "input": {"ticket_id": 42}, "run_as": str(run_as)},
    )


async def _query(session_factory, statement):
    from src.core.database import close_db

    try:
        async with session_factory() as session:
            return (await session.execute(statement)).all()
    finally:
        await close_db()


def _runs_of(session_factory, agent_id: str) -> int:
    from src.models.orm.agent_runs import AgentRun

    rows = asyncio.run(_query(session_factory, select(func.count()).where(AgentRun.agent_id == UUID(agent_id))))
    return rows[0][0]


def _recorded_run_as(session_factory, initiator: UUID, run_as_user: UUID) -> list[dict]:
    from src.models.orm.audit import AuditLog

    statement = select(AuditLog.outcome, AuditLog.organization_id, AuditLog.details).where(
        AuditLog.action == "access.check",
        AuditLog.resource_type == "run_as",
        AuditLog.user_id == initiator,
    )

    def found():
        rows = asyncio.run(_query(session_factory, statement))
        matching = [
            {"outcome": outcome, "organization_id": org_id, "details": details}
            for outcome, org_id, details in rows
            if details["inputs"].get("run_as_user_id") == str(run_as_user)
        ]
        return matching or None

    return poll_until(found, max_wait=30, interval=0.5) or []


def test_a_person_without_impersonate_users_is_refused_and_nothing_is_queued(
    e2e_client, agent_run_as_world, async_session_factory
):
    world = agent_run_as_world
    runs_before = _runs_of(async_session_factory, world["agent"]["id"])

    resp = _enqueue(e2e_client, world["ungranted"], world["agent"], world["colleague"].user_id)

    assert resp.status_code == 403, resp.text
    detail = resp.json()["detail"]
    assert detail == RUN_AS_DENIED
    runs_after = _runs_of(async_session_factory, world["agent"]["id"])
    assert runs_after == runs_before


def test_impersonate_users_runs_an_agent_as_a_user_in_its_organization(
    e2e_client, agent_run_as_world, async_session_factory
):
    world = agent_run_as_world
    initiator, colleague = world["initiator"], world["colleague"]

    accepted = _enqueue(e2e_client, initiator, world["agent"], colleague.user_id)

    receipt = _ok(accepted, 202)
    run = _ok(e2e_client.get(f"/api/agent-runs/{receipt['run_id']}", headers=initiator.headers))
    acting_user = str(colleague.user_id)
    shown = (run["run_as_user_id"], run["run_as_user_name"], run["org_id"], run["caller_user_id"])
    expected = (acting_user, colleague.name, world["contoso"]["id"], str(initiator.user_id))
    assert receipt["run_as_user_id"] == acting_user
    assert shown == expected

    checks = _recorded_run_as(async_session_factory, initiator.user_id, colleague.user_id)
    summary = [(c["outcome"], str(c["organization_id"]), c["details"]["enforced"]) for c in checks]
    assert summary == [("success", world["contoso"]["id"], True)]


def test_impersonate_users_is_refused_for_a_user_outside_its_organization(e2e_client, agent_run_as_world):
    world = agent_run_as_world

    resp = _enqueue(e2e_client, world["initiator"], world["agent"], world["outsider"].user_id)

    assert resp.status_code == 403, resp.text
    detail = resp.json()["detail"]
    assert detail == RUN_AS_DENIED
