"""Live local-Solution development context regression coverage.

This covers the local-development data plane without paying for a deploy or an
app build: the install-owned rows are direct fixtures, while the API,
capability, reverse proxy, and local Python host are all real.
"""
from __future__ import annotations

import os
from uuid import UUID, uuid4

import httpx
import pytest
from aiohttp import web
from sqlalchemy import delete

from bifrost._context import clear_execution_context
from bifrost.client import BifrostAPIError, BifrostClient
from bifrost.solution_dev.function_host import FunctionHost, set_dev_execution_context
from bifrost.solution_dev.proxy import DevProxyConfig, build_dev_app
from bifrost.solution_dev.session import DEV_CONTEXT_HEADER, SolutionDevSession
from src.models.orm.applications import Application
from src.models.orm.solutions import Solution
from src.models.orm.tables import Document, Table
from src.services.solutions.deploy import solution_entity_id


pytestmark = pytest.mark.e2e
TEST_API_URL = os.getenv("TEST_API_URL", "http://api:8000")


def _owner_policies() -> dict:
    """Allow each authenticated caller to read only their own rows and write rows."""
    return {
        "policies": [
            {
                "name": "own_rows",
                "actions": ["read"],
                "when": {"eq": [{"row": "created_by"}, {"user": "user_id"}]},
            },
            {"name": "authenticated_writes", "actions": ["create"], "when": None},
        ]
    }


async def _serve_proxy(app: web.Application) -> tuple[web.AppRunner, str]:
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    sockets = site._server.sockets if site._server is not None else []  # type: ignore[attr-defined]
    assert sockets, "local development proxy did not bind a socket"
    port = sockets[0].getsockname()[1]
    return runner, f"http://127.0.0.1:{port}"


async def test_solution_dev_context_binds_proxy_and_local_host_to_installed_scope(
    e2e_client,
    platform_admin,
    org1,
    org1_user,
    db_session,
    tmp_path,
) -> None:
    """A local preview has its install scope, user ownership, and sealed boundaries.

    The test deliberately creates rows instead of deploying: its contract is
    development-context issuance and consumption across the HTTP proxy and
    FunctionHost, not package installation or build execution.
    """
    source_id = uuid4()
    target_id = uuid4()
    source_app_manifest_id = uuid4()
    source_app_id = solution_entity_id(source_id, source_app_manifest_id)
    source_table_name = f"dev_context_rows_{uuid4().hex[:10]}"
    empty_table_name = f"dev_context_empty_{uuid4().hex[:10]}"
    target_table_name = f"dev_context_sealed_{uuid4().hex[:10]}"
    missing_table_name = f"dev_context_missing_{uuid4().hex[:10]}"
    organization_id = UUID(org1["id"])

    source = Solution(
        id=source_id,
        slug=f"dev-context-source-{uuid4().hex[:8]}",
        name="Local Development Source",
        organization_id=organization_id,
        allow_inbound_access=False,
    )
    target = Solution(
        id=target_id,
        slug=f"dev-context-target-{uuid4().hex[:8]}",
        name="Sealed Target",
        organization_id=organization_id,
        allow_inbound_access=False,
    )
    source_table = Table(
        id=solution_entity_id(source_id, uuid4()),
        name=source_table_name,
        organization_id=organization_id,
        solution_id=source_id,
        access=_owner_policies(),
    )
    empty_table = Table(
        id=solution_entity_id(source_id, uuid4()),
        name=empty_table_name,
        organization_id=organization_id,
        solution_id=source_id,
        access=_owner_policies(),
    )
    target_table = Table(
        id=solution_entity_id(target_id, uuid4()),
        name=target_table_name,
        organization_id=organization_id,
        solution_id=target_id,
        access=_owner_policies(),
    )
    # These rows are deliberately direct fixtures. Flush each FK layer because
    # there are no ORM relationship edges for SQLAlchemy to infer this order.
    db_session.add_all([source, target])
    await db_session.flush()
    db_session.add_all(
        [
            Application(
                id=source_app_id,
                solution_id=source_id,
                organization_id=organization_id,
                name="Local Preview",
                slug=f"local-preview-{uuid4().hex[:8]}",
                app_model="standalone_v2",
                dependencies={},
                access_level="authenticated",
            ),
            source_table,
            empty_table,
            target_table,
        ]
    )
    await db_session.flush()
    db_session.add_all(
        [
            Document(
                table_id=source_table.id,
                id="owned-row",
                data={"marker": "owned"},
                created_by=str(platform_admin.user_id),
            ),
            Document(
                table_id=source_table.id,
                id="other-user-row",
                data={"marker": "other"},
                created_by=str(org1_user.user_id),
            ),
        ]
    )
    await db_session.flush()
    await db_session.commit()

    client: BifrostClient | None = None
    proxy_runner: web.AppRunner | None = None
    try:
        issued = e2e_client.post(
            f"/api/solutions/{source_id}/dev-context",
            headers=platform_admin.headers,
            json={"app_id": str(source_app_manifest_id)},
        )
        assert issued.status_code == 200, issued.text
        capability = issued.json()
        assert capability["solution_id"] == str(source_id)
        assert capability["organization_id"] == str(organization_id)
        assert capability["app_id"] == str(source_app_id)
        assert capability["token"]
        assert capability["expires_at"] > 0

        # The signed context supplements a real bearer; it neither replaces one nor
        # grants a second user's credentials access to this developer session.
        forged = e2e_client.post(
            f"/api/tables/{source_table_name}/documents/query",
            headers={**platform_admin.headers, DEV_CONTEXT_HEADER: "forged"},
            json={},
        )
        assert forged.status_code == 401, forged.text
        other_user = e2e_client.post(
            f"/api/tables/{source_table_name}/documents/query",
            headers={**org1_user.headers, DEV_CONTEXT_HEADER: capability["token"]},
            json={},
        )
        assert other_user.status_code == 403, other_user.text
        cross_sealed = e2e_client.post(
            f"/api/tables/{target_table_name}/documents/query?solution={target_id}",
            headers={**platform_admin.headers, DEV_CONTEXT_HEADER: capability["token"]},
            json={},
        )
        assert cross_sealed.status_code == 404, cross_sealed.text

        client = BifrostClient(TEST_API_URL, platform_admin.access_token or "")
        session = SolutionDevSession(client, str(source_id), str(source_app_manifest_id))
        client._solution_dev_session = session
        context_headers = await session.headers()
        assert context_headers[DEV_CONTEXT_HEADER]
        assert session.app_id == str(source_app_id)
        assert session.organization_id == str(organization_id)

        workspace = tmp_path / "local-solution"
        workflow_path = workspace / "workflows" / "table_roundtrip.py"
        workflow_path.parent.mkdir(parents=True)
        workflow_path.write_text(
            "from bifrost import tables, workflow\n\n"
            "@workflow\n"
            "async def read_write(table_name, document_id):\n"
            "    before = await tables.query(table_name)\n"
            "    await tables.insert(table_name, {'marker': 'local'}, id=document_id)\n"
            "    after = await tables.query(table_name)\n"
            "    return {\n"
            "        'before': sorted(doc.id for doc in before.documents),\n"
            "        'after': sorted(doc.id for doc in after.documents),\n"
            "    }\n\n"
            "@workflow\n"
            "async def missing(table_name):\n"
            "    await tables.query(table_name)\n"
        )
        set_dev_execution_context(
            user={
                "id": str(platform_admin.user_id),
                "email": platform_admin.email,
                "name": platform_admin.name,
                "is_superuser": True,
            },
            org={"id": str(organization_id), "name": org1["name"]},
            solution_id=str(source_id),
        )
        host = FunctionHost(workspace, client=client)
        host.reload()

        proxy = build_dev_app(
            DevProxyConfig(
                upstream_url=TEST_API_URL,
                token=platform_admin.access_token or "",
                app_id=session.app_id,
                org_id=session.organization_id,
                solution_id=str(source_id),
                dev_context_headers=session.headers,
            ),
            host,
            vite_url=TEST_API_URL,
        )
        proxy_runner, proxy_url = await _serve_proxy(proxy)
        async with httpx.AsyncClient(base_url=proxy_url) as proxy_client:
            frontend_rows = await proxy_client.post(
                f"/api/tables/{source_table_name}/documents/query", json={}
            )
            assert frontend_rows.status_code == 200, frontend_rows.text
            assert [row["id"] for row in frontend_rows.json()["documents"]] == ["owned-row"]

            empty_table_response = await proxy_client.post(
                f"/api/tables/{empty_table_name}/documents/query", json={}
            )
            assert empty_table_response.status_code == 200, empty_table_response.text
            assert empty_table_response.json()["documents"] == []

        async with httpx.AsyncClient(base_url=proxy_url) as proxy_client:
            local_workflow = await proxy_client.post(
                "/api/workflows/execute",
                json={
                    "workflow_id": "workflows/table_roundtrip.py::read_write",
                    "input_data": {
                        "table_name": source_table_name,
                        "document_id": "local-write",
                    },
                },
            )
        assert local_workflow.status_code == 200, local_workflow.text
        local_payload = local_workflow.json()
        assert "result" in local_payload, local_payload
        assert local_payload["result"] == {
            "before": ["owned-row"],
            "after": ["local-write", "owned-row"],
        }
        with pytest.raises(BifrostAPIError, match="could not be resolved or authorized"):
            await host.run(
                "workflows/table_roundtrip.py::missing",
                {"table_name": missing_table_name},
            )
    finally:
        clear_execution_context()
        if proxy_runner is not None:
            await proxy_runner.cleanup()
        if client is not None:
            await client.close()
        await db_session.execute(delete(Solution).where(Solution.id.in_((source_id, target_id))))
        await db_session.commit()
