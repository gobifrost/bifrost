"""Named permissions recorded for a person's own requests (report-only).

An elevated branch a person takes today (a provider-org member reading
another organization's agent) is judged against their own roles and written
as an ``access.check`` failure with ``enforced: false``; the request itself
is unchanged.
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

import pytest

from tests.e2e.conftest import poll_until
from tests.e2e.fixtures.setup import PROVIDER_ORG_ID, _register_and_authenticate_user
from tests.e2e.fixtures.users import E2EUser

pytestmark = pytest.mark.e2e


def _ok(response) -> Any:
    assert response.status_code in (200, 201, 204), f"{response.request.url}: {response.status_code} {response.text}"
    return response.json() if response.content else None


async def _permission_checks(session_factory, user_id: uuid.UUID) -> list[dict]:
    from sqlalchemy import select

    from src.core.database import close_db
    from src.models.orm.audit import AuditLog

    try:
        async with session_factory() as session:
            rows = (
                await session.execute(
                    select(AuditLog).where(
                        AuditLog.action == "access.check",
                        AuditLog.resource_type == "permission",
                        AuditLog.user_id == user_id,
                    )
                )
            ).scalars()
            return [{"outcome": r.outcome, "organization_id": r.organization_id, "details": r.details} for r in rows]
    finally:
        await close_db()


def _person(e2e_client, admin: dict, org_id: str, key: str) -> tuple[dict, E2EUser]:
    tag = uuid.uuid4().hex[:8]
    created = _ok(
        e2e_client.post(
            "/api/users",
            headers=admin,
            json={"email": f"pc-{key}-{tag}@contoso.example", "name": f"PC {key}", "organization_id": org_id},
        )
    )
    person = _register_and_authenticate_user(
        E2EUser(email=created["email"], password=f"Pc-{tag}-Pass1!", name=f"PC {key}", organization_id=uuid.UUID(org_id)),
        skip_registration=False,
    )
    return created, person


def test_a_provider_member_reading_another_orgs_agent_records_one_would_deny(
    e2e_client, platform_admin, org1, async_session_factory
) -> None:
    admin = platform_admin.headers
    created, member = _person(e2e_client, admin, str(PROVIDER_ORG_ID), "member")
    agent = _ok(
        e2e_client.post(
            "/api/agents",
            headers=admin,
            json={
                "name": f"PC agent {uuid.uuid4().hex[:8]}",
                "system_prompt": "Reply briefly.",
                "channels": ["chat"],
                "access_level": "role_based",
                "organization_id": org1["id"],
            },
        )
    )
    try:
        response = e2e_client.get(f"/api/agents/{agent['id']}", headers=member.headers)

        assert response.status_code == 200, response.text
        assert response.json()["id"] == agent["id"]
        subject = f"agent:{agent['id']}"
        checks = poll_until(
            lambda: [
                c
                for c in asyncio.run(_permission_checks(async_session_factory, uuid.UUID(created["id"])))
                if c["details"]["inputs"]["subject"] == subject
            ],
            max_wait=10,
        )
        assert checks is not None, "no access.check was written for the elevated read"
        [check] = checks
        assert check["outcome"] == "failure"
        assert check["organization_id"] == uuid.UUID(org1["id"])
        assert (check["details"]["enforced"], check["details"]["direct"]) == (False, True)
        assert check["details"]["inputs"]["permission"] == "agents.read"
        assert check["details"]["trace"]["steps"][-1]["facts"]["permission_display_name"] == "Read Agents"
    finally:
        e2e_client.delete(f"/api/agents/{agent['id']}", headers=admin)
        e2e_client.delete(f"/api/users/{created['id']}", headers=admin)

