"""Stage 2c E2E: ``integrations.upsert_mapping/delete_mapping`` and
``OAuthCredentials.refresh()`` via a live worker.

Exercises the full path — workflow code in a forked worker child calling
the three stage-2c operations — against the same seeded integration the
external HTTP endpoint serves:

- the workflow records that the worker injected its engine socket, so the
  fixed operations ride the shared client's worker-local HTTP transport;
- upsert create/update round-trips through the parent service with the
  merged-config echo, delete commits, and a missing-provider refresh
  fails loudly with the HTTP-shaped ``RuntimeError`` (refresh success
  persistence is covered by unit and forked-child socket tests with a
  mocked provider HTTP call — no real OAuth provider exists in the test
  stack);
- the same committed state is verified over external HTTP afterwards.

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
def live_mut_keys():
    tag = _uid()
    return {
        "tag": tag,
        "name": f"slive_mut_{tag}",
        "entity": f"slive-mut-entity-{tag}",
        "missing_prov": f"slive-mut-missing-prov-{tag}",
    }


@pytest.fixture(scope="module")
def live_mut_integration(e2e_client, platform_admin, live_mut_keys):
    """Seed one integration with no mappings; the workflow writes them."""
    create = e2e_client.post(
        "/api/integrations",
        headers=platform_admin.headers,
        json={"name": live_mut_keys["name"]},
    )
    assert create.status_code == 201, create.text
    integration_id = create.json()["id"]

    yield {"integration_id": integration_id}

    e2e_client.delete(
        f"/api/integrations/{integration_id}", headers=platform_admin.headers
    )


@pytest.fixture(scope="module")
def live_mut_workflow(
    e2e_client, platform_admin, org1, org2, live_mut_keys, live_mut_integration
):
    name = f"e2e_sdk_local_mut_{live_mut_keys['tag']}"
    path = f"{name}.py"
    integ_name = live_mut_keys["name"]
    entity = live_mut_keys["entity"]
    missing_prov = live_mut_keys["missing_prov"]
    content = f'''"""Stage 2c local integrations mutations E2E workflow."""
from bifrost import workflow, integrations

@workflow(name="{name}", description="Stage 2c local integrations mutations E2E")
async def {name}():
    from bifrost.client import get_engine_socket_path
    from bifrost.models import OAuthCredentials
    used_socket = get_engine_socket_path() is not None

    created = await integrations.upsert_mapping(
        "{integ_name}", scope="{org1["id"]}", entity_id="{entity}",
        entity_name="Live", config={{"region": "eu-live"}},
    )
    updated = await integrations.upsert_mapping(
        "{integ_name}", scope="{org1["id"]}", entity_id="{entity}-2",
        config={{"region": "ap-live"}},
    )
    mapping = await integrations.get_mapping("{integ_name}")
    creds = OAuthCredentials(
        connection_name="{missing_prov}", client_id=None,
        client_secret=None, authorization_url=None, token_url=None,
        scopes=[], access_token=None, refresh_token=None,
        expires_at=None,
    )
    try:
        await creds.refresh()
        refresh_out = "UNEXPECTED-SUCCESS"
    except Exception as e:
        refresh_out = f"{{type(e).__name__}}: {{e}}"
    try:
        await integrations.upsert_mapping(
            "{integ_name}", scope="{org2["id"]}", entity_id="x"
        )
        cross_org = "LEAKED"
    except Exception as e:
        cross_org = f"denied: {{type(e).__name__}}"
    deleted = await integrations.delete_mapping(
        "{integ_name}", scope="{org1["id"]}"
    )
    after = await integrations.get_mapping("{integ_name}")
    return {{
        "used_socket": used_socket,
        "created_entity": created.entity_id,
        "created_region": created.config.get("region"),
        "updated_entity": updated.entity_id,
        "updated_region": updated.config.get("region"),
        "same_row": created.id == updated.id,
        "mapping_entity": mapping.entity_id if mapping else None,
        "refresh_out": refresh_out,
        "cross_org": cross_org,
        "deleted": deleted,
        "after": after,
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


class TestSdkIntegrationMutationsLocalLiveE2E:
    def test_workflow_mutations_match_http(
        self, e2e_client, org1, org1_user, live_mut_workflow, live_mut_keys
    ):
        result = execute_workflow_sync(
            e2e_client,
            org1_user.headers,
            live_mut_workflow["id"],
            max_wait=120.0,
        )
        assert result["status"] == "Success", result
        out = result["result"]
        # The worker injected its engine socket, so the fixed operations ran
        # over the shared client's worker-local transport.
        assert out["used_socket"] is True
        assert out["created_entity"] == live_mut_keys["entity"]
        assert out["created_region"] == "eu-live"
        assert out["updated_entity"] == live_mut_keys["entity"] + "-2"
        assert out["updated_region"] == "ap-live"
        assert out["same_row"] is True
        assert out["mapping_entity"] == live_mut_keys["entity"] + "-2"
        assert str(out["refresh_out"]).startswith(
            "RuntimeError: Token refresh failed: 404"
        ), out
        assert str(out["cross_org"]).startswith("denied"), out
        assert out["deleted"] is True
        assert out["after"] is None

        # Committed state verified over external HTTP (parity): the delete
        # committed, so the mapping is gone there too.
        name = live_mut_keys["name"]
        gm = e2e_client.post(
            "/api/sdk/integrations/get_mapping",
            headers=org1_user.headers,
            json={"name": name},
        )
        assert gm.status_code == 200, gm.text
        assert gm.json() is None

        # External HTTP upsert/delete still work after the stage.
        up = e2e_client.post(
            "/api/sdk/integrations/upsert_mapping",
            headers=org1_user.headers,
            json={
                "name": name,
                "scope": org1["id"],
                "entity_id": live_mut_keys["entity"],
                "config": {"region": "eu-http"},
            },
        )
        assert up.status_code == 200, up.text
        assert up.json()["entity_id"] == live_mut_keys["entity"]
        assert up.json()["config"]["region"] == "eu-http"

        rm = e2e_client.post(
            "/api/sdk/integrations/delete_mapping",
            headers=org1_user.headers,
            json={"name": name, "scope": org1["id"]},
        )
        assert rm.status_code == 200, rm.text
        assert rm.json() == {"deleted": True}
