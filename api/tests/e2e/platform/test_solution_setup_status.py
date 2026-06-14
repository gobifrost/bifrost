"""E2E: GET /api/solutions/{id}/setup — required-config setup status.

Verifies that the endpoint returns a ``setup_complete`` flag and an ``items``
list that includes each SolutionConfigSchema declaration paired with whether a
matching Config value is set in the install's org scope.

Also covers Task 7: install recomputes ``setup_complete`` from required config
declarations so an install with no provided value is marked incomplete and one
that fills the required config is marked complete.
"""
from __future__ import annotations

import io
import uuid
import zipfile

import pytest

pytestmark = pytest.mark.e2e


def _make_required_config_zip(slug: str, *, key: str = "api_key") -> bytes:
    """Minimal Solution workspace zip with one required config declaration.

    Mirrors the ``_make_zip`` helper from ``test_solution_zip_install_e2e.py``:
    descriptor + workflow manifest + source + a required config in
    ``.bifrost/configs.yaml``.  The config ``required: true`` is the critical
    field that makes the setup-status endpoint and ``compute_setup_status``
    return ``setup_complete=False`` when no matching Config value is set.
    """
    wf_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"{slug}/workflows/main"))
    cfg_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"{slug}/configs/{key}"))
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
            f"  {key}:\n"
            f"    id: {cfg_id}\n"
            f"    key: {key}\n"
            "    type: string\n"
            "    required: true\n"
            "    description: required config for setup-status test\n"
            "    position: 0\n"
        ),
        "workflows/main.py": "def run(sdk):\n    return 'ok'\n",
    }
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, content in files.items():
            z.writestr(name, content)
    return buf.getvalue()


async def test_setup_status_lists_required_unset_configs(
    e2e_client, platform_admin, make_solution_with_required_config
):
    sol = await make_solution_with_required_config(key="api_key", required=True)
    headers = platform_admin.headers
    resp = e2e_client.get(f"/api/solutions/{sol['id']}/setup", headers=headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["setup_complete"] is False
    keys = {i["key"]: i for i in body["items"]}
    assert keys["api_key"]["is_set"] is False
    assert keys["api_key"]["required"] is True
    # The declaration's default rides along for the setup wizard.
    assert keys["api_key"]["default"] == "a-default"


async def test_setup_status_marks_set_config_complete(
    e2e_client, platform_admin, make_solution_with_required_config
):
    sol = await make_solution_with_required_config(
        key="api_key", required=True, set_value=True
    )
    headers = platform_admin.headers
    resp = e2e_client.get(f"/api/solutions/{sol['id']}/setup", headers=headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["setup_complete"] is True
    keys = {i["key"]: i for i in body["items"]}
    assert keys["api_key"]["is_set"] is True
    assert keys["api_key"]["required"] is True


async def test_setup_status_no_declarations_is_complete(
    e2e_client, platform_admin, make_solution_without_configs
):
    """Vacuous-true guard: an install with no config declarations is complete."""
    sol = await make_solution_without_configs()
    headers = platform_admin.headers
    resp = e2e_client.get(f"/api/solutions/{sol['id']}/setup", headers=headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["setup_complete"] is True
    assert body["items"] == []


async def test_install_with_set_value_flips_setup_complete(e2e_client, platform_admin):
    """Task 7: install recomputes and persists setup_complete after deploy.

    * Install WITHOUT the required value → setup_complete must be False in the
      install RESPONSE (the persisted ORM column, not a live recompute).
    * Install WITH the required value → setup_complete must be True.

    Before Task 7 the setup_complete column was never written by install_zip, so
    it stayed at its default (True) regardless of whether required configs were
    satisfied — making the first assertion fail.
    """
    headers = platform_admin.headers
    # httpx sets the multipart Content-Type itself; the auth headers carry an
    # application/json Content-Type that would otherwise override it — strip it.
    upload_headers = {k: v for k, v in headers.items() if k.lower() != "content-type"}

    slug_no_val = f"setup-sc-noval-{uuid.uuid4().hex[:8]}"
    zip_no_val = _make_required_config_zip(slug_no_val, key="api_key")

    # Install WITHOUT the value — required config unset → incomplete.
    # The install RESPONSE carries the persisted setup_complete column value.
    r1 = e2e_client.post(
        "/api/solutions/install",
        headers=upload_headers,
        files={"file": ("s.zip", zip_no_val, "application/zip")},
    )
    assert r1.status_code in (200, 201), r1.text
    assert r1.json()["setup_complete"] is False, (
        "install with required config unset must persist setup_complete=False; "
        f"got: {r1.json()}"
    )

    # Install WITH the value — required config set → complete.
    slug_with_val = f"setup-sc-val-{uuid.uuid4().hex[:8]}"
    zip_with_val = _make_required_config_zip(slug_with_val, key="api_key")

    r2 = e2e_client.post(
        "/api/solutions/install",
        headers=upload_headers,
        files={"file": ("s.zip", zip_with_val, "application/zip")},
        data={"config_values": '{"api_key": "xyz"}'},
    )
    assert r2.status_code in (200, 201), r2.text
    assert r2.json()["setup_complete"] is True, (
        "install with required config value provided must persist setup_complete=True; "
        f"got: {r2.json()}"
    )
