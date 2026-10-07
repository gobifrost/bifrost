"""E2E: the identities API.

Identities are the accounts that run work no person started. They are listed,
created (custom ones), renamed and deleted here, and kept out of the people
list; roles go through the role-assignment routes. Reading is ``users.read``
and changing is ``users.lifecycle.readwrite``, at the identity's organization
(Global for the global identities).
"""

from __future__ import annotations

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
PLATFORM_ADMIN_ROLE_ID = "00000000-0000-0000-0000-000000000005"

_SOURCE = '''
from bifrost import workflow


@workflow
def {name}():
    return "ok"
'''


def _ok(response, status: int = 200) -> Any:
    assert response.status_code == status, f"{response.request.url}: {response.status_code} {response.text}"
    return response.json() if response.content else None


def _identities(e2e_client, headers: dict, **params) -> list[dict]:
    return _ok(e2e_client.get("/api/identities", headers=headers, params=params))


def _default(e2e_client, headers: dict, organization_id: str) -> dict:
    (default,) = [
        identity
        for identity in _identities(e2e_client, headers, organization_id=organization_id)
        if identity["identity_kind"] == "org_default"
    ]
    return default


def _create(e2e_client, headers: dict, name: str, organization_id: str | None):
    return e2e_client.post("/api/identities", headers=headers, json={"name": name, "organization_id": organization_id})


def _by_name(identities: list[dict], name: str) -> dict:
    (found,) = [identity for identity in identities if identity["name"] == name]
    return found


def _register_workflow(e2e_client, admin: dict, organization_id: str) -> dict:
    name = f"identity_api_{uuid.uuid4().hex[:8]}"
    return write_and_register(
        e2e_client,
        admin,
        f"workflows/{name}.py",
        _SOURCE.format(name=name),
        name,
        organization_id=organization_id,
    )


def _person(e2e_client, admin: dict, organization_id: str, tag: str, label: str, *, additional: list[dict]) -> E2EUser:
    person = E2EUser(
        email=f"{label}-{tag}@contoso.example",
        password="IdentityApi123!",
        name=f"Identity {label} {tag}",
        organization_id=uuid.UUID(organization_id),
    )
    created = _ok(
        e2e_client.post(
            "/api/users",
            headers=admin,
            json={"email": person.email, "name": person.name, "organization_id": organization_id},
        ),
        201,
    )
    person = _register_and_authenticate_user(person)
    person.user_id = uuid.UUID(created["id"])
    _ok(
        e2e_client.put(
            f"/api/users/{created['id']}/role-assignments",
            headers=admin,
            json={"base_role_id": USER_ROLE_ID, "additional": additional},
        )
    )
    return person


@pytest.fixture(scope="module")
def world(e2e_client, platform_admin):
    tag = uuid.uuid4().hex[:8]
    admin = platform_admin.headers
    contoso = _ok(e2e_client.post("/api/organizations", headers=admin, json={"name": f"Contoso-{tag}"}), 201)
    fabrikam = _ok(e2e_client.post("/api/organizations", headers=admin, json={"name": f"Fabrikam-{tag}"}), 201)

    role = _ok(e2e_client.post("/api/roles", headers=admin, json={"name": f"Identity Lifecycle {tag}"}), 201)
    _ok(
        e2e_client.put(
            f"/api/roles/{role['id']}/permissions",
            headers=admin,
            json={"permissions": ["users.read", "users.lifecycle.readwrite"]},
        )
    )
    lifecycle = _person(
        e2e_client,
        admin,
        contoso["id"],
        tag,
        "lifecycle",
        additional=[{"role_id": role["id"], "boundaries": [{"kind": "organization", "organization_id": contoso["id"]}]}],
    )
    plain = _person(e2e_client, admin, contoso["id"], tag, "plain", additional=[])
    operator = _person(
        e2e_client,
        admin,
        str(PROVIDER_ORG_ID),
        tag,
        "operator",
        additional=[{"role_id": PLATFORM_OPERATOR_ROLE_ID, "boundaries": [{"kind": "managed_organizations"}]}],
    )

    yield {"tag": tag, "contoso": contoso, "fabrikam": fabrikam, "role": role, "lifecycle": lifecycle, "plain": plain, "operator": operator}

    for identity in _identities(e2e_client, admin):
        if identity["identity_kind"] == "custom" and identity["name"].endswith(tag):
            e2e_client.delete(f"/api/identities/{identity['id']}", headers=admin)
    for person in (lifecycle, plain, operator):
        e2e_client.delete(f"/api/users/{person.user_id}", headers=admin)
    e2e_client.delete(f"/api/roles/{role['id']}", headers=admin)
    for organization in (contoso, fabrikam):
        e2e_client.delete(f"/api/organizations/{organization['id']}", headers=admin)


def test_list_has_defaults_global_first(e2e_client, platform_admin, world) -> None:
    contoso = world["contoso"]
    everything = _identities(e2e_client, platform_admin.headers)
    assert everything[0]["identity_kind"] == "global_default"
    assert everything[0]["organization_id"] is None

    default = _default(e2e_client, platform_admin.headers, contoso["id"])
    assert default["identity_kind"] == "org_default"
    assert default["name"] == "Default Identity"
    assert (default["organization_id"], default["organization_name"]) == (contoso["id"], contoso["name"])
    assert default["base_role"]["name"] == "User"
    assert default["additional_roles"] == []
    assert default["workflows_using"] == 0
    assert default in everything


def test_create_custom_in_organization_and_global(e2e_client, platform_admin, world) -> None:
    admin, tag, contoso = platform_admin.headers, world["tag"], world["contoso"]
    in_org = _ok(_create(e2e_client, admin, f"Nightly {tag}", contoso["id"]), 201)
    assert in_org["identity_kind"] == "custom"
    assert (in_org["organization_id"], in_org["organization_name"]) == (contoso["id"], contoso["name"])
    assert in_org["base_role"]["name"] == "User"
    assert (in_org["additional_roles"], in_org["workflows_using"]) == ([], 0)

    global_one = _ok(_create(e2e_client, admin, f"Shared {tag}", None), 201)
    assert (global_one["identity_kind"], global_one["organization_id"], global_one["organization_name"]) == (
        "custom",
        None,
        None,
    )

    listed = _identities(e2e_client, admin)
    assert in_org in listed and global_one in listed
    people = _ok(e2e_client.get("/api/users", headers=admin, params={"include_inactive": True}))
    assert in_org["id"] not in {person["id"] for person in people}


def test_create_refuses_bad_input(e2e_client, platform_admin, world) -> None:
    admin, tag, contoso = platform_admin.headers, world["tag"], world["contoso"]
    assert _create(e2e_client, admin, f"Bad {tag}", str(uuid.uuid4())).status_code == 404
    assert _create(e2e_client, admin, "   ", contoso["id"]).status_code == 422


def _refused(response, detail: str) -> None:
    assert response.status_code == 409, response.text
    assert response.json()["detail"] == detail


def test_names_are_unique_per_organization_ignoring_case(e2e_client, platform_admin, world) -> None:
    admin, tag, contoso, fabrikam = platform_admin.headers, world["tag"], world["contoso"], world["fabrikam"]
    _ok(_create(e2e_client, admin, f"Payroll {tag}", contoso["id"]), 201)

    _refused(
        _create(e2e_client, admin, f"PAYROLL {tag}", contoso["id"]),
        f'An identity named "PAYROLL {tag}" already exists in {contoso["name"]}',
    )
    _ok(_create(e2e_client, admin, f"Payroll {tag}", fabrikam["id"]), 201)

    _ok(_create(e2e_client, admin, f"Shared Payroll {tag}", None), 201)
    _refused(
        _create(e2e_client, admin, f"Shared Payroll {tag}", None),
        f'An identity named "Shared Payroll {tag}" already exists in Global',
    )

    # The default identity holds its name in its organization.
    _refused(
        _create(e2e_client, admin, "Default Identity", contoso["id"]),
        f'An identity named "Default Identity" already exists in {contoso["name"]}',
    )


def test_rename_into_a_taken_name_is_refused(e2e_client, platform_admin, world) -> None:
    admin, tag, contoso = platform_admin.headers, world["tag"], world["contoso"]
    _ok(_create(e2e_client, admin, f"Taken {tag}", contoso["id"]), 201)
    moving = _ok(_create(e2e_client, admin, f"Moving {tag}", contoso["id"]), 201)

    _refused(
        e2e_client.patch(f"/api/identities/{moving['id']}", headers=admin, json={"name": f"taken {tag}"}),
        f'An identity named "taken {tag}" already exists in {contoso["name"]}',
    )
    assert _by_name(_identities(e2e_client, admin, organization_id=contoso["id"]), f"Moving {tag}")["id"] == moving["id"]


def test_rename_custom_but_not_default(e2e_client, platform_admin, world) -> None:
    admin, tag, contoso = platform_admin.headers, world["tag"], world["contoso"]
    default = _default(e2e_client, admin, contoso["id"])
    custom = _ok(_create(e2e_client, admin, f"Before {tag}", contoso["id"]), 201)

    renamed = _ok(e2e_client.patch(f"/api/identities/{custom['id']}", headers=admin, json={"name": f"After {tag}"}))
    assert renamed["name"] == f"After {tag}"

    refused = e2e_client.patch(f"/api/identities/{default['id']}", headers=admin, json={"name": f"Contoso Default {tag}"})
    assert refused.status_code == 409, refused.text
    assert refused.json()["detail"] == "Default identities can't be renamed"
    assert _default(e2e_client, admin, contoso["id"])["name"] == "Default Identity"
    global_default = _identities(e2e_client, admin)[0]
    refused = e2e_client.patch(f"/api/identities/{global_default['id']}", headers=admin, json={"name": f"Shared {tag}"})
    assert refused.status_code == 409, refused.text

    assert e2e_client.patch(f"/api/identities/{uuid.uuid4()}", headers=admin, json={"name": "Nobody"}).status_code == 404


def test_rename_refuses_a_person(e2e_client, platform_admin, world) -> None:
    response = e2e_client.patch(
        f"/api/identities/{world['plain'].user_id}", headers=platform_admin.headers, json={"name": "Renamed"}
    )
    assert response.status_code == 404, response.text


def test_delete_custom_but_not_default(e2e_client, platform_admin, world) -> None:
    admin, tag, contoso = platform_admin.headers, world["tag"], world["contoso"]
    custom = _ok(_create(e2e_client, admin, f"Doomed {tag}", contoso["id"]), 201)
    default = _default(e2e_client, admin, contoso["id"])

    assert e2e_client.delete(f"/api/identities/{custom['id']}", headers=admin).status_code == 204
    assert custom["id"] not in {identity["id"] for identity in _identities(e2e_client, admin)}
    assert e2e_client.delete(f"/api/identities/{custom['id']}", headers=admin).status_code == 404

    refused = e2e_client.delete(f"/api/identities/{default['id']}", headers=admin)
    assert refused.status_code == 409, refused.text
    assert refused.json()["detail"] == "Default identities can't be deleted"
    assert default["id"] in {identity["id"] for identity in _identities(e2e_client, admin)}


def test_workflows_using_counts_and_blocks_delete(e2e_client, platform_admin, world) -> None:
    admin, tag, contoso = platform_admin.headers, world["tag"], world["contoso"]
    default = _default(e2e_client, admin, contoso["id"])
    custom = _ok(_create(e2e_client, admin, f"Busy {tag}", contoso["id"]), 201)
    workflow = _register_workflow(e2e_client, admin, contoso["id"])

    # A workflow that names no identity runs as its organization's default.
    unnamed = _by_name(_identities(e2e_client, admin, organization_id=contoso["id"]), default["name"])
    assert unnamed["workflows_using"] == default["workflows_using"] + 1

    _ok(e2e_client.patch(f"/api/workflows/{workflow['id']}", headers=admin, json={"run_identity_id": custom["id"]}))
    listed = _identities(e2e_client, admin, organization_id=contoso["id"])
    assert _by_name(listed, default["name"])["workflows_using"] == default["workflows_using"]
    assert _by_name(listed, custom["name"])["workflows_using"] == 1

    refused = e2e_client.delete(f"/api/identities/{custom['id']}", headers=admin)
    assert refused.status_code == 409, refused.text
    assert workflow["name"] in refused.json()["detail"]
    assert custom["id"] in {identity["id"] for identity in _identities(e2e_client, admin)}

    _ok(e2e_client.patch(f"/api/workflows/{workflow['id']}", headers=admin, json={"run_identity_id": None}))
    assert e2e_client.delete(f"/api/identities/{custom['id']}", headers=admin).status_code == 204


def test_default_identity_base_role_is_fixed(e2e_client, platform_admin, world) -> None:
    admin, tag, contoso = platform_admin.headers, world["tag"], world["contoso"]
    default = _default(e2e_client, admin, contoso["id"])
    custom = _ok(_create(e2e_client, admin, f"Based {tag}", contoso["id"]), 201)
    base = _ok(e2e_client.post("/api/roles", headers=admin, json={"name": f"Identity Base {tag}"}), 201)
    try:
        refused = e2e_client.put(
            f"/api/users/{default['id']}/role-assignments",
            headers=admin,
            json={"base_role_id": base["id"], "additional": []},
        )
        assert refused.status_code == 409, refused.text
        assert refused.json()["detail"] == "A default identity's base role is fixed"
        assert _default(e2e_client, admin, contoso["id"])["base_role"]["id"] == USER_ROLE_ID

        # What the person page offers matches: a default identity keeps its base role, a custom one may change it.
        def offered_as_base(identity_id: str) -> set[str]:
            view = _ok(e2e_client.get(f"/api/users/{identity_id}/role-assignments", headers=admin))
            return {role["id"] for role in view["assignable_roles"] if role["can_be_base"]}

        assert offered_as_base(default["id"]) == {USER_ROLE_ID}
        assert {USER_ROLE_ID, base["id"]} <= offered_as_base(custom["id"])

        allowed = e2e_client.put(
            f"/api/users/{custom['id']}/role-assignments",
            headers=admin,
            json={"base_role_id": base["id"], "additional": []},
        )
        assert allowed.status_code == 200, allowed.text
        changed = _by_name(_identities(e2e_client, admin, organization_id=contoso["id"]), custom["name"])
        assert changed["base_role"] == {"id": base["id"], "name": base["name"]}

        # Additional roles still go on a default identity, and the list shows them with where they apply.
        placed = e2e_client.put(
            f"/api/users/{default['id']}/role-assignments",
            headers=admin,
            json={
                "base_role_id": USER_ROLE_ID,
                "additional": [
                    {"role_id": world["role"]["id"], "boundaries": [{"kind": "organization", "organization_id": contoso["id"]}]}
                ],
            },
        )
        assert placed.status_code == 200, placed.text
        (held,) = _default(e2e_client, admin, contoso["id"])["additional_roles"]
        assert (held["role_id"], held["name"]) == (world["role"]["id"], world["role"]["name"])
        assert [(b["kind"], b["organization_id"], b["organization_name"]) for b in held["boundaries"]] == [
            ("organization", contoso["id"], contoso["name"])
        ]
    finally:
        e2e_client.put(
            f"/api/users/{default['id']}/role-assignments",
            headers=admin,
            json={"base_role_id": USER_ROLE_ID, "additional": []},
        )
        e2e_client.put(
            f"/api/users/{custom['id']}/role-assignments",
            headers=admin,
            json={"base_role_id": USER_ROLE_ID, "additional": []},
        )
        e2e_client.delete(f"/api/roles/{base['id']}", headers=admin)


def test_a_global_identity_takes_and_loses_platform_admin_like_any_assignment(e2e_client, platform_admin, world) -> None:
    admin = platform_admin.headers
    custom = _ok(_create(e2e_client, admin, f"Admin {world['tag']}", None), 201)
    url = f"/api/users/{custom['id']}/role-assignments"

    offered = _ok(e2e_client.get(url, headers=admin))["assignable_roles"]
    assert PLATFORM_ADMIN_ROLE_ID in {role["id"] for role in offered if role["can_be_additional"]}
    granted = e2e_client.put(
        url, headers=admin, json={"base_role_id": USER_ROLE_ID, "additional": [{"role_id": PLATFORM_ADMIN_ROLE_ID}]}
    )
    assert granted.status_code == 200, granted.text
    assert PLATFORM_ADMIN_ROLE_ID in {role["role_id"] for role in granted.json()["additional"]}

    removed = e2e_client.put(url, headers=admin, json={"base_role_id": USER_ROLE_ID, "additional": []})
    assert removed.status_code == 200, removed.text
    assert removed.json()["additional"] == []


def test_operator_reads_only_what_it_reaches_and_changes_nothing(e2e_client, platform_admin, world) -> None:
    operator, admin = world["operator"].headers, platform_admin.headers
    contoso, fabrikam = world["contoso"], world["fabrikam"]
    custom = _ok(_create(e2e_client, admin, f"Operated {world['tag']}", contoso["id"]), 201)

    seen = _identities(e2e_client, operator)
    organizations = {identity["organization_id"] for identity in seen}
    assert {contoso["id"], fabrikam["id"]} <= organizations
    assert None not in organizations and str(PROVIDER_ORG_ID) not in organizations
    assert custom["id"] in {identity["id"] for identity in seen}

    assert {i["organization_id"] for i in _identities(e2e_client, operator, organization_id=fabrikam["id"])} == {fabrikam["id"]}
    assert e2e_client.get("/api/identities", headers=operator, params={"organization_id": str(PROVIDER_ORG_ID)}).status_code == 403

    assert _create(e2e_client, operator, f"Nope {world['tag']}", contoso["id"]).status_code == 403
    assert e2e_client.patch(f"/api/identities/{custom['id']}", headers=operator, json={"name": "Nope"}).status_code == 403
    assert e2e_client.delete(f"/api/identities/{custom['id']}", headers=operator).status_code == 403


def test_lifecycle_holder_changes_identities_at_their_organization(e2e_client, platform_admin, world) -> None:
    holder, admin = world["lifecycle"].headers, platform_admin.headers
    contoso, fabrikam, tag = world["contoso"], world["fabrikam"], world["tag"]

    assert {i["organization_id"] for i in _identities(e2e_client, holder)} == {contoso["id"]}
    assert e2e_client.get("/api/identities", headers=holder, params={"organization_id": fabrikam["id"]}).status_code == 403

    made = _ok(_create(e2e_client, holder, f"Held {tag}", contoso["id"]), 201)
    assert _create(e2e_client, holder, f"Elsewhere {tag}", fabrikam["id"]).status_code == 403
    assert _create(e2e_client, holder, f"Global {tag}", None).status_code == 403

    renamed = _ok(e2e_client.patch(f"/api/identities/{made['id']}", headers=holder, json={"name": f"Held Again {tag}"}))
    assert renamed["name"] == f"Held Again {tag}"
    elsewhere = _ok(_create(e2e_client, admin, f"Abroad {tag}", fabrikam["id"]), 201)
    assert e2e_client.patch(f"/api/identities/{elsewhere['id']}", headers=holder, json={"name": "Nope"}).status_code == 403
    assert e2e_client.delete(f"/api/identities/{elsewhere['id']}", headers=holder).status_code == 403

    assert e2e_client.delete(f"/api/identities/{made['id']}", headers=holder).status_code == 204


def test_plain_user_is_refused(e2e_client, world) -> None:
    plain = world["plain"].headers
    default = _default(e2e_client, world["lifecycle"].headers, world["contoso"]["id"])

    assert e2e_client.get("/api/identities", headers=plain).status_code == 403
    assert _create(e2e_client, plain, "Nope", world["contoso"]["id"]).status_code == 403
    assert e2e_client.patch(f"/api/identities/{default['id']}", headers=plain, json={"name": "Nope"}).status_code == 403
    assert e2e_client.delete(f"/api/identities/{default['id']}", headers=plain).status_code == 403


def test_user_list_excludes_identities(e2e_client, platform_admin, world) -> None:
    admin = platform_admin.headers
    default = _default(e2e_client, admin, world["contoso"]["id"])
    people = _ok(e2e_client.get("/api/users", headers=admin, params={"scope": world["contoso"]["id"]}))
    assert {person["email"] for person in people} >= {world["plain"].email, world["lifecycle"].email}
    assert all(person["identity_kind"] is None for person in people)
    assert default["id"] not in {person["id"] for person in people}
