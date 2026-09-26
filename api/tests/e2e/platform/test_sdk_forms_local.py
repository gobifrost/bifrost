"""Gate C5c E2E: form reads through the real worker pool.

Exercises the full path — workflow code in a forked worker child calling the
fixed ``bifrost.forms.list`` / ``bifrost.forms.get`` — and verifies the same
form over external HTTP, proving parity end to end:

- the workflow records that the engine injected the worker's private socket,
  so the migrated calls rode the shared client transport rather than the
  network API (the zero-HTTP proof lives in the unit and forked-child socket
  tests, where the child's network API is dead);
- the engine child (token-equivalent superuser, like the HTTP engine-token
  path) sees the org-scoped form the test created, reads its detail, and maps
  a missing id to ``ValueError``;
- the same form is re-read over external HTTP ``/api/forms`` and
  ``/api/forms/{form_id}`` and matches by id/name.
"""

from __future__ import annotations

import uuid
from uuid import uuid4

import pytest

from tests.e2e.conftest import execute_workflow_sync, write_and_register

pytestmark = pytest.mark.e2e


def _uid() -> str:
    return uuid.uuid4().hex[:8]


@pytest.fixture(scope="module")
def live_forms_keys():
    tag = _uid()
    return {"tag": tag, "form_name": f"e2e-sdk-forms-{tag}"}


@pytest.fixture(scope="module")
def live_form(e2e_client, platform_admin, org1, live_forms_keys):
    """Create an org-scoped form the workflow reads back over the socket."""
    resp = e2e_client.post(
        "/api/forms",
        headers=platform_admin.headers,
        json={
            "name": live_forms_keys["form_name"],
            "description": "Gate C5c forms local E2E",
            "form_schema": {"fields": []},
            "access_level": "authenticated",
            "organization_id": org1["id"],
        },
    )
    assert resp.status_code == 201, resp.text
    form = resp.json()
    yield form
    e2e_client.delete(
        f"/api/forms/{form['id']}",
        headers=platform_admin.headers,
    )


@pytest.fixture(scope="module")
def live_forms_workflow(
    e2e_client, platform_admin, org1, live_forms_keys, live_form
):
    """Workflow exercising forms.list/forms.get through the live worker."""
    name = f"e2e_sdk_local_forms_{live_forms_keys['tag']}"
    path = f"{name}.py"
    form_id = live_form["id"]
    form_name = live_forms_keys["form_name"]
    content = f'''"""Local forms E2E workflow."""
from bifrost import forms, workflow
from bifrost.client import get_engine_socket_path

@workflow(name="{name}", description="Local forms E2E")
async def {name}():
    # The engine injected its private socket; the zero-HTTP proof lives in
    # the unit and forked-child socket tests where the child's network API
    # is dead by environment.
    used_socket = get_engine_socket_path() is not None
    listed = await forms.list()
    saw = any(f.name == "{form_name}" for f in listed)
    detail = await forms.get("{form_id}")
    try:
        await forms.get("{uuid4()}")
        missing = "LEAKED"
    except ValueError:
        missing = "ValueError"
    except Exception as e:
        missing = type(e).__name__
    return {{
        "used_socket": used_socket,
        "saw": saw,
        "detail_id": detail.id,
        "detail_name": detail.name,
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


class TestSdkFormsLocalLiveE2E:
    def test_workflow_forms_match_http(
        self, e2e_client, org1_user, platform_admin, live_forms_workflow, live_form
    ):
        result = execute_workflow_sync(
            e2e_client,
            org1_user.headers,
            live_forms_workflow["id"],
            max_wait=120.0,
        )
        assert result["status"] == "Success", result
        out = result["result"]
        # The worker injected its private socket, so the migrated calls rode
        # the shared client transport (zero-HTTP proof is in the unit and
        # forked-child tests).
        assert out["used_socket"] is True
        assert out["saw"] is True, out
        assert out["detail_id"] == live_form["id"], out
        assert out["detail_name"] == live_form["name"], out
        assert out["missing"] == "ValueError", out

        # External HTTP parity: the same form over the list and detail routes.
        listed = e2e_client.get(
            "/api/forms",
            headers=platform_admin.headers,
        )
        assert listed.status_code == 200, listed.text
        assert live_form["id"] in {f["id"] for f in listed.json()}

        detail = e2e_client.get(
            f"/api/forms/{live_form['id']}",
            headers=platform_admin.headers,
        )
        assert detail.status_code == 200, detail.text
        assert detail.json()["id"] == live_form["id"]
        assert detail.json()["name"] == live_form["name"]

        missing = e2e_client.get(
            f"/api/forms/{uuid4()}",
            headers=platform_admin.headers,
        )
        assert missing.status_code == 404, missing.text
