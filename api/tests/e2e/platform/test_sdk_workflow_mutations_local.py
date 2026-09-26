"""Stage workflow-mutations E2E: ``workflows.execute``/``cancel`` via live worker.

Exercises the full path — workflow code in a forked worker child calling
scheduled execute plus cancel (including duplicate and missing cancels) —
against the same seeded workflow the external HTTP endpoint serves,
proving parity (durable scheduled row, queue status, 404/409 behavior)
end to end:

- the workflow records that the engine injected the worker's private
  socket, so the migrated calls rode the shared client transport (the
  zero-HTTP proof lives in the unit and forked-child socket tests, where
  the child's network API is dead);
- the same values served over external HTTP ``/api/workflows/execute``
  and ``/api/workflows/executions/{id}/cancel`` match, and the
  cancelled row stays durable when re-read over external HTTP.
"""

import uuid

import pytest

from tests.e2e.conftest import execute_workflow_sync, write_and_register


def _uid() -> str:
    return uuid.uuid4().hex[:8]


@pytest.fixture(scope="module")
def live_wf_keys():
    return {"tag": _uid()}


@pytest.fixture(scope="module")
def live_inner_workflow(e2e_client, platform_admin, org1, live_wf_keys):
    tag = live_wf_keys["tag"]
    name = f"e2e_sdk_local_wfmut_inner_{tag}"
    path = f"{name}.py"
    content = f'''"""Workflow-mutations local E2E inner workflow."""
from bifrost import workflow

@workflow(name="{name}", description="Inner target for local execute E2E")
async def {name}(n: int = 0):
    return {{"n": n}}
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


@pytest.fixture(scope="module")
def live_outer_workflow(
    e2e_client, platform_admin, org1, live_wf_keys, live_inner_workflow
):
    tag = live_wf_keys["tag"]
    name = f"e2e_sdk_local_wfmut_outer_{tag}"
    path = f"{name}.py"
    inner_id = live_inner_workflow["id"]
    content = f'''"""Workflow-mutations local E2E outer workflow."""
from bifrost import workflow, workflows
from bifrost.client import get_engine_socket_path

@workflow(name="{name}", description="Local execute/cancel E2E")
async def {name}():
    # The engine injected its private socket; the zero-HTTP proof lives in
    # the unit and forked-child socket tests where the child's network API
    # is dead by environment.
    used_socket = get_engine_socket_path() is not None
    eid = await workflows.execute("{inner_id}", {{"n": 1}}, delay_seconds=3600)
    await workflows.cancel(eid)
    try:
        await workflows.cancel(eid)
        duplicate = "LEAKED"
    except Exception as e:
        duplicate = f"{{type(e).__name__}}:{{getattr(getattr(e, 'response', None), 'status_code', None)}}"
    try:
        await workflows.cancel("00000000-0000-0000-0000-000000000000")
        missing = "LEAKED"
    except Exception as e:
        missing = f"{{type(e).__name__}}:{{getattr(getattr(e, 'response', None), 'status_code', None)}}"
    return {{
        "used_socket": used_socket,
        "execution_id": eid,
        "duplicate": duplicate,
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


class TestSdkWorkflowMutationsLocalLiveE2E:
    def test_workflow_mutations_match_http(
        self, e2e_client, org1_user, platform_admin, live_outer_workflow, live_inner_workflow
    ):
        result = execute_workflow_sync(
            e2e_client,
            org1_user.headers,
            live_outer_workflow["id"],
            max_wait=180.0,
        )
        assert result["status"] == "Success", result
        out = result["result"]
        # The engine injected its socket; the zero-HTTP proof is in the
        # unit and forked-child socket tests where the child's network API
        # is dead.
        assert out["used_socket"] is True
        assert isinstance(out["execution_id"], str) and out["execution_id"]
        assert str(out["duplicate"]).endswith(":409"), out
        assert str(out["missing"]).endswith(":404"), out

        # The cancelled row is durable when re-read over external HTTP.
        # Engine-submitted rows belong to the engine sentinel, so a
        # superuser reads them (a non-superuser may only view their own
        # rows — the same rule the HTTP path enforces).
        get = e2e_client.get(
            f"/api/executions/{out['execution_id']}",
            headers=platform_admin.headers,
        )
        assert get.status_code == 200, get.text
        assert get.json()["status"] == "Cancelled"

        # External HTTP parity: schedule, cancel, duplicate, missing.
        inner_id = live_inner_workflow["id"]
        scheduled = e2e_client.post(
            "/api/workflows/execute",
            headers=org1_user.headers,
            json={
                "workflow_id": inner_id,
                "input_data": {"n": 2},
                "delay_seconds": 3600,
            },
        )
        assert scheduled.status_code == 200, scheduled.text
        assert scheduled.json()["status"] == "Scheduled"
        http_eid = scheduled.json()["execution_id"]

        cancelled = e2e_client.post(
            f"/api/workflows/executions/{http_eid}/cancel",
            headers=org1_user.headers,
        )
        assert cancelled.status_code == 200, cancelled.text
        assert cancelled.json()["status"] == "Cancelled"

        duplicate = e2e_client.post(
            f"/api/workflows/executions/{http_eid}/cancel",
            headers=org1_user.headers,
        )
        assert duplicate.status_code == 409, duplicate.text

        missing = e2e_client.post(
            "/api/workflows/executions/00000000-0000-0000-0000-000000000000/cancel",
            headers=org1_user.headers,
        )
        assert missing.status_code == 404, missing.text
