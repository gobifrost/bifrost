"""E2E: ``organizations`` through the real worker pool.

Exercises the full path — workflow code in a forked worker child calling
the five fixed ``bifrost.organizations`` methods — proving parity with
the external HTTP API:

- the workflow hard-disables fixed-operation HTTP in-engine
  (``bifrost.organizations.get_client`` raises if touched) and records
  that the local transport is installed, so success proves zero API
  requests for the migrated operations;
- create/get/list/update/delete round-trip inside the workflow through
  the parent-local ``shared.sdk_organizations`` service;
- the committed state (renamed, then soft-disabled) is verified over
  external HTTP.
"""

import uuid

import pytest

from tests.e2e.conftest import execute_workflow_sync, write_and_register


def _uid() -> str:
    return uuid.uuid4().hex[:8]


@pytest.fixture(scope="module")
def live_org_keys():
    tag = _uid()
    return {"tag": tag, "name": f"e2e-orgs-local-{tag}"}


@pytest.fixture(scope="module")
def live_org_workflow(e2e_client, platform_admin, org1, live_org_keys):
    """Workflow exercising the organizations facade through the live worker.

    The workflow hard-disables fixed-operation HTTP in-engine and records
    that the local transport is installed, so success proves zero API
    requests for the migrated operations. The test then verifies the
    committed state over external HTTP.
    """
    name = f"e2e_sdk_local_orgs_{live_org_keys['tag']}"
    path = f"{name}.py"
    base = live_org_keys["name"]
    content = f'''"""Local organizations E2E workflow."""
from bifrost import workflow, organizations

@workflow(name="{name}", description="Local organizations E2E")
async def {name}():
    import importlib
    _orgs = importlib.import_module("bifrost.organizations")
    from bifrost._local_transport import get as _get_transport
    used_local = _get_transport() is not None

    def _dead(*args, **kwargs):
        raise AssertionError(
            "fixed-operation HTTP must not be used in the engine path"
        )
    _orig = _orgs.get_client
    _orgs.get_client = _dead
    try:
        created = await organizations.create("{base}")
        fetched = await organizations.get(created.id)
        listed = await organizations.list()
        updated = await organizations.update(created.id, name="{base}-renamed")
        deleted = await organizations.delete(created.id)
        refetched = await organizations.get(created.id)
        relisted = await organizations.list()
    finally:
        _orgs.get_client = _orig
    return {{
        "used_local": used_local,
        "created_id": created.id,
        "created_by": created.created_by,
        "fetched_name": fetched.name,
        "listed_before": created.id in [o.id for o in listed],
        "updated_name": updated.name,
        "deleted": deleted,
        "refetched_active": refetched.is_active,
        "listed_after": created.id in [o.id for o in relisted],
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


class TestSdkOrganizationsLocalLiveE2E:
    def test_workflow_crud_commits_and_matches_http(
        self, e2e_client, org1_user, platform_admin, live_org_workflow, live_org_keys
    ):
        result = execute_workflow_sync(
            e2e_client,
            org1_user.headers,
            live_org_workflow["id"],
            max_wait=120.0,
        )
        assert result["status"] == "Success", result
        out = result["result"]
        # Zero API requests: the transport was installed and every
        # fixed-operation HTTP call would have raised inside the workflow.
        assert out["used_local"] is True
        # Non-admin initiator still gets engine superuser authority locally.
        assert out["created_by"] == "engine@bifrost.internal"
        assert out["fetched_name"] == live_org_keys["name"]
        assert out["listed_before"] is True
        assert out["updated_name"] == f"{live_org_keys['name']}-renamed"
        assert out["deleted"] is True
        assert out["refetched_active"] is False
        assert out["listed_after"] is False

        org_id = out["created_id"]
        # Committed state verified over external HTTP (parity).
        fetched = e2e_client.get(
            f"/api/organizations/{org_id}", headers=platform_admin.headers
        )
        assert fetched.status_code == 200, fetched.text
        body = fetched.json()
        assert body["name"] == f"{live_org_keys['name']}-renamed"
        assert body["is_active"] is False

        listed = e2e_client.get("/api/organizations", headers=platform_admin.headers)
        assert listed.status_code == 200, listed.text
        assert org_id not in {item["id"] for item in listed.json()}
