"""Gate C4c E2E: knowledge facade through the real worker pool.

Exercises the full path — workflow code in a forked worker child calling the
fixed ``bifrost.knowledge`` methods — and verifies the same values over
external HTTP, proving parity end to end:

- the workflow records that the engine injected the worker's private socket,
  so the migrated calls rode the shared client transport rather than the
  network API (the zero-HTTP proof lives in the unit and forked-child socket
  tests, where the child's network API is dead);
- the live run exercises the methods that need no embedding provider
  (``get``/``delete``/``delete_namespace``/``list_namespaces``) so no paid
  provider call is made. The embedding-backed methods (``store``/
  ``store_many``/``search``) are proven cross-process with a stubbed
  embedder in ``tests/unit/execution/test_sdk_knowledge_local_fork.py``,
  because the worker runs in a separate container and the existing harness
  has no injectable embedding provider.
"""

from __future__ import annotations

import uuid

import pytest

from tests.e2e.conftest import execute_workflow_sync, write_and_register

pytestmark = pytest.mark.e2e


def _uid() -> str:
    return uuid.uuid4().hex[:8]


@pytest.fixture(scope="module")
def live_knowledge_keys():
    tag = _uid()
    return {"tag": tag, "namespace": f"e2e-sdk-knowledge-{tag}"}


@pytest.fixture(scope="module")
def live_knowledge_workflow(
    e2e_client, platform_admin, org1, live_knowledge_keys
):
    """Workflow exercising the no-embedding knowledge calls via the worker."""
    name = f"e2e_sdk_local_knowledge_{live_knowledge_keys['tag']}"
    path = f"{name}.py"
    ns = live_knowledge_keys["namespace"]
    content = f'''"""Local knowledge facade E2E workflow (no embedding provider)."""
from bifrost import knowledge, workflow
from bifrost.client import get_engine_socket_path

@workflow(name="{name}", description="Local knowledge E2E")
async def {name}():
    used_socket = get_engine_socket_path() is not None
    missing = await knowledge.get("absent", namespace="{ns}")
    deleted = await knowledge.delete("absent", namespace="{ns}")
    cleared = await knowledge.delete_namespace("{ns}")
    namespaces = await knowledge.list_namespaces()
    return {{
        "used_socket": used_socket,
        "missing_is_none": missing is None,
        "deleted": deleted,
        "cleared": cleared,
        "namespace_present": any(
            n.namespace == "{ns}" for n in namespaces
        ),
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


class TestSdkKnowledgeLocalLiveE2E:
    def test_workflow_knowledge_matches_http(
        self,
        e2e_client,
        org1_user,
        platform_admin,
        org1,
        live_knowledge_workflow,
        live_knowledge_keys,
    ):
        result = execute_workflow_sync(
            e2e_client,
            org1_user.headers,
            live_knowledge_workflow["id"],
            max_wait=180.0,
        )
        assert result["status"] == "Success", result
        out = result["result"]
        # The engine injected its private socket, so the migrated calls rode
        # the shared client transport (zero-HTTP proof is in the unit and
        # forked-child tests).
        assert out["used_socket"] is True
        assert out["missing_is_none"] is True
        assert out["deleted"] is False
        assert out["cleared"] == 0
        assert out["namespace_present"] is False

        # Same results over external HTTP in the same org scope.
        ns = live_knowledge_keys["namespace"]
        scope = org1["id"]
        get = e2e_client.get(
            "/api/sdk/knowledge/get",
            headers=platform_admin.headers,
            params={"key": "absent", "namespace": ns, "scope": scope},
        )
        assert get.status_code == 404, get.text

        deleted = e2e_client.post(
            "/api/sdk/knowledge/delete",
            headers=platform_admin.headers,
            json={"key": "absent", "namespace": ns, "scope": scope},
        )
        assert deleted.status_code == 200, deleted.text
        assert deleted.json()["deleted"] is False

        cleared = e2e_client.delete(
            f"/api/sdk/knowledge/namespace/{ns}",
            headers=platform_admin.headers,
            params={"scope": scope},
        )
        assert cleared.status_code == 200, cleared.text
        assert cleared.json()["deleted_count"] == 0

        listed = e2e_client.get(
            "/api/sdk/knowledge/namespaces",
            headers=platform_admin.headers,
            params={"scope": scope, "include_global": True},
        )
        assert listed.status_code == 200, listed.text
        assert ns not in {item["namespace"] for item in listed.json()}
