"""E2E: identities never sign in and are kept apart from people.

An identity is an ordinary user account for roles, but it has no
credentials, can't be registered or invited, doesn't appear in the Users
list, and isn't edited or deleted through the people routes.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from shared.identities import IDENTITY_EMAIL_DOMAIN
from src.models.enums import IdentityKind
from src.models.orm.audit import AuditLog
from src.models.orm.users import User

pytestmark = pytest.mark.e2e

USER_ROLE_ID = "00000000-0000-0000-0000-000000000006"


async def _org_identity(db_session, org_id: str) -> User:
    return (
        await db_session.scalars(
            select(User).where(
                User.organization_id == uuid.UUID(org_id),
                User.identity_kind == IdentityKind.ORG_DEFAULT,
            )
        )
    ).one()


@pytest.mark.asyncio
async def test_identity_cannot_sign_in(private_client, org1, db_session) -> None:
    identity = await _org_identity(db_session, org1["id"])

    response = private_client.post(
        "/auth/login",
        data={"username": identity.email, "password": "any-password-1!"},
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    assert response.status_code == 401, response.text

    reasons = (
        await db_session.scalars(
            select(AuditLog.details["reason"].astext).where(
                AuditLog.action == "auth.login.failed", AuditLog.resource_id == identity.id
            )
        )
    ).all()
    assert reasons == ["identity"]


@pytest.mark.asyncio
async def test_identity_cannot_be_registered(private_client, org1, db_session) -> None:
    identity = await _org_identity(db_session, org1["id"])

    response = private_client.post(
        "/auth/register",
        json={"email": identity.email, "password": "Registered-Pass-1!", "name": "Claimed"},
    )
    assert response.status_code != 201, response.text

    await db_session.refresh(identity)
    assert identity.hashed_password is None
    assert identity.name == f"{org1['name']} identity"


def test_identity_email_domain_is_reserved(e2e_client, platform_admin, org1) -> None:
    response = e2e_client.post(
        "/api/users",
        headers=platform_admin.headers,
        json={
            "email": f"someone-{uuid.uuid4().hex[:8]}@{IDENTITY_EMAIL_DOMAIN}",
            "name": "Reserved",
            "organization_id": org1["id"],
        },
    )
    assert response.status_code == 422, response.text


def test_users_list_has_no_identities(e2e_client, platform_admin, org1, org1_user) -> None:
    for params in ({}, {"scope": org1["id"]}):
        response = e2e_client.get("/api/users", headers=platform_admin.headers, params=params)
        assert response.status_code == 200, response.text
        emails = [user["email"] for user in response.json()]
        assert emails
        assert not [email for email in emails if email.endswith("@" + IDENTITY_EMAIL_DOMAIN)]


@pytest.mark.asyncio
async def test_identity_is_not_edited_or_deleted_as_a_person(e2e_client, platform_admin, org1, db_session) -> None:
    identity = await _org_identity(db_session, org1["id"])

    patched = e2e_client.patch(
        f"/api/users/{identity.id}", headers=platform_admin.headers, json={"name": "Renamed"}
    )
    assert patched.status_code == 409, patched.text
    assert patched.json()["detail"] == "Identities are managed from Identities"

    deleted = e2e_client.delete(f"/api/users/{identity.id}", headers=platform_admin.headers)
    assert deleted.status_code == 409, deleted.text

    bulk = e2e_client.patch(
        "/api/users/bulk",
        headers=platform_admin.headers,
        json={"user_ids": [str(identity.id)], "operation": "set_active", "is_active": False},
    )
    assert bulk.status_code == 200, bulk.text
    assert bulk.json()["failed"] == [
        {"user_id": str(identity.id), "reason": "Identities are managed from Identities"}
    ]

    await db_session.refresh(identity)
    assert (identity.name, identity.is_active) == (f"{org1['name']} identity", True)


@pytest.mark.asyncio
async def test_identity_takes_roles_like_anyone(e2e_client, platform_admin, org1, db_session) -> None:
    identity = await _org_identity(db_session, org1["id"])
    role = e2e_client.post(
        "/api/roles", headers=platform_admin.headers, json={"name": f"identity-role-{uuid.uuid4().hex[:8]}"}
    )
    assert role.status_code == 201, role.text
    role_id = role.json()["id"]

    try:
        assigned = e2e_client.put(
            f"/api/users/{identity.id}/role-assignments",
            headers=platform_admin.headers,
            json={"base_role_id": USER_ROLE_ID, "additional": [{"role_id": role_id}]},
        )
        assert assigned.status_code == 200, assigned.text
        view = e2e_client.get(f"/api/users/{identity.id}/role-assignments", headers=platform_admin.headers)
        assert view.status_code == 200, view.text
        assert role_id in {item["role_id"] for item in view.json()["additional"]}
    finally:
        e2e_client.put(
            f"/api/users/{identity.id}/role-assignments",
            headers=platform_admin.headers,
            json={"base_role_id": USER_ROLE_ID, "additional": []},
        )
        e2e_client.delete(f"/api/roles/{role_id}", headers=platform_admin.headers)
