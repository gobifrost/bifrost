"""Stage execution-reads E2E: reads via live worker.

Exercises the full path — workflow code in a forked worker child calling
``workflows.list``, ``executions.list`` (filtered plus keyset-paged),
and ``executions.get`` (own running execution, a foreign row, and a
missing id) — proving parity with the same values served over external
HTTP ``/api/workflows`` and ``/api/executions``:

- the workflow records that the engine injected the worker's private
  socket, so the migrated calls rode the shared client transport (the
  zero-HTTP proof lives in the unit and forked-child socket tests, where
  the child's network API is dead);
- the same rows re-read over external HTTP match (summaries, detail,
  continuation tokens, 404 on missing);
- the engine child's superuser visibility matches the HTTP engine path:
  a foreign row readable locally is 403 for a non-superuser over HTTP.

"""

import uuid

import pytest

from tests.e2e.conftest import execute_workflow_sync, write_and_register


def _uid() -> str:
    return uuid.uuid4().hex[:8]


@pytest.fixture(scope="module")
def live_keys():
    return {"tag": _uid()}


@pytest.fixture(scope="module")
def live_outer_workflow(e2e_client, platform_admin, org1, live_keys):
    tag = live_keys["tag"]
    name = f"e2e_sdk_local_execreads_outer_{tag}"
    path = f"{name}.py"
    content = f'''"""Execution-reads local E2E outer workflow."""
import asyncio
from bifrost import workflow, workflows, executions
from bifrost.client import get_engine_socket_path

@workflow(name="{name}", description="Local execution reads E2E")
async def {name}(foreign_id: str):
    from bifrost._context import get_execution_context
    # The engine injected its private socket; the zero-HTTP proof lives in
    # the unit and forked-child socket tests where the child's network API
    # is dead by environment.
    used_socket = get_engine_socket_path() is not None
    _wf_list = await workflows.list()
    saw_self = any(_w.name == "{name}" for _w in _wf_list)
    own_id = get_execution_context().execution_id
    _page1, _detail, _foreign = await asyncio.gather(
        executions.list(limit=1),
        executions.get(own_id),
        executions.get(foreign_id),
    )
    _page2 = await executions.list(limit=1, continuation_token=_page1.continuation_token)
    _named = await executions.list(workflow_name="{name}")
    try:
        await executions.get("00000000-0000-0000-0000-000000000000")
        missing = "LEAKED"
    except ValueError:
        missing = "ValueError"
    except Exception as e:
        missing = type(e).__name__
    return {{
        "used_socket": used_socket,
        "saw_self": saw_self,
        "own_id": own_id,
        "page1_count": len(_page1),
        "page1_token": _page1.continuation_token,
        "page2_count": len(_page2),
        "detail_id": _detail.execution_id,
        "detail_name": _detail.workflow_name,
        "foreign_id": _foreign.execution_id,
        "named_saw_own": any(_e.execution_id == own_id for _e in _named),
        "missing": missing,
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


class TestSdkExecutionReadsLocalLiveE2E:
    def test_execution_reads_match_http(
        self, e2e_client, org1_user, platform_admin, org1, live_outer_workflow
    ):
        # A foreign row: scheduled by the platform admin, so a
        # non-superuser cannot view it over HTTP — while the engine
        # child (superuser, like the HTTP engine-token path) can.
        scheduled = e2e_client.post(
            "/api/workflows/execute",
            headers=platform_admin.headers,
            json={
                "workflow_id": live_outer_workflow["id"],
                "input_data": {},
                "delay_seconds": 3600,
            },
        )
        assert scheduled.status_code == 200, scheduled.text
        foreign_id = scheduled.json()["execution_id"]

        result = execute_workflow_sync(
            e2e_client,
            org1_user.headers,
            live_outer_workflow["id"],
            input_data={"foreign_id": foreign_id},
            max_wait=180.0,
        )
        assert result["status"] == "Success", result
        out = result["result"]
        # The engine injected its socket; the zero-HTTP proof is in the
        # unit and forked-child socket tests where the child's network API
        # is dead.
        assert out["used_socket"] is True
        assert out["saw_self"] is True, out
        # Filtered + keyset-paged list agrees with itself across pages.
        assert out["page1_count"] == 1, out
        assert out["page1_token"], out
        assert out["page2_count"] == 1, out
        assert out["named_saw_own"] is True, out
        # Own running execution plus the foreign row resolve locally.
        assert out["detail_id"] == out["own_id"], out
        assert out["detail_name"] == live_outer_workflow["name"], out
        assert out["foreign_id"] == foreign_id, out
        assert out["missing"] == "ValueError", out

        # External HTTP parity: the same rows, tokens, and 404.
        listed = e2e_client.get(
            "/api/executions",
            headers=platform_admin.headers,
            params={"limit": 1},
        )
        assert listed.status_code == 200, listed.text
        assert listed.json()["continuation_token"], listed.text

        detail = e2e_client.get(
            f"/api/executions/{out['own_id']}",
            headers=platform_admin.headers,
        )
        assert detail.status_code == 200, detail.text
        assert detail.json()["execution_id"] == out["own_id"]
        assert detail.json()["workflow_name"] == live_outer_workflow["name"]

        missing = e2e_client.get(
            "/api/executions/00000000-0000-0000-0000-000000000000",
            headers=platform_admin.headers,
        )
        assert missing.status_code == 404, missing.text

        # Hidden cross-org outcome: the non-superuser submitter cannot
        # view the admin-scheduled foreign row over HTTP (403), while
        # the engine child read it locally (superuser engine path).
        denied = e2e_client.get(
            f"/api/executions/{foreign_id}",
            headers=org1_user.headers,
        )
        assert denied.status_code == 403, denied.text

        e2e_client.post(
            f"/api/workflows/executions/{foreign_id}/cancel",
            headers=platform_admin.headers,
        )
