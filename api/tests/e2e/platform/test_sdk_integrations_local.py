"""Stage 2b E2E: ``integrations.get/list_mappings/get_mapping`` via live worker.

Exercises the full path — workflow code in a forked worker child calling
the three read operations — against the same seeded integration the
external HTTP endpoint serves, proving parity (org mapping, merged
config, entity-ID lookup, missing-entity nulls, denial) end to end:

- the workflow records that the worker injected its engine socket, so the
  fixed operations ride the shared client's worker-local HTTP transport;
- the same values served over external HTTP ``/api/sdk/integrations/*``
  match, including the cross-org denial.

The zero-HTTP proof for the migrated operations lives in the unit and
forked-child socket tests (``test_worker_sdk_http.py``,
``test_worker_sdk_http_fork.py``), where the child's network API is
unreachable yet the integration calls succeed over the worker socket.
"""

import uuid

import pytest

from tests.e2e.conftest import execute_workflow_sync, write_and_register


def _uid() -> str:
    return uuid.uuid4().hex[:8]


@pytest.fixture(scope="module")
def live_integ_keys():
    tag = _uid()
    return {
        "tag": tag,
        "name": f"slive_integ_{tag}",
        "entity": f"slive-entity-{tag}",
        "missing": f"slive_integ_missing_{tag}",
    }


@pytest.fixture(scope="module")
def live_integration(e2e_client, platform_admin, org1, live_integ_keys):
    """Seed one integration with an org1 mapping + config override."""
    create = e2e_client.post(
        "/api/integrations",
        headers=platform_admin.headers,
        json={"name": live_integ_keys["name"]},
    )
    assert create.status_code == 201, create.text
    integration_id = create.json()["id"]

    mapping = e2e_client.post(
        f"/api/integrations/{integration_id}/mappings",
        headers=platform_admin.headers,
        json={
            "organization_id": org1["id"],
            "entity_id": live_integ_keys["entity"],
            "config": {"region": "eu-live"},
        },
    )
    assert mapping.status_code == 201, mapping.text

    yield {"integration_id": integration_id}

    e2e_client.delete(
        f"/api/integrations/{integration_id}", headers=platform_admin.headers
    )


@pytest.fixture(scope="module")
def live_integ_workflow(
    e2e_client, platform_admin, org1, org2, live_integ_keys, live_integration
):
    name = f"e2e_sdk_local_integ_{live_integ_keys['tag']}"
    path = f"{name}.py"
    integ_name = live_integ_keys["name"]
    entity = live_integ_keys["entity"]
    missing = live_integ_keys["missing"]
    content = f'''"""Stage 2b local integrations reads E2E workflow."""
from bifrost import workflow, integrations

@workflow(name="{name}", description="Stage 2b local integrations reads E2E")
async def {name}():
    from bifrost.client import get_engine_socket_path
    used_socket = get_engine_socket_path() is not None

    data = await integrations.get("{integ_name}")
    mappings = await integrations.list_mappings("{integ_name}")
    mapping = await integrations.get_mapping("{integ_name}")
    by_entity = await integrations.get_mapping(
        "{integ_name}", entity_id="{entity}"
    )
    missing_get = await integrations.get("{missing}")
    missing_list = await integrations.list_mappings("{missing}")
    missing_gm = await integrations.get_mapping("{missing}")
    try:
        await integrations.get("{integ_name}", scope="{org2["id"]}")
        cross_org = "LEAKED"
    except Exception as e:
        cross_org = f"denied: {{type(e).__name__}}"
    return {{
        "used_socket": used_socket,
        "entity_id": data.entity_id,
        "region": data.config.get("region"),
        "oauth_none": data.oauth is None,
        "list_count": len(mappings),
        "list_entity": mappings[0].entity_id,
        "mapping_entity": mapping.entity_id,
        "by_entity": by_entity.entity_id,
        "missing_get": missing_get,
        "missing_list": missing_list,
        "missing_gm": missing_gm,
        "cross_org": cross_org,
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


class TestSdkIntegrationsLocalLiveE2E:
    def test_workflow_reads_match_http(
        self, e2e_client, org1_user, live_integ_workflow, live_integ_keys
    ):
        result = execute_workflow_sync(
            e2e_client,
            org1_user.headers,
            live_integ_workflow["id"],
            max_wait=120.0,
        )
        assert result["status"] == "Success", result
        out = result["result"]
        # The worker injected its engine socket, so the fixed operations ran
        # over the shared client's worker-local transport.
        assert out["used_socket"] is True
        assert out["entity_id"] == live_integ_keys["entity"]
        assert out["region"] == "eu-live"
        assert out["oauth_none"] is True
        assert out["list_count"] == 1
        assert out["list_entity"] == live_integ_keys["entity"]
        assert out["mapping_entity"] == live_integ_keys["entity"]
        assert out["by_entity"] == live_integ_keys["entity"]
        assert out["missing_get"] is None
        assert out["missing_list"] is None
        assert out["missing_gm"] is None
        assert str(out["cross_org"]).startswith("denied"), out

        # Committed state verified over external HTTP (parity).
        name = live_integ_keys["name"]
        get = e2e_client.post(
            "/api/sdk/integrations/get",
            headers=org1_user.headers,
            json={"name": name},
        )
        assert get.status_code == 200, get.text
        assert get.json()["entity_id"] == live_integ_keys["entity"]
        assert get.json()["config"]["region"] == "eu-live"

        listed = e2e_client.post(
            "/api/sdk/integrations/list_mappings",
            headers=org1_user.headers,
            json={"name": name},
        )
        assert listed.status_code == 200, listed.text
        assert len(listed.json()["items"]) == 1

        gm = e2e_client.post(
            "/api/sdk/integrations/get_mapping",
            headers=org1_user.headers,
            json={"name": name},
        )
        assert gm.status_code == 200, gm.text
        assert gm.json()["entity_id"] == live_integ_keys["entity"]
