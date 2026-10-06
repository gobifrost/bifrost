"""E2E: the workflow Access panel's backend.

A workflow lists the identities it may run as, setting a powerful one takes
the powers to hand out, and Requirements says what the workflow's identity
lacks, from the access checks the workflow's runs recorded.
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

import pytest

from src.core.constants import PROVIDER_ORG_ID
from tests.e2e.conftest import write_and_register
from tests.e2e.fixtures.setup import _register_and_authenticate_user
from tests.e2e.fixtures.users import E2EUser

pytestmark = pytest.mark.e2e

USER_ROLE_ID = "00000000-0000-0000-0000-000000000006"
PLATFORM_OPERATOR_ROLE_ID = "00000000-0000-0000-0000-000000000007"

_SOURCE = '''
from bifrost import workflow


@workflow
def {name}():
    return "ok"
'''


def _ok(response, status: int = 200) -> Any:
    assert response.status_code == status, f"{response.request.url}: {response.status_code} {response.text}"
    return response.json() if response.content else None


def _register(e2e_client, admin: dict, organization_id: str | None) -> dict:
    name = f"access_panel_{uuid.uuid4().hex[:8]}"
    return write_and_register(
        e2e_client,
        admin,
        f"workflows/{name}.py",
        _SOURCE.format(name=name),
        name,
        organization_id=organization_id,
    )


def _run_identities(e2e_client, headers: dict, workflow_id: str) -> list[dict]:
    return _ok(e2e_client.get(f"/api/workflows/{workflow_id}/run-identities", headers=headers))


def _requirements(e2e_client, headers: dict, workflow_id: str, **params) -> dict:
    return _ok(e2e_client.get(f"/api/workflows/{workflow_id}/requirements", headers=headers, params=params))


def _create_identity(e2e_client, admin: dict, name: str, organization_id: str | None) -> dict:
    return _ok(
        e2e_client.post("/api/identities", headers=admin, json={"name": name, "organization_id": organization_id}),
        201,
    )


def _default_identity(e2e_client, admin: dict, organization_id: str) -> dict:
    (default,) = [
        identity
        for identity in _ok(e2e_client.get("/api/identities", headers=admin, params={"organization_id": organization_id}))
        if identity["identity_kind"] == "org_default"
    ]
    return default


def _grant(e2e_client, admin: dict, identity_id: str, additional: list[dict]) -> None:
    _ok(
        e2e_client.put(
            f"/api/users/{identity_id}/role-assignments",
            headers=admin,
            json={"base_role_id": USER_ROLE_ID, "additional": additional},
        )
    )


async def _with_session(session_factory, work) -> None:
    from src.core.database import close_db

    try:
        async with session_factory() as session:
            await work(session)
            await session.commit()
    finally:
        await close_db()


def _record_checks(session_factory, rows: list[dict]) -> None:
    from src.models.orm.audit import AuditLog

    async def add(session) -> None:
        session.add_all(AuditLog(source="http", action="access.check", **row) for row in rows)

    asyncio.run(_with_session(session_factory, add))


def _check(workflow_id: str, kind: str, outcome: str, *, organization_id: str | None, inputs: dict, run: uuid.UUID) -> dict:
    return {
        "resource_type": kind,
        "outcome": outcome,
        "organization_id": uuid.UUID(organization_id) if organization_id else None,
        "execution_id": run,
        "details": {"workflow_id": workflow_id, "inputs": {"target": organization_id, **inputs}},
    }


@pytest.fixture(scope="module")
def world(e2e_client, platform_admin, async_session_factory):
    tag = uuid.uuid4().hex[:8]
    admin = platform_admin.headers
    contoso = _ok(e2e_client.post("/api/organizations", headers=admin, json={"name": f"Contoso-{tag}"}), 201)
    fabrikam = _ok(e2e_client.post("/api/organizations", headers=admin, json={"name": f"Fabrikam-{tag}"}), 201)
    hr = _ok(e2e_client.post("/api/roles", headers=admin, json={"name": f"HR {tag}"}), 201)
    writer_role = _ok(e2e_client.post("/api/roles", headers=admin, json={"name": f"Writer {tag}"}), 201)
    _ok(
        e2e_client.put(
            f"/api/roles/{writer_role['id']}/permissions", headers=admin, json={"permissions": ["users.lifecycle.readwrite"]}
        )
    )
    custom = _create_identity(e2e_client, admin, f"Nightly {tag}", contoso["id"])
    powerful = _create_identity(e2e_client, admin, f"Powerful {tag}", contoso["id"])
    _grant(
        e2e_client,
        admin,
        powerful["id"],
        [{"role_id": writer_role["id"], "boundaries": [{"kind": "organization", "organization_id": contoso["id"]}]}],
    )
    global_custom = _create_identity(e2e_client, admin, f"Shared {tag}", None)

    operator = E2EUser(
        email=f"panel-operator-{tag}@example.com",
        password="PanelOperator123!",
        name="Panel Operator",
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
    _grant(
        e2e_client,
        admin,
        created["id"],
        [{"role_id": PLATFORM_OPERATOR_ROLE_ID, "boundaries": [{"kind": "managed_organizations"}]}],
    )

    async def flag_superuser(session) -> None:
        from src.models.orm.users import User

        user = await session.get(User, uuid.UUID(created["id"]))
        assert user is not None
        user.is_superuser = True

    # The workflow routes still admit only superuser tokens; this person is
    # one without holding Platform Admin, which is what delegation tells apart.
    asyncio.run(_with_session(async_session_factory, flag_superuser))
    operator = _register_and_authenticate_user(operator)

    yield {
        "tag": tag,
        "contoso": contoso,
        "fabrikam": fabrikam,
        "hr": hr,
        "custom": custom,
        "powerful": powerful,
        "global_custom": global_custom,
        "operator": operator,
        "operator_id": created["id"],
    }

    for identity in (custom, powerful, global_custom):
        e2e_client.delete(f"/api/identities/{identity['id']}", headers=admin)
    e2e_client.delete(f"/api/users/{created['id']}", headers=admin)
    for role in (hr, writer_role):
        e2e_client.delete(f"/api/roles/{role['id']}", headers=admin)
    for organization in (contoso, fabrikam):
        e2e_client.delete(f"/api/organizations/{organization['id']}", headers=admin)


def test_organization_workflow_lists_its_organizations_identities(e2e_client, platform_admin, world) -> None:
    admin = platform_admin.headers
    workflow = _register(e2e_client, admin, world["contoso"]["id"])

    listed = _run_identities(e2e_client, admin, workflow["id"])

    default = _default_identity(e2e_client, admin, world["contoso"]["id"])
    assert [i["id"] for i in listed][0] == default["id"]
    assert {i["id"] for i in listed} == {default["id"], world["custom"]["id"], world["powerful"]["id"]}
    assert {i["organization_id"] for i in listed} == {world["contoso"]["id"]}


def test_global_workflow_lists_global_and_provider_identities(e2e_client, platform_admin, world) -> None:
    admin = platform_admin.headers
    workflow = _register(e2e_client, admin, None)

    listed = _run_identities(e2e_client, admin, workflow["id"])

    assert listed[0]["identity_kind"] == "global_default"
    assert world["global_custom"]["id"] in {i["id"] for i in listed}
    assert {i["organization_id"] for i in listed} == {None, str(PROVIDER_ORG_ID)}


def test_workflow_carries_its_effective_permission_mode(e2e_client, platform_admin, world) -> None:
    admin = platform_admin.headers
    workflow = _register(e2e_client, admin, world["contoso"]["id"])

    assert _ok(e2e_client.get(f"/api/workflows/{workflow['id']}", headers=admin))["permission_mode"] == "full"
    patched = _ok(e2e_client.patch(f"/api/workflows/{workflow['id']}", headers=admin, json={"description": "x"}))
    assert patched["permission_mode"] == "full"
    listed = _ok(e2e_client.get("/api/workflows", headers=admin, params={"scope": world["contoso"]["id"]}))
    assert {w["permission_mode"] for w in listed if w["id"] == workflow["id"]} == {"full"}


def test_platform_admin_may_set_any_allowed_identity(e2e_client, platform_admin, world) -> None:
    admin = platform_admin.headers
    workflow = _register(e2e_client, admin, world["contoso"]["id"])

    for identity in (world["powerful"], _default_identity(e2e_client, admin, world["contoso"]["id"])):
        patched = _ok(
            e2e_client.patch(f"/api/workflows/{workflow['id']}", headers=admin, json={"run_identity_id": identity["id"]})
        )
        assert patched["run_identity_id"] == identity["id"]


def test_setting_an_identity_takes_the_powers_it_holds(e2e_client, platform_admin, world) -> None:
    admin, operator = platform_admin.headers, world["operator"].headers
    workflow = _register(e2e_client, admin, world["contoso"]["id"])
    url = f"/api/workflows/{workflow['id']}"

    refused = e2e_client.patch(url, headers=operator, json={"run_identity_id": world["powerful"]["id"]})
    assert refused.status_code == 403, refused.text
    assert world["powerful"]["name"] in refused.json()["detail"]
    assert _ok(e2e_client.get(url, headers=admin))["run_identity_id"] is None

    plain = _ok(e2e_client.patch(url, headers=operator, json={"run_identity_id": world["custom"]["id"]}))
    assert plain["run_identity_id"] == world["custom"]["id"]
    default = _default_identity(e2e_client, admin, world["contoso"]["id"])
    assert _ok(e2e_client.patch(url, headers=operator, json={"run_identity_id": default["id"]}))["run_identity_id"] == default["id"]
    unchanged = e2e_client.patch(url, headers=operator, json={"run_identity_id": default["id"], "description": "y"})
    assert unchanged.status_code == 200, unchanged.text


def test_requirements_are_empty_until_runs_are_observed(e2e_client, platform_admin, world) -> None:
    workflow = _register(e2e_client, platform_admin.headers, world["contoso"]["id"])

    requirements = _requirements(e2e_client, platform_admin.headers, workflow["id"])

    assert requirements["items"] == []
    assert requirements["observed_runs"] == 0
    assert requirements["window_days"] >= 1
    assert requirements["identity_id"] == _default_identity(e2e_client, platform_admin.headers, world["contoso"]["id"])["id"]


def test_requirements_list_what_the_identity_lacks_and_clear_once_granted(
    e2e_client, platform_admin, world, async_session_factory
) -> None:
    admin = platform_admin.headers
    contoso, fabrikam, hr = world["contoso"], world["fabrikam"], world["hr"]
    workflow = _register(e2e_client, admin, contoso["id"])
    _record_checks(
        async_session_factory,
        [
            _check(workflow["id"], "scope_switch", "failure", organization_id=fabrikam["id"], inputs={}, run=uuid.uuid4()),
            _check(
                workflow["id"],
                "policy",
                "failure",
                organization_id=contoso["id"],
                inputs={"missing": ["role:" + hr["name"], "claim:department"]},
                run=uuid.uuid4(),
            ),
        ],
    )
    identity = _default_identity(e2e_client, admin, contoso["id"])

    requirements = _requirements(e2e_client, admin, workflow["id"])

    assert requirements["observed_runs"] == 2
    reach, policy = requirements["items"]
    assert (reach["kind"], reach["label"], reach["grant"]) == ("reach", fabrikam["name"], None)
    assert (policy["kind"], policy["label"]) == ("policy_role", hr["name"])
    assert policy["grant"] == {
        "role_id": hr["id"],
        "boundaries": [{"kind": "organization", "organization_id": contoso["id"]}],
    }

    _grant(
        e2e_client,
        admin,
        identity["id"],
        [
            {
                "role_id": hr["id"],
                "boundaries": [
                    {"kind": "organization", "organization_id": contoso["id"]},
                    {"kind": "organization", "organization_id": fabrikam["id"]},
                ],
            }
        ],
    )

    assert _requirements(e2e_client, admin, workflow["id"])["items"] == []
    _grant(e2e_client, admin, identity["id"], [])


def test_requirements_follow_the_selected_identity(e2e_client, platform_admin, world, async_session_factory) -> None:
    admin = platform_admin.headers
    workflow = _register(e2e_client, admin, world["contoso"]["id"])
    _record_checks(
        async_session_factory,
        [
            _check(
                workflow["id"], "scope_switch", "failure", organization_id=world["fabrikam"]["id"], inputs={}, run=uuid.uuid4()
            )
        ],
    )
    _grant(
        e2e_client,
        admin,
        world["custom"]["id"],
        [
            {
                "role_id": world["hr"]["id"],
                "boundaries": [{"kind": "organization", "organization_id": world["fabrikam"]["id"]}],
            }
        ],
    )

    selected = _requirements(e2e_client, admin, workflow["id"], identity_id=world["custom"]["id"])
    current = _requirements(e2e_client, admin, workflow["id"])

    assert (selected["identity_id"], selected["items"]) == (world["custom"]["id"], [])
    assert [item["kind"] for item in current["items"]] == ["reach"]
    _grant(e2e_client, admin, world["custom"]["id"], [])


def test_requirements_say_when_the_identity_lacks_a_workflow_role(
    e2e_client, platform_admin, world, async_session_factory
) -> None:
    admin = platform_admin.headers
    workflow = _register(e2e_client, admin, world["contoso"]["id"])
    _ok(e2e_client.patch(f"/api/workflows/{workflow['id']}", headers=admin, json={"access_level": "role_based", "role_ids": [world["hr"]["id"]]}))
    _record_checks(
        async_session_factory,
        [
            _check(
                workflow["id"], "scope_switch", "success", organization_id=world["contoso"]["id"], inputs={}, run=uuid.uuid4()
            )
        ],
    )

    (item,) = _requirements(e2e_client, admin, workflow["id"])["items"]

    assert (item["kind"], item["label"], item["grant"]) == ("workflow_role", world["hr"]["name"], None)


def test_requirements_for_a_disallowed_identity_are_refused(e2e_client, platform_admin, world) -> None:
    admin = platform_admin.headers
    workflow = _register(e2e_client, admin, world["contoso"]["id"])
    fabrikam_default = _default_identity(e2e_client, admin, world["fabrikam"]["id"])

    response = e2e_client.get(
        f"/api/workflows/{workflow['id']}/requirements", headers=admin, params={"identity_id": fabrikam_default["id"]}
    )

    assert response.status_code == 422, response.text


def test_unknown_workflow_is_not_found_and_people_are_refused(e2e_client, platform_admin, org1_user, world) -> None:
    missing = uuid.uuid4()
    workflow = _register(e2e_client, platform_admin.headers, world["contoso"]["id"])

    for route in ("run-identities", "requirements"):
        assert e2e_client.get(f"/api/workflows/{missing}/{route}", headers=platform_admin.headers).status_code == 404
        assert e2e_client.get(f"/api/workflows/{workflow['id']}/{route}", headers=org1_user.headers).status_code == 403


def test_a_platform_admin_identity_needs_no_workflow_role(e2e_client, platform_admin, world, async_session_factory) -> None:
    admin = platform_admin.headers
    workflow = _register(e2e_client, admin, None)
    _ok(e2e_client.patch(f"/api/workflows/{workflow['id']}", headers=admin, json={"access_level": "role_based", "role_ids": [world["hr"]["id"]]}))
    _record_checks(
        async_session_factory,
        [_check(workflow["id"], "scope_switch", "success", organization_id=None, inputs={}, run=uuid.uuid4())],
    )
    identities = {i["identity_kind"] + str(i["organization_id"]): i for i in _run_identities(e2e_client, admin, workflow["id"])}
    global_default = identities["global_defaultNone"]
    provider_default = identities[f"org_default{PROVIDER_ORG_ID}"]

    lacking = _requirements(e2e_client, admin, workflow["id"], identity_id=global_default["id"])["items"]
    admin_identity = _requirements(e2e_client, admin, workflow["id"], identity_id=provider_default["id"])["items"]

    assert [item["kind"] for item in lacking] == ["workflow_role"]
    assert admin_identity == []


def test_the_base_role_does_not_count_as_a_held_policy_role(e2e_client, platform_admin, world, async_session_factory) -> None:
    admin = platform_admin.headers
    workflow = _register(e2e_client, admin, world["contoso"]["id"])
    _record_checks(
        async_session_factory,
        [
            _check(
                workflow["id"],
                "policy",
                "failure",
                organization_id=world["contoso"]["id"],
                inputs={"missing": ["role:User"]},
                run=uuid.uuid4(),
            )
        ],
    )

    (item,) = _requirements(e2e_client, admin, workflow["id"])["items"]

    assert (item["kind"], item["label"], item["grant"]) == ("policy_role", "User", None)
