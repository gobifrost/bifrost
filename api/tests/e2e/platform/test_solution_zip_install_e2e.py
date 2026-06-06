"""E2E: drag-and-drop ZIP install of a Solution (Tasks 11+12).

* PREVIEW (``POST /api/solutions/install/preview``) parses the workspace and
  reports its entities + config declarations without persisting anything.
* INSTALL (``POST /api/solutions/install``) deploys the bundle AND applies the
  provided config VALUES atomically under the per-install write lock — so the
  install never exists without its just-entered secret. We prove atomicity by
  reading ``/entities`` afterward: the required secret's ``value_set`` is True
  and it is NOT in ``required_configs_unset``.
"""
from __future__ import annotations

import io
import uuid
import zipfile

import pytest

pytestmark = pytest.mark.e2e


def _make_zip(slug: str) -> bytes:
    """A minimal Solution workspace zip: descriptor + a workflow (manifest +
    source) + a required secret config declaration."""
    wf_id = str(uuid.uuid4())
    files = {
        "bifrost.solution.yaml": f"slug: {slug}\nname: {slug.upper()}\nscope: global\n",
        ".bifrost/workflows.yaml": (
            "workflows:\n"
            f"  {wf_id}:\n"
            f"    id: {wf_id}\n"
            "    name: main\n"
            "    function_name: run\n"
            "    path: workflows/main.py\n"
        ),
        ".bifrost/configs.yaml": (
            "configs:\n"
            "  API_KEY:\n"
            f"    id: {uuid.uuid4()}\n"
            "    key: API_KEY\n"
            "    type: secret\n"
            "    required: true\n"
            "    description: needed\n"
            "    position: 0\n"
        ),
        "workflows/main.py": "def run(sdk):\n    return 'ok'\n",
    }
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, content in files.items():
            z.writestr(name, content)
    return buf.getvalue()


async def test_zip_install_atomic_deploy_and_values(e2e_client, platform_admin):
    headers = platform_admin.headers
    # httpx sets the multipart Content-Type itself; the auth headers carry an
    # application/json Content-Type that would otherwise override it and make the
    # server fail to parse the upload — strip it for the multipart requests.
    upload_headers = {
        k: v for k, v in headers.items() if k.lower() != "content-type"
    }
    slug = f"zip-e2e-{uuid.uuid4().hex[:8]}"
    data = _make_zip(slug)

    # PREVIEW: parse-only, nothing persisted.
    pv = e2e_client.post(
        "/api/solutions/install/preview",
        headers=upload_headers,
        files={"file": (f"{slug}.zip", data, "application/zip")},
    )
    assert pv.status_code == 200, pv.text
    body = pv.json()
    assert body["slug"] == slug
    assert len(body["workflows"]) == 1
    assert any(c["key"] == "API_KEY" for c in body["config_schemas"])

    # INSTALL: deploy + apply the secret value atomically.
    inst = e2e_client.post(
        "/api/solutions/install",
        headers=upload_headers,
        files={"file": (f"{slug}.zip", data, "application/zip")},
        data={"config_values": '{"API_KEY": "sk_x"}'},
    )
    assert inst.status_code in (200, 201), inst.text
    sid = inst.json()["id"]

    # The deployed workflow landed and the secret VALUE is set (atomic with deploy).
    ent = e2e_client.get(f"/api/solutions/{sid}/entities", headers=headers)
    assert ent.status_code == 200, ent.text
    entities = ent.json()
    assert len(entities["workflows"]) >= 1, "workflow should have deployed"

    api_key = next((c for c in entities["configs"] if c["key"] == "API_KEY"), None)
    assert api_key is not None, "API_KEY declaration should be present"
    assert api_key["value_set"] is True, "the provided secret value should be set"
    assert "API_KEY" not in entities["required_configs_unset"], (
        "a provided required value must not be reported as unset"
    )
