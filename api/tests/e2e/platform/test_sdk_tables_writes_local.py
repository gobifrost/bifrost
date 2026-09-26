"""Stage 3b E2E: ``tables`` facade writes via live worker.

Exercises the full path — workflow code in a forked worker child calling
the fixed table operations — against tables the external HTTP endpoints
serve, proving write parity end to end:

- the workflow records that the engine injected the worker's private
  socket, so the migrated operations rode the shared client transport
  rather than the network API — including the auto-create-on-insert retry,
  which rides the engine-local ``tables.create`` operation, never HTTP
  (the zero-HTTP proof lives in the unit and forked-child socket tests,
  where the child's network API is dead);
- at least one metadata mutation (``create``), one single document
  write (``insert``), and one batch write (``insert_batch``) commit
  state verified over external HTTP, alongside upsert/update/delete
  coverage in the same run.

Service-identity write behavior (deny parity, attribution, the
service-delete 403) is covered without a fork in
``tests/unit/sdk/test_sdk_tables_writes_local.py``.
"""

from __future__ import annotations

import uuid

import pytest

from tests.e2e.conftest import execute_workflow_sync, write_and_register

pytestmark = pytest.mark.e2e


def _uid() -> str:
    return uuid.uuid4().hex[:8]


@pytest.fixture(scope="module")
def live_write_keys():
    tag = _uid()
    return {
        "tag": tag,
        "table": f"wlive_tbl_{tag}",
        "auto_table": f"wlive_auto_{tag}",
    }


@pytest.fixture(scope="module")
def live_writes_workflow(e2e_client, platform_admin, org1, live_write_keys):
    name = f"e2e_sdk_local_table_writes_{live_write_keys['tag']}"
    path = f"{name}.py"
    table = live_write_keys["table"]
    auto_table = live_write_keys["auto_table"]
    content = f'''"""Stage 3b local table writes E2E workflow."""
from bifrost import workflow, tables
from bifrost.client import get_engine_socket_path

@workflow(name="{name}", description="Stage 3b local table writes E2E")
async def {name}():
    used_socket = get_engine_socket_path() is not None
    created = await tables.create("{table}")
    one = await tables.insert("{table}", {{"v": 1}}, id="w-1")
    up = await tables.upsert("{table}", "w-1", {{"v": 2}})
    batch = await tables.insert_batch("{table}", [
        {{"id": "w-2", "data": {{"v": 3}}}},
        {{"v": 4}},
    ])
    bulk = await tables.bulk_upsert("{table}", [
        {{"id": "w-3", "data": {{"v": 5}}}},
    ])
    updated = await tables.update("{table}", "w-2", {{"extra": True}})
    listed = await tables.list()
    # Auto-create-on-insert through the engine-local create operation.
    auto = await tables.insert("{auto_table}", {{"v": 9}}, id="a-1")
    removed = await tables.delete_document("{table}", "w-3")
    batch_removed = await tables.delete_batch("{table}", ["w-1"])
    return {{
        "used_socket": used_socket,
        "table_id": created.id,
        "one_id": one.id,
        "up_v": up.data.get("v"),
        "batch_count": batch.count,
        "batch_ids": sorted(d.id for d in batch.documents),
        "bulk_count": bulk.count,
        "updated_data": updated.data,
        "listed_has_table": "{table}" in [t.name for t in listed],
        "auto_table": auto.data,
        "removed": removed,
        "batch_removed_count": batch_removed.count,
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

    for table_name in (table, auto_table):
        found = e2e_client.get(
            "/api/tables", headers=platform_admin.headers
        )
        if found.status_code == 200:
            for item in found.json()["tables"]:
                if item["name"] == table_name:
                    e2e_client.delete(
                        f"/api/tables/{item['id']}",
                        headers=platform_admin.headers,
                    )
    e2e_client.delete(
        f"/api/files/editor?path={path}",
        headers=platform_admin.headers,
    )


class TestSdkTableWritesLocalLiveE2E:
    def test_workflow_writes_match_http(
        self, e2e_client, org1_user, platform_admin, live_writes_workflow,
        live_write_keys, org1,
    ):
        result = execute_workflow_sync(
            e2e_client,
            org1_user.headers,
            live_writes_workflow["id"],
            max_wait=180.0,
        )
        assert result["status"] == "Success", result
        out = result["result"]
        # The worker injected its private socket, so the migrated calls rode
        # the shared client transport (zero-HTTP proof is in the unit and
        # forked-child tests).
        assert out["used_socket"] is True
        assert out["one_id"] == "w-1"
        assert out["up_v"] == 2
        assert out["batch_count"] == 2
        assert "w-2" in out["batch_ids"] and len(out["batch_ids"]) == 2
        assert out["bulk_count"] == 1
        assert out["updated_data"] == {"v": 3, "extra": True}
        assert out["listed_has_table"] is True
        assert out["auto_table"] == {"v": 9}
        assert out["removed"] is True
        assert out["batch_removed_count"] == 1

        # Committed state verified over external HTTP (parity).
        table_id = out["table_id"]
        get = e2e_client.get(
            f"/api/tables/{table_id}/documents/w-2",
            headers=platform_admin.headers,
        )
        assert get.status_code == 200, get.text
        assert get.json()["data"] == {"v": 3, "extra": True}

        queried = e2e_client.post(
            f"/api/tables/{table_id}/documents/query",
            headers=platform_admin.headers,
            json={"limit": 100},
        )
        assert queried.status_code == 200, queried.text
        ids = sorted(d["id"] for d in queried.json()["documents"])
        # w-1 deleted via delete_batch, w-3 via delete_document; the
        # auto-id batch row and w-2 remain.
        assert "w-2" in ids
        assert "w-1" not in ids and "w-3" not in ids
        assert len(ids) == 2

        auto_table = live_write_keys["auto_table"]
        found = e2e_client.get("/api/tables", headers=platform_admin.headers)
        assert found.status_code == 200, found.text
        auto_id = next(
            item["id"] for item in found.json()["tables"]
            if item["name"] == auto_table
        )
        auto_get = e2e_client.get(
            f"/api/tables/{auto_id}/documents/a-1",
            headers=platform_admin.headers,
        )
        assert auto_get.status_code == 200, auto_get.text
        assert auto_get.json()["data"] == {"v": 9}
