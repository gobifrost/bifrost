"""Shared builders for the Run As E2E suites (workflows and agents)."""

from __future__ import annotations

import asyncio
from typing import Any
from uuid import UUID

from sqlalchemy import select

from tests.e2e.conftest import poll_until
from tests.e2e.fixtures.setup import _register_and_authenticate_user
from tests.e2e.fixtures.users import E2EUser

USER_ROLE_ID = "00000000-0000-0000-0000-000000000006"
RUN_AS_DENIED = "You don't have permission to run as this user"


def ok(response, status: int = 200) -> Any:
    assert response.status_code == status, f"{response.request.url}: {response.status_code} {response.text}"
    return response.json() if response.content else None


def create_person(e2e_client, admin: dict, organization: dict, tag: str, label: str, *, additional: list[dict]) -> E2EUser:
    """A person in ``organization`` with the User base role plus ``additional`` role assignments."""
    user = E2EUser(
        email=f"run-as-{label}-{tag}@contoso.example",
        password="RunAsPerson123!",
        name=f"Run As {label} {tag}",
        organization_id=UUID(organization["id"]),
    )
    created = ok(
        e2e_client.post(
            "/api/users",
            headers=admin,
            json={"email": user.email, "name": user.name, "organization_id": organization["id"]},
        ),
        201,
    )
    user = _register_and_authenticate_user(user)
    user.user_id = UUID(created["id"])
    ok(
        e2e_client.put(
            f"/api/users/{created['id']}/role-assignments",
            headers=admin,
            json={"base_role_id": USER_ROLE_ID, "additional": additional},
        )
    )
    return user


async def query(session_factory, statement) -> list[Any]:
    """Rows of ``statement`` in a session of its own; closes the app engine the test loop made."""
    from src.core.database import close_db

    try:
        async with session_factory() as session:
            return list((await session.execute(statement)).all())
    finally:
        await close_db()


def recorded_run_as(session_factory, initiator: UUID, run_as_user: UUID, *, count: int = 1) -> list[dict]:
    """The ``run_as`` access checks recorded for ``initiator`` naming
    ``run_as_user``, once the writer has written at least ``count``."""
    from src.models.orm.audit import AuditLog

    statement = select(AuditLog.outcome, AuditLog.organization_id, AuditLog.details).where(
        AuditLog.action == "access.check",
        AuditLog.resource_type == "run_as",
        AuditLog.user_id == initiator,
    )

    def found():
        rows = asyncio.run(query(session_factory, statement))
        matching = [
            {"outcome": outcome, "organization_id": org_id, "details": details}
            for outcome, org_id, details in rows
            if details["inputs"].get("run_as_user_id") == str(run_as_user)
        ]
        return matching if len(matching) >= count else None

    return poll_until(found, max_wait=30, interval=0.5) or []
