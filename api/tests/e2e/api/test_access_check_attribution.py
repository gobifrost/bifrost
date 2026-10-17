"""External account claims and direct versus agent access-check attribution."""

from __future__ import annotations

import asyncio
from uuid import UUID, uuid4

import jwt
import pytest

from src.services.mcp_server.server import MCPContext
from src.services.mcp_server.tools._http_bridge import _token_from_context
from tests.e2e.conftest import poll_until
from tests.e2e.fixtures.setup import PROVIDER_ORG_ID, _register_and_authenticate_user
from tests.e2e.fixtures.users import E2EUser

pytestmark = pytest.mark.e2e


def _ok(response, status=200):
    assert response.status_code == status, response.text
    return response.json() if response.content else None


async def _create_running_agent_run(session_factory, *, user_id: str) -> str:
    from src.core.database import close_db
    from src.models.orm.agent_runs import AgentRun

    try:
        async with session_factory() as session:
            run = AgentRun(
                trigger_type="test",
                status="running",
                org_id=UUID(str(PROVIDER_ORG_ID)),
                run_user_id=UUID(user_id),
                acting_user_id=UUID(user_id),
            )
            session.add(run)
            await session.commit()
            return str(run.id)
    finally:
        await close_db()


async def _delete_agent_run(session_factory, run_id: str) -> None:
    from sqlalchemy import delete

    from src.core.database import close_db
    from src.models.orm.agent_runs import AgentRun

    try:
        async with session_factory() as session:
            await session.execute(delete(AgentRun).where(AgentRun.id == UUID(run_id)))
            await session.commit()
    finally:
        await close_db()


def test_external_provider_claim_and_agent_access_checks(
    e2e_client, platform_admin, org1, async_session_factory
):
    """A durable agent bridge retains the external actor and run attribution."""
    admin = platform_admin.headers
    tag = uuid4().hex[:8]
    person = _ok(e2e_client.post("/api/users", headers=admin, json={
        "email": f"contoso-guest-{tag}@contoso.example", "name": "Contoso Guest",
        "organization_id": str(PROVIDER_ORG_ID), "is_external": True,
    }), 201)
    form = None
    run_id = None
    try:
        account = _register_and_authenticate_user(E2EUser(
            email=person["email"], password=f"Contoso-{tag}-Pass1!", name=person["name"],
            organization_id=PROVIDER_ORG_ID,
        ))
        claims = jwt.decode(account.access_token, options={"verify_signature": False})
        assert claims["is_external"] is True
        assert claims["is_provider_org"] is True
        form = _ok(e2e_client.post("/api/forms", headers=admin, json={
            "name": f"Contoso Form {tag}", "organization_id": str(PROVIDER_ORG_ID),
            "form_schema": {"fields": []}, "access_level": "everyone",
        }), 201)
        for _ in range(2):
            _ok(e2e_client.get(
                f"/api/forms/{form['id']}/runtime",
                headers={**account.headers, "X-Bifrost-Surface": "cli"},
            ))
        run_id = asyncio.run(
            _create_running_agent_run(async_session_factory, user_id=person["id"])
        )
        bridge = _token_from_context(MCPContext(
            user_id=person["id"], org_id=PROVIDER_ORG_ID, user_email=person["email"],
            runtime_credential_kind="agent_run", runtime_credential_id=run_id,
        ))
        bridge_headers = {"Authorization": f"Bearer {bridge}", "X-Bifrost-Surface": "mcp"}
        _ok(e2e_client.get(f"/api/forms/{form['id']}/runtime", headers=bridge_headers))
        _ok(
            e2e_client.post("/api/sdk/tables/list", headers=bridge_headers, json={"scope": org1["id"]}),
            403,
        )

        def rows():
            body = _ok(e2e_client.get("/api/audit", headers=admin, params={"action": "access.check", "user_id": person["id"]}))
            return [row for row in body["entries"] if row["actor"]["user_id"] == person["id"]]

        def ready():
            found = rows()
            return found if any(row["details"].get("agent_run_id") == run_id for row in found) else None

        checks = poll_until(ready, max_wait=15)
        assert checks is not None, "Access-check rows were not written"
        assert not [row for row in checks if row["resource_type"] == "permission"]
        agent = [row for row in checks if row["details"].get("agent_run_id") == run_id]
        assert len(agent) == 1
        assert agent[0]["actor"]["user_id"] == str(UUID(person["id"]))
        assert agent[0]["surface"] == "agent"
        assert agent[0]["details"]["direct"] is True
        assert agent[0]["resource_type"] == "scope_switch"
        assert agent[0]["outcome"] == "failure"
        assert agent[0]["details"]["enforced"] is True
    finally:
        if run_id:
            asyncio.run(_delete_agent_run(async_session_factory, run_id))
        if form:
            _ok(e2e_client.delete(f"/api/forms/{form['id']}", headers=admin), 204)
        _ok(e2e_client.delete(f"/api/users/{person['id']}", headers=admin), 204)
