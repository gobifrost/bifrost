"""Explaining stored access checks, and what-if access checks.

``GET /api/audit/{event_id}/explain`` returns a stored ``access.check`` as
decided then and judged again now (the run user's current roles and the
workflow's current powers). ``POST /api/users/{user_id}/access/check`` asks
the same model about a user who has not tried anything yet. Platform Admins
explain everything; Platform Operators explain and ask only inside their reach.
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

import pytest

from tests.e2e.conftest import write_and_register
from tests.e2e.fixtures.setup import PROVIDER_ORG_ID, _register_and_authenticate_user
from tests.e2e.fixtures.users import E2EUser

pytestmark = pytest.mark.e2e

PLATFORM_OPERATOR_ROLE_ID = "00000000-0000-0000-0000-000000000007"
USER_ROLE_ID = "00000000-0000-0000-0000-000000000006"
# PATCH /api/tables/{table_id}: tables.readwrite, organization boundary.
OPERATION = "PATCH /api/tables/{table_id}"
CATALOG_ID = "tables.update"


def _ok(response, status: int = 200) -> Any:
    assert response.status_code == status, f"{response.request.url}: {response.status_code} {response.text}"
    return response.json() if response.content else None


async def _seed(session_factory, rows: list[dict]) -> list[uuid.UUID]:
    from src.core.database import close_db
    from src.models.orm.audit import AuditLog

    try:
        async with session_factory() as session:
            logs = [AuditLog(source="http", **row) for row in rows]
            session.add_all(logs)
            await session.commit()
            return [log.id for log in logs]
    finally:
        await close_db()


async def _delete(session_factory, ids: list[uuid.UUID]) -> None:
    from sqlalchemy import delete

    from src.core.database import close_db
    from src.models.orm.audit import AuditLog

    try:
        async with session_factory() as session:
            await session.execute(delete(AuditLog).where(AuditLog.id.in_(ids)))
            await session.commit()
    finally:
        await close_db()


def _scope_switch(user_id: uuid.UUID | None, home: str, target: str | None) -> dict:
    """An ``access.check`` row as the writer stores a scope switch the model
    refuses: the target is outside the run user's reach."""
    trace = {
        "outcome": "failure",
        "enforced": False,
        "steps": [
            {
                "key": "run_user",
                "label": "Run user",
                "status": "passed",
                "reason": "person",
                "facts": {
                    "user_id": str(user_id),
                    "identity_kind": None,
                    "home_organization_id": home,
                    "is_platform_admin": False,
                },
            },
            {"key": "powers", "label": "Workflow powers", "status": "passed", "reason": "full", "facts": {"grants": []}},
            {"key": "target", "label": "Target in reach", "status": "stopped", "reason": "outside", "facts": {"organization_id": target}},
            {"key": "permission", "label": "Permission", "status": "not_reached", "reason": "", "facts": {}},
        ],
    }
    return {
        "action": "access.check",
        "resource_type": "scope_switch",
        "outcome": "failure",
        "user_id": user_id,
        "organization_id": None if target is None else uuid.UUID(target),
        "operation_id": OPERATION,
        "details": {
            "enforced": False,
            "workflow_id": None,
            "trace": trace,
            "inputs": {"operation": OPERATION, "target": target},
            "today": "allowed",
        },
    }


def _person(e2e_client, admin: dict, organization_id: str, name: str) -> dict:
    return _ok(
        e2e_client.post(
            "/api/users",
            headers=admin,
            json={"email": f"{name.lower().replace(' ', '-')}@contoso.example", "name": name, "organization_id": organization_id},
        ),
        201,
    )


@pytest.fixture(scope="module")
def world(e2e_client, platform_admin, async_session_factory):
    tag = uuid.uuid4().hex[:8]
    admin = platform_admin.headers
    contoso = _ok(e2e_client.post("/api/organizations", headers=admin, json={"name": f"Contoso-{tag}"}), 201)
    fabrikam = _ok(e2e_client.post("/api/organizations", headers=admin, json={"name": f"Fabrikam-{tag}"}), 201)
    person = _person(e2e_client, admin, contoso["id"], f"Explain Person {tag}")
    promoted = _person(e2e_client, admin, contoso["id"], f"Explain Promoted {tag}")
    operator = E2EUser(
        email=f"explain-operator-{tag}@example.com",
        password="ExplainOperator123!",
        name="Explain Operator",
        organization_id=PROVIDER_ORG_ID,
    )
    created = _ok(
        e2e_client.post(
            "/api/users",
            headers=admin,
            json={"email": operator.email, "name": operator.name, "organization_id": str(PROVIDER_ORG_ID)},
        ),
        201,
    )
    operator = _register_and_authenticate_user(operator)
    _ok(
        e2e_client.put(
            f"/api/users/{created['id']}/role-assignments",
            headers=admin,
            json={
                "base_role_id": USER_ROLE_ID,
                "additional": [{"role_id": PLATFORM_OPERATOR_ROLE_ID, "boundaries": [{"kind": "managed_organizations"}]}],
            },
        )
    )
    person_id, promoted_id = uuid.UUID(person["id"]), uuid.UUID(promoted["id"])
    other = {**_scope_switch(person_id, contoso["id"], fabrikam["id"]), "action": "user.update"}
    ids = asyncio.run(
        _seed(
            async_session_factory,
            [
                _scope_switch(person_id, contoso["id"], fabrikam["id"]),
                _scope_switch(promoted_id, contoso["id"], fabrikam["id"]),
                _scope_switch(person_id, contoso["id"], str(PROVIDER_ORG_ID)),
                _scope_switch(person_id, contoso["id"], None),
                other,
                # A deleted run user leaves user_id NULL (the foreign key sets it).
                _scope_switch(None, contoso["id"], fabrikam["id"]),
            ],
        )
    )
    events = dict(zip(("fabrikam", "promoted", "provider", "global", "other", "orphaned"), map(str, ids), strict=True))
    yield {
        "contoso": contoso,
        "fabrikam": fabrikam,
        "person": person,
        "promoted": promoted,
        "operator": operator,
        "events": events,
        "tag": tag,
    }
    asyncio.run(_delete(async_session_factory, ids))
    for user_id in (created["id"], person["id"], promoted["id"]):
        e2e_client.delete(f"/api/users/{user_id}", headers=admin)
    for organization in (contoso, fabrikam):
        e2e_client.delete(f"/api/organizations/{organization['id']}", headers=admin)


def _explain(e2e_client, headers: dict, event_id: str):
    return e2e_client.get(f"/api/audit/{event_id}/explain", headers=headers)


def _check(e2e_client, headers: dict, user_id: str, **body):
    return e2e_client.post(f"/api/users/{user_id}/access/check", headers=headers, json=body)


def _steps(trace: dict) -> dict[str, dict]:
    return {step["key"]: step for step in trace["steps"]}


def test_admin_explains_then_and_now(e2e_client, platform_admin, world) -> None:
    event_id = world["events"]["fabrikam"]
    body = _ok(_explain(e2e_client, platform_admin.headers, event_id))

    assert body["event"]["id"] == event_id
    assert (body["then"]["outcome"], body["now"]["outcome"], body["changed"]) == ("failure", "failure", False)
    assert _steps(body["now"])["target"]["status"] == "stopped"
    assert body["now_unavailable"] is None


def test_role_change_flips_now(e2e_client, platform_admin, world) -> None:
    """A role placed on Fabrikam brings it into the run user's reach; Full powers hold the permission."""
    admin = platform_admin.headers
    role = _ok(e2e_client.post("/api/roles", headers=admin, json={"name": f"Explain Tables {world['tag']}"}), 201)
    try:
        _ok(
            e2e_client.put(
                f"/api/users/{world['promoted']['id']}/role-assignments",
                headers=admin,
                json={
                    "base_role_id": USER_ROLE_ID,
                    "additional": [
                        {"role_id": role["id"], "boundaries": [{"kind": "organization", "organization_id": world["fabrikam"]["id"]}]}
                    ],
                },
            )
        )

        body = _ok(_explain(e2e_client, admin, world["events"]["promoted"]))
    finally:
        e2e_client.delete(f"/api/roles/{role['id']}", headers=admin)

    assert (body["then"]["outcome"], body["now"]["outcome"], body["changed"]) == ("failure", "success", True)
    assert _steps(body["then"])["target"]["status"] == "stopped"
    assert _steps(body["now"])["target"]["reason"] == f"role:{role['id']}@organization"


def test_operator_reads_in_reach_only(e2e_client, platform_admin, world) -> None:
    operator, events = world["operator"].headers, world["events"]

    assert _explain(e2e_client, operator, events["fabrikam"]).status_code == 200
    for outside in ("provider", "global", "other"):
        assert _explain(e2e_client, operator, events[outside]).status_code == 403, outside
    assert _explain(e2e_client, platform_admin.headers, events["other"]).status_code == 422


def test_missing_event_says_archived(e2e_client, platform_admin) -> None:
    response = _explain(e2e_client, platform_admin.headers, str(uuid.uuid4()))

    assert response.status_code == 404
    assert "archived" in response.json()["detail"]


def test_run_user_deleted_now_unavailable(e2e_client, platform_admin, world) -> None:
    body = _ok(_explain(e2e_client, platform_admin.headers, world["events"]["orphaned"]))

    assert (body["now"], body["now_unavailable"], body["changed"]) == (None, "run_user_missing", None)
    assert body["then"]["outcome"] == "failure"


def test_access_check_what_if(e2e_client, platform_admin, world) -> None:
    admin, operator = platform_admin.headers, world["operator"].headers
    person, fabrikam = world["person"]["id"], world["fabrikam"]["id"]

    refused = _ok(_check(e2e_client, admin, person, organization_id=fabrikam, operation=CATALOG_ID))
    assert refused["outcome"] == "failure"
    assert [step["key"] for step in refused["steps"] if step["status"] == "stopped"] == ["target"]

    at_global = _ok(_check(e2e_client, admin, person, organization_id="global", operation=OPERATION))
    assert _steps(at_global)["target"]["status"] == "passed"

    assert _check(e2e_client, admin, person, organization_id="global", operation="GET /api/nowhere").status_code == 422
    provider_subject = str(platform_admin.user_id)
    assert _check(e2e_client, operator, provider_subject, organization_id=fabrikam, operation=CATALOG_ID).status_code == 403
    assert (
        _check(e2e_client, operator, person, organization_id=str(PROVIDER_ORG_ID), operation=CATALOG_ID).status_code
        == 403
    )
    assert _check(e2e_client, operator, person, organization_id="global", operation=CATALOG_ID).status_code == 403
    assert _check(e2e_client, operator, person, organization_id=fabrikam, operation=CATALOG_ID).status_code == 200


def test_what_if_with_workflow_uses_powers(e2e_client, platform_admin, world) -> None:
    admin = platform_admin.headers
    name = f"explain_probe_{world['tag']}"
    path = f"workflows/{name}.py"
    source = f'from bifrost import workflow\n\n\n@workflow\ndef {name}():\n    return "ok"\n'
    workflow = write_and_register(e2e_client, admin, path, source, name, organization_id=world["contoso"]["id"])
    try:
        _ok(e2e_client.patch(f"/api/workflows/{workflow['id']}", headers=admin, json={"access_level": "authenticated"}))

        body = _ok(
            _check(
                e2e_client,
                admin,
                world["person"]["id"],
                organization_id=world["contoso"]["id"],
                operation=CATALOG_ID,
                workflow_id=workflow["id"],
            )
        )

        assert body["steps"][0]["key"] == "workflow_access"
        assert (_steps(body)["powers"]["status"], _steps(body)["powers"]["reason"]) == ("passed", "full")
    finally:
        e2e_client.delete(f"/api/files/editor?path={path}", headers=admin)
