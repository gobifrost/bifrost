"""E2E coverage for app-facing /api/files policy gates."""

from __future__ import annotations

import uuid
from collections import defaultdict
from typing import Any
from uuid import UUID

import pytest


ACTIONS = ("read", "write", "delete", "list")


def _body(path: str, *, location: str, scope: str | None, content: str = "payload") -> dict[str, Any]:
    return {
        "path": path,
        "content": content,
        "location": location,
        "scope": scope,
        "mode": "cloud",
    }


def _read_body(path: str, *, location: str, scope: str | None) -> dict[str, Any]:
    return {"path": path, "location": location, "scope": scope, "mode": "cloud"}


def _list_body(directory: str, *, location: str, scope: str | None) -> dict[str, Any]:
    return {"directory": directory, "location": location, "scope": scope, "mode": "cloud"}


def _sign_body(
    path: str,
    *,
    location: str,
    scope: str | None,
    method: str,
) -> dict[str, Any]:
    return {
        "path": path,
        "location": location,
        "scope": scope,
        "method": method,
        "content_type": "text/plain",
    }


async def _replace_file_policies(db_session, policies: list[dict[str, Any]]) -> None:
    from sqlalchemy import delete

    from src.models.contracts.policies import FilePolicies
    from src.models.orm.file_metadata import FilePolicy
    from src.services.file_policy_service import FilePolicyService

    service = FilePolicyService(db_session)
    await db_session.execute(delete(FilePolicy))
    grouped: dict[tuple[str | None, str, str], list[dict[str, Any]]] = defaultdict(list)
    for item in policies:
        grouped[(item["scope"], item["location"], item["prefix"])].append(
            {
                key: value
                for key, value in item.items()
                if key not in {"scope", "location", "prefix"}
            }
        )
    for (scope, location, prefix), rules in grouped.items():
        await service.upsert_policy(
            organization_id=UUID(scope) if scope is not None else None,
            location=location,
            path=prefix,
            policies=FilePolicies.model_validate({"policies": rules}),
        )
    await db_session.commit()


def _allow_all_when() -> None:
    return None


def _has_role_when(role_id: str) -> dict[str, Any]:
    return {"call": "has_role", "args": [role_id]}


def _creator_when() -> dict[str, Any]:
    return {"eq": [{"file": "created_by"}, {"user": "user_id"}]}


def _policy(
    name: str,
    *,
    location: str,
    scope: str | None,
    prefix: str,
    actions: list[str],
    when: Any,
) -> dict[str, Any]:
    return {
        "name": name,
        "location": location,
        "scope": scope,
        "prefix": prefix,
        "actions": actions,
        **({"when": when} if when is not None else {}),
    }


@pytest.fixture
def file_policy_role(e2e_client, platform_admin):
    name = f"file-policy-role-{uuid.uuid4().hex[:8]}"
    create = e2e_client.post(
        "/api/roles",
        headers=platform_admin.headers,
        json={"name": name, "description": "file policy e2e role"},
    )
    assert create.status_code == 201, create.text
    role = create.json()
    yield role


def _assign_role(e2e_client, platform_admin, role_id: str, *users) -> None:
    resp = e2e_client.post(
        f"/api/roles/{role_id}/users",
        headers=platform_admin.headers,
        json={"user_ids": [str(user.user_id) for user in users]},
    )
    assert resp.status_code == 204, resp.text


@pytest.mark.e2e
# NOTE: `workspace` is intentionally NOT in this list. It is the shared
# platform codebase: superuser-only and never policy-governed (default-deny
# does not apply). Its contract lives in test_workspace_superuser_only.py.
@pytest.mark.parametrize(
    ("location", "scope_kind"),
    [
        ("temp", "org"),
        ("uploads", "org"),
        ("shared", "org"),
    ],
)
def test_files_default_deny_by_location_without_policies(
    e2e_client,
    platform_admin,
    org1_user,
    org1,
    location: str,
    scope_kind: str,
):
    scope = None if scope_kind == "workspace" else org1["id"]
    path = f"default-deny/{uuid.uuid4().hex}.txt"

    denied_read = e2e_client.post(
        "/api/files/read",
        headers=org1_user.headers,
        json=_read_body(path, location=location, scope=scope),
    )
    assert denied_read.status_code == 403

    denied_write = e2e_client.post(
        "/api/files/write",
        headers=org1_user.headers,
        json=_body(path, location=location, scope=scope, content="blocked"),
    )
    assert denied_write.status_code == 403

    denied_delete = e2e_client.post(
        "/api/files/delete",
        headers=org1_user.headers,
        json=_read_body(path, location=location, scope=scope),
    )
    assert denied_delete.status_code == 403

    denied_list = e2e_client.post(
        "/api/files/list",
        headers=org1_user.headers,
        json=_list_body("default-deny", location=location, scope=scope),
    )
    assert denied_list.status_code == 403

    exists = e2e_client.post(
        "/api/files/exists",
        headers=org1_user.headers,
        json=_read_body(path, location=location, scope=scope),
    )
    assert exists.status_code == 200
    assert exists.json()["exists"] is False

    signed_get = e2e_client.post(
        "/api/files/signed-url",
        headers=org1_user.headers,
        json=_sign_body(path, location=location, scope=scope, method="GET"),
    )
    assert signed_get.status_code == 403

    signed_put = e2e_client.post(
        "/api/files/signed-url",
        headers=org1_user.headers,
        json=_sign_body(path, location=location, scope=scope, method="PUT"),
    )
    assert signed_put.status_code == 403

    admin_read_without_policy = e2e_client.post(
        "/api/files/read",
        headers=platform_admin.headers,
        json=_read_body(path, location=location, scope=scope),
    )
    assert admin_read_without_policy.status_code == 403


def _policy_deny_rows(e2e_client, platform_admin, user, *, resource_type: str = "file") -> list[dict[str, Any]]:
    audit = e2e_client.get(
        "/api/audit",
        headers=platform_admin.headers,
        params={
            "action": "policy.deny",
            "resource_type": resource_type,
            "user_id": str(user.user_id),
            "limit": 50,
        },
    )
    assert audit.status_code == 200, audit.text
    return audit.json()["entries"]


@pytest.mark.e2e
def test_denied_read_emits_policy_deny_audit_row(
    e2e_client,
    platform_admin,
    org1_user,
    org1,
):
    """READ denials go through `_authorize_file_policy` directly (not
    `_require_file_policy`), so `/api/files/read` used to 403 with no audit
    row. It must now emit exactly one `policy.deny` row per denial, same
    shape as the write path."""
    location, scope = "uploads", org1["id"]
    path = f"read-deny-audit/{uuid.uuid4().hex}.txt"

    before = len(_policy_deny_rows(e2e_client, platform_admin, org1_user))

    denied_read = e2e_client.post(
        "/api/files/read",
        headers=org1_user.headers,
        json=_read_body(path, location=location, scope=scope),
    )
    assert denied_read.status_code == 403

    rows = _policy_deny_rows(e2e_client, platform_admin, org1_user)
    assert len(rows) == before + 1, f"expected exactly one new policy.deny row for the denied read: {rows}"
    entry = rows[0]
    assert entry["action"] == "policy.deny"
    assert entry["resource_type"] == "file"
    assert entry["outcome"] == "failure"
    assert entry["details"]["policy_action"] == "read"
    assert entry["details"]["location"] == location
    assert entry["details"]["path"] == path


@pytest.mark.e2e
def test_denied_write_emits_exactly_one_policy_deny_audit_row(
    e2e_client,
    platform_admin,
    org1_user,
    org1,
):
    """Denied writes already emitted via `_require_file_policy`; guard against
    a regression where refactoring the emit choke point causes a double-emit
    (e.g. if both `_authorize_file_policy` and `_require_file_policy` emit)."""
    location, scope = "uploads", org1["id"]
    path = f"write-deny-audit/{uuid.uuid4().hex}.txt"

    before = len(_policy_deny_rows(e2e_client, platform_admin, org1_user))

    denied_write = e2e_client.post(
        "/api/files/write",
        headers=org1_user.headers,
        json=_body(path, location=location, scope=scope, content="blocked"),
    )
    assert denied_write.status_code == 403

    rows = _policy_deny_rows(e2e_client, platform_admin, org1_user)
    assert len(rows) == before + 1, f"expected exactly one new policy.deny row for the denied write, got double-emit or none: {rows}"
    entry = rows[0]
    assert entry["action"] == "policy.deny"
    assert entry["resource_type"] == "file"
    assert entry["outcome"] == "failure"
    assert entry["details"]["policy_action"] == "write"
    assert entry["details"]["location"] == location
    assert entry["details"]["path"] == path


@pytest.mark.e2e
async def test_everyone_read_role_write_creator_cross_org_and_nested_prefix_matrix(
    e2e_client,
    platform_admin,
    org1_user,
    alice_user,
    org2_user,
    org1,
    org2,
    file_policy_role,
    db_session,
):
    role_id = file_policy_role["id"]
    _assign_role(e2e_client, platform_admin, role_id, org1_user, org2_user)

    root = f"policy-matrix/{uuid.uuid4().hex}/"
    role_path = f"{root}role-write.txt"
    creator_path = f"{root}creator/only.txt"
    nested_plain_path = f"{root}nested/plain-denied.txt"
    nested_role_path = f"{root}nested/role-allowed.txt"

    await _replace_file_policies(
        db_session,
        [
            _policy(
                "admin-bypass",
                location="temp",
                scope=org1["id"],
                prefix=root,
                actions=["read", "write", "delete", "list"],
                when={"user": "is_platform_admin"},
            ),
            _policy(
                "everyone-read",
                location="temp",
                scope=org1["id"],
                prefix=root,
                actions=["read", "list"],
                when=_allow_all_when(),
            ),
            _policy(
                "role-write",
                location="temp",
                scope=org1["id"],
                prefix=root,
                actions=["write", "delete"],
                when=_has_role_when(role_id),
            ),
            _policy(
                "creator-write-seed",
                location="temp",
                scope=org1["id"],
                prefix=f"{root}creator",
                actions=["write"],
                when=_allow_all_when(),
            ),
            _policy(
                "creator-only",
                location="temp",
                scope=org1["id"],
                prefix=f"{root}creator",
                actions=["read", "delete", "list"],
                when=_creator_when(),
            ),
            _policy(
                "nested-role-override",
                location="temp",
                scope=org1["id"],
                prefix=f"{root}nested/",
                actions=["write", "delete"],
                when=_has_role_when(role_id),
            ),
        ],
    )

    admin_seed = e2e_client.post(
        "/api/files/write",
        headers=platform_admin.headers,
        json=_body(role_path, location="temp", scope=org1["id"], content="seed"),
    )
    assert admin_seed.status_code == 204, admin_seed.text

    read = e2e_client.post(
        "/api/files/read",
        headers=alice_user.headers,
        json=_read_body(role_path, location="temp", scope=org1["id"]),
    )
    assert read.status_code == 200
    assert read.json()["content"] == "seed"

    listed = e2e_client.post(
        "/api/files/list",
        headers=alice_user.headers,
        json=_list_body(root, location="temp", scope=org1["id"]),
    )
    assert listed.status_code == 200

    plain_write = e2e_client.post(
        "/api/files/write",
        headers=alice_user.headers,
        json=_body(role_path, location="temp", scope=org1["id"], content="nope"),
    )
    assert plain_write.status_code == 403

    role_write = e2e_client.post(
        "/api/files/write",
        headers=org1_user.headers,
        json=_body(role_path, location="temp", scope=org1["id"], content="role update"),
    )
    assert role_write.status_code == 204, role_write.text

    org2_cross_org = e2e_client.post(
        "/api/files/read",
        headers=org2_user.headers,
        json=_read_body(role_path, location="temp", scope=org1["id"]),
    )
    assert org2_cross_org.status_code == 403

    org2_own_scope_missing_policy = e2e_client.post(
        "/api/files/read",
        headers=org2_user.headers,
        json=_read_body(role_path, location="temp", scope=org2["id"]),
    )
    assert org2_own_scope_missing_policy.status_code == 403

    creator_write = e2e_client.post(
        "/api/files/write",
        headers=alice_user.headers,
        json=_body(creator_path, location="temp", scope=org1["id"], content="alice owns this"),
    )
    assert creator_write.status_code == 204, creator_write.text

    creator_read = e2e_client.post(
        "/api/files/read",
        headers=alice_user.headers,
        json=_read_body(creator_path, location="temp", scope=org1["id"]),
    )
    assert creator_read.status_code == 200

    non_creator_read = e2e_client.post(
        "/api/files/read",
        headers=org1_user.headers,
        json=_read_body(creator_path, location="temp", scope=org1["id"]),
    )
    assert non_creator_read.status_code == 403

    nested_plain = e2e_client.post(
        "/api/files/write",
        headers=alice_user.headers,
        json=_body(nested_plain_path, location="temp", scope=org1["id"], content="blocked"),
    )
    assert nested_plain.status_code == 403

    nested_role = e2e_client.post(
        "/api/files/write",
        headers=org1_user.headers,
        json=_body(nested_role_path, location="temp", scope=org1["id"], content="ok"),
    )
    assert nested_role.status_code == 204, nested_role.text

    signed_get = e2e_client.post(
        "/api/files/signed-url",
        headers=alice_user.headers,
        json=_sign_body(role_path, location="temp", scope=org1["id"], method="GET"),
    )
    assert signed_get.status_code == 200

    signed_put_denied = e2e_client.post(
        "/api/files/signed-url",
        headers=alice_user.headers,
        json=_sign_body(role_path, location="temp", scope=org1["id"], method="PUT"),
    )
    assert signed_put_denied.status_code == 403

    signed_put_allowed = e2e_client.post(
        "/api/files/signed-url",
        headers=org1_user.headers,
        json=_sign_body(role_path, location="temp", scope=org1["id"], method="PUT"),
    )
    assert signed_put_allowed.status_code == 200

    exists_allowed = e2e_client.post(
        "/api/files/exists",
        headers=alice_user.headers,
        json=_read_body(role_path, location="temp", scope=org1["id"]),
    )
    assert exists_allowed.status_code == 200
    assert exists_allowed.json()["exists"] is True

    exists_denied = e2e_client.post(
        "/api/files/exists",
        headers=org2_user.headers,
        json=_read_body(role_path, location="temp", scope=org1["id"]),
    )
    assert exists_denied.status_code == 200
    assert exists_denied.json()["exists"] is False

    delete_denied = e2e_client.post(
        "/api/files/delete",
        headers=alice_user.headers,
        json=_read_body(role_path, location="temp", scope=org1["id"]),
    )
    assert delete_denied.status_code == 403

    delete_allowed = e2e_client.post(
        "/api/files/delete",
        headers=org1_user.headers,
        json=_read_body(role_path, location="temp", scope=org1["id"]),
    )
    assert delete_allowed.status_code == 204


@pytest.mark.e2e
async def test_creator_policy_can_list_owned_files_without_directory_allow(
    e2e_client,
    org1_user,
    alice_user,
    org1,
    db_session,
):
    root = f"creator-list/{uuid.uuid4().hex}/"
    creator_path = f"{root}private/alice.txt"

    await _replace_file_policies(
        db_session,
        [
            _policy(
                "seed-writes",
                location="temp",
                scope=org1["id"],
                prefix=f"{root}private",
                actions=["write"],
                when=_allow_all_when(),
            ),
            _policy(
                "creator-list",
                location="temp",
                scope=org1["id"],
                prefix=f"{root}private",
                actions=["read", "list"],
                when=_creator_when(),
            ),
        ],
    )

    creator_write = e2e_client.post(
        "/api/files/write",
        headers=alice_user.headers,
        json=_body(creator_path, location="temp", scope=org1["id"], content="alice"),
    )
    assert creator_write.status_code == 204, creator_write.text

    creator_list = e2e_client.post(
        "/api/files/list",
        headers=alice_user.headers,
        json=_list_body(root, location="temp", scope=org1["id"]),
    )
    assert creator_list.status_code == 200, creator_list.text
    assert creator_list.json()["files"] == [creator_path]

    non_creator_list = e2e_client.post(
        "/api/files/list",
        headers=org1_user.headers,
        json=_list_body(root, location="temp", scope=org1["id"]),
    )
    assert non_creator_list.status_code == 403


@pytest.mark.e2e
async def test_scoped_location_list_returns_relative_paths(
    e2e_client,
    org1_user,
    org1,
    db_session,
):
    root = f"shared/gallery/{uuid.uuid4().hex}/"
    path = f"{root}photo.txt"

    await _replace_file_policies(
        db_session,
        [
            _policy(
                "user-shared-files",
                location="shared",
                scope=org1["id"],
                prefix=root,
                actions=["read", "write", "delete", "list"],
                when=_allow_all_when(),
            )
        ],
    )

    write = e2e_client.post(
        "/api/files/write",
        headers=org1_user.headers,
        json=_body(path, location="shared", scope=org1["id"], content="relative"),
    )
    assert write.status_code == 204, write.text

    listed = e2e_client.post(
        "/api/files/list",
        headers=org1_user.headers,
        json=_list_body(root.rstrip("/"), location="shared", scope=org1["id"]),
    )
    assert listed.status_code == 200, listed.text
    assert listed.json()["files"] == [path]
    assert not listed.json()["files"][0].startswith(f"shared/{org1['id']}/")


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_global_file_policy_cascades_to_org_users_with_override(
    e2e_client,
    db_session,
    platform_admin,
    org1_user,
    org1,
    org2_user,
    org2,
):
    """A GLOBAL (scope=None) shared policy cascades to every org's users, and an
    org-specific policy overrides it for that org — the same org→global cascade
    Tables/Config use. Every real user has an org, so a non-cascading global
    policy would be dead; this locks the cascade end-to-end over REST.

    Setup:
      - global  shared/gallery : everyone may read   (the cascade source)
      - org2    shared/gallery : deny (empty rules)   (the override)
    Expect: org1_user (no override) reads via the global policy; org2_user is
    denied by its override; both call with their default scope (scope=None).
    """
    root = f"gallery-{uuid.uuid4().hex}"
    path = f"{root}/logo.png"
    await _replace_file_policies(
        db_session,
        [
            _policy(
                "everyone-read-global",
                location="shared",
                scope=None,  # GLOBAL
                prefix=root,
                actions=["read", "list"],
                when=_allow_all_when(),
            ),
            _policy(
                # An org policy EXISTS for this prefix (so the override wins over
                # global), but it grants only "write" — so org2's READ is denied
                # and the global read-allow is correctly NOT consulted.
                "org2-write-only-override",
                location="shared",
                scope=org2["id"],
                prefix=root,
                actions=["write"],
                when=_allow_all_when(),
            ),
        ],
    )

    # org1_user has no override → reads through the GLOBAL policy. The file does
    # not exist, so the gate passes and we get 404 (not 403).
    org1_read = e2e_client.post(
        "/api/files/read",
        headers=org1_user.headers,
        json=_read_body(path, location="shared", scope=None),
    )
    assert org1_read.status_code == 404, org1_read.text

    # org2_user is denied by its org-specific override (403, before any 404).
    org2_read = e2e_client.post(
        "/api/files/read",
        headers=org2_user.headers,
        json=_read_body(path, location="shared", scope=None),
    )
    assert org2_read.status_code == 403, org2_read.text
