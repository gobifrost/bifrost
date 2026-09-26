"""Stage 3a E2E: ``tables.get/query/count`` via live worker and live service.

Exercises the full path — workflow code in a forked worker child calling
the table definitions and reads — against the same tables the external
HTTP endpoints serve, proving parity (filters, pagination, create/list/
delete, missing-entity results) end to end:

- the workflow records that the engine injected the worker's private
  socket, so the migrated calls rode the shared client transport rather
  than the network API (the zero-HTTP proof lives in the unit and
  forked-child socket tests, where the child's network API is dead);
- the same values served over external HTTP match.

A second, service-identity path registers a live ``@service`` doing the
same reads: its token identity differs (system-user non-superuser,
org-confined), so the default admin-bypass table reads as empty/denied —
proving the parent dispatches service table calls under the service
token, not the initiating user's admin flag.
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid

import pytest

from tests.e2e.conftest import execute_workflow_sync, write_and_register

pytestmark = pytest.mark.e2e


def _uid() -> str:
    return uuid.uuid4().hex[:8]


@pytest.fixture(scope="module")
def live_table_keys():
    tag = _uid()
    return {
        "tag": tag,
        "table": f"slive_tbl_{tag}",
        "missing_table": f"slive_tbl_missing_{tag}",
    }


@pytest.fixture(scope="module")
def live_table(e2e_client, platform_admin, live_table_keys):
    """Seed one GLOBAL table with three rows (deterministic for any caller org)."""
    name = live_table_keys["table"]
    create = e2e_client.post(
        "/api/tables",
        headers=platform_admin.headers,
        json={"name": name, "description": "stage 3a local tables E2E",
              "organization_id": None},
    )
    assert create.status_code == 201, create.text
    table_id = create.json()["id"]
    rows = [
        ("row-a", {"status": "active", "n": 1}),
        ("row-b", {"status": "archived", "n": 2}),
        ("row-c", {"status": "active", "n": 3}),
    ]
    for doc_id, data in rows:
        insert = e2e_client.post(
            f"/api/tables/{table_id}/documents",
            headers=platform_admin.headers,
            json={"id": doc_id, "data": data},
        )
        assert insert.status_code == 201, insert.text
    yield {"table_id": table_id}
    e2e_client.delete(f"/api/tables/{table_id}", headers=platform_admin.headers)


@pytest.fixture(scope="module")
def live_tables_workflow(
    e2e_client, platform_admin, org1, live_table_keys, live_table
):
    name = f"e2e_sdk_local_tables_{live_table_keys['tag']}"
    path = f"{name}.py"
    table = live_table_keys["table"]
    missing = live_table_keys["missing_table"]
    created_name = f"slive_new_{live_table_keys['tag']}"
    content = f'''"""Stage 3a local table definitions/reads E2E workflow."""
from bifrost import workflow, tables
from bifrost.client import get_engine_socket_path

@workflow(name="{name}", description="Stage 3a local table reads E2E")
async def {name}():
    used_socket = get_engine_socket_path() is not None
    doc = await tables.get("{table}", "row-a")
    filtered = await tables.query(
        "{table}", where={{"status": "active"}},
        order_by="n", order_dir="desc", limit=1,
    )
    total = await tables.count("{table}")
    active_total = await tables.count("{table}", where={{"status": "active"}})
    missing_doc = await tables.get("{table}", "row-missing")
    missing_query = await tables.query("{missing}")
    missing_count = await tables.count("{missing}")
    created = await tables.create("{created_name}", description="stage 3a")
    listed_names = [t.name for t in await tables.list()]
    deleted = await tables.delete(created.id)
    return {{
        "used_socket": used_socket,
        "doc_id": doc.id,
        "doc_n": doc.data.get("n"),
        "filtered_ids": [d.id for d in filtered.documents],
        "filtered_total": filtered.total,
        "total": total,
        "active_total": active_total,
        "missing_doc": missing_doc,
        "missing_docs": missing_query.documents,
        "missing_total": missing_query.total,
        "missing_count": missing_count,
        "created_id": created.id,
        "created_listed": "{created_name}" in listed_names,
        "deleted": deleted,
    }}
'''
    registered = write_and_register(
        e2e_client,
        platform_admin.headers,
        path,
        content,
        name,
        organization_id=org1["id"],
    )
    resp = e2e_client.patch(
        f"/api/workflows/{registered['id']}",
        headers=platform_admin.headers,
        json={"organization_id": org1["id"], "access_level": "authenticated"},
    )
    assert resp.status_code == 200, f"workflow patch failed: {resp.text}"
    yield registered

    e2e_client.delete(
        f"/api/files/editor?path={path}",
        headers=platform_admin.headers,
    )


class TestSdkTablesLocalLiveE2E:
    def test_workflow_reads_match_http(
        self, e2e_client, org1_user, platform_admin, live_tables_workflow, live_table_keys,
        live_table
    ):
        result = execute_workflow_sync(
            e2e_client,
            org1_user.headers,
            live_tables_workflow["id"],
            max_wait=120.0,
        )
        assert result["status"] == "Success", result
        out = result["result"]
        # The worker injected its private socket, so the migrated calls rode
        # the shared client transport (zero-HTTP proof is in the unit and
        # forked-child tests).
        assert out["used_socket"] is True
        assert out["doc_id"] == "row-a"
        assert out["doc_n"] == 1
        assert out["filtered_ids"] == ["row-c"]
        assert out["filtered_total"] == 2
        assert out["total"] == 3
        assert out["active_total"] == 2
        assert out["missing_doc"] is None
        assert out["missing_docs"] == []
        assert out["missing_total"] == 0
        assert out["missing_count"] == 0
        # Definitions: create/list/delete round-trip through the live worker.
        assert out["created_id"]
        assert out["created_listed"] is True
        assert out["deleted"] is True

        # The delete committed: the created table is gone over external HTTP.
        gone = e2e_client.get(
            f"/api/tables/{out['created_id']}",
            headers=platform_admin.headers,
        )
        assert gone.status_code == 404, gone.text

        # Committed state verified over external HTTP (parity).
        table_id = live_table["table_id"]
        get = e2e_client.get(
            f"/api/tables/{table_id}/documents/row-a",
            headers=platform_admin.headers,
        )
        assert get.status_code == 200, get.text
        assert get.json()["data"] == {"status": "active", "n": 1}

        queried = e2e_client.post(
            f"/api/tables/{table_id}/documents/query",
            headers=platform_admin.headers,
            json={"where": {"status": "active"}, "order_by": "n",
                  "order_dir": "desc", "limit": 1},
        )
        assert queried.status_code == 200, queried.text
        assert [d["id"] for d in queried.json()["documents"]] == ["row-c"]
        assert queried.json()["total"] == 2

        counted = e2e_client.get(
            f"/api/tables/{table_id}/documents/count",
            headers=platform_admin.headers,
        )
        assert counted.status_code == 200, counted.text
        assert counted.json() == {"count": 3}


_SERVICE_SOURCE = '''"""Stage 3a local table reads E2E service (service token identity)."""

import json
import logging

from bifrost import service, tables


@service
async def {function_name}() -> None:
    """Read the seeded table with HTTP disabled, log the outcome, park."""
    await service.ready()
    from bifrost.client import get_engine_socket_path
    used_socket = get_engine_socket_path() is not None

    queried = await tables.query("{table}")
    try:
        await tables.get("{table}", "row-a")
        get_result = "LEAKED"
    except Exception as e:
        get_result = type(e).__name__
    counted = await tables.count("{table}")
    logging.getLogger(__name__).info(
        "TABLES_LOCAL %s",
        json.dumps({{
            "used_socket": used_socket,
            "query_total": queried.total,
            "query_docs": len(queried.documents),
            "get_result": get_result,
            "count": counted,
        }}),
    )
    await service.wait_until_stopping()
'''


def _service_definition(e2e_client, headers, workflow_id: str) -> dict:
    resp = e2e_client.get("/api/services", headers=headers)
    assert resp.status_code == 200, resp.text
    for item in resp.json()["items"]:
        if item["workflow_id"] == workflow_id:
            return item
    raise AssertionError(f"no service definition for workflow {workflow_id}")


async def _wait_for_state(e2e_client, headers, definition_id: str, state: str,
                          timeout=180.0) -> dict:
    deadline = time.monotonic() + timeout
    last: dict = {}
    while time.monotonic() < deadline:
        resp = e2e_client.get(f"/api/services/{definition_id}", headers=headers)
        assert resp.status_code == 200, resp.text
        last = resp.json()
        if last["observed_state"] == state:
            return last
        await asyncio.sleep(1.0)
    raise AssertionError(f"service {definition_id} never reached {state}: {last}")


@pytest.fixture(scope="module")
def live_tables_service(e2e_client, platform_admin, live_table_keys, live_table):
    suffix = live_table_keys["tag"]
    function_name = f"e2e_svc_tables_{suffix}"
    path = f"workflows/e2e_svc_tables_{suffix}.py"
    registered = write_and_register(
        e2e_client,
        platform_admin.headers,
        path,
        _SERVICE_SOURCE.format(
            function_name=function_name, table=live_table_keys["table"]
        ),
        function_name,
    )
    assert registered["type"] == "service", registered
    definition = _service_definition(
        e2e_client, platform_admin.headers, registered["id"]
    )
    yield definition
    e2e_client.post(
        f"/api/services/{definition['id']}/stop", headers=platform_admin.headers
    )
    e2e_client.post(
        f"/api/services/{definition['id']}/disable", headers=platform_admin.headers
    )
    e2e_client.delete(
        f"/api/files/editor?path={path}", headers=platform_admin.headers
    )


class TestSdkTablesLocalLiveService:
    async def test_service_reads_use_service_token_identity(
        self, e2e_client, platform_admin, live_tables_service
    ):
        """The service child rides the local transport under its own token.

        The seeded table carries the default admin-bypass policy, which the
        service token (system-user non-superuser) does not satisfy — so a
        local query reads empty, a local get denies, and a local count is
        zero, even though the platform-admin initiator could read everything.
        """
        definition_id = live_tables_service["id"]
        await _wait_for_state(
            e2e_client, platform_admin.headers, definition_id, "running"
        )
        deadline = time.monotonic() + 120.0
        payload = None
        while time.monotonic() < deadline:
            resp = e2e_client.get(
                f"/api/services/{definition_id}/logs",
                headers=platform_admin.headers,
                params={"limit": 200},
            )
            assert resp.status_code == 200, resp.text
            for item in resp.json()["items"]:
                message = item.get("message", "")
                if "TABLES_LOCAL" in message:
                    payload = json.loads(message.split("TABLES_LOCAL", 1)[1])
                    break
            if payload is not None:
                break
            await asyncio.sleep(2.0)
        assert payload is not None, "service never logged its table outcome"
        assert payload["used_socket"] is True
        assert payload["query_total"] == 0
        assert payload["query_docs"] == 0
        assert payload["get_result"] == "BifrostAuthorizationError"
        assert payload["count"] == 0
