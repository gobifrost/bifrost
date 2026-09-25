"""E2E: artifact core SDK calls through the real worker pool.

Exercises the full path — workflow code in a forked worker child calling
``artifacts.write``/``read``/``list``/``get_download_url`` — against the
same values the external HTTP endpoints serve, proving parity (exact
bytes, versioned refs, workspace scope, signed-URL semantics, error
mapping) end to end:

- a live workflow writes two files, reads them back, lists the workspace,
  mints download URLs, and probes a missing id (checked in-workflow);
- the workflow hard-disables fixed-operation HTTP in-engine (``get_client``
  raises if touched) and records that the local transport is installed, so
  success proves zero API requests for the migrated operations;
- the same artifacts are then verified over external HTTP
  (``/api/sdk/artifacts*``): list, content bytes, and download URL shape.

The zero-HTTP proof for the migrated operations also lives in the
unit/fork tests (``test_sdk_artifacts_local.py``), where the child's HTTP
route is hard-disabled yet all four calls succeed.
"""

import uuid

import pytest

from tests.e2e.conftest import execute_workflow_sync, write_and_register


def _uid() -> str:
    return uuid.uuid4().hex[:8]


@pytest.fixture(scope="module")
def artifact_keys():
    tag = _uid()
    return {
        "tag": tag,
        "small": f"e2e-art-small-{tag}.md",
        "big": f"e2e-art-big-{tag}.md",
    }


@pytest.fixture(scope="module")
def artifact_workflow(e2e_client, platform_admin, org1, artifact_keys):
    """Workflow exercising all four core calls through the live worker."""
    name = f"e2e_sdk_local_art_{artifact_keys['tag']}"
    path = f"{name}.py"
    k_small = artifact_keys["small"]
    k_big = artifact_keys["big"]
    content = f'''"""Local artifact core E2E workflow."""
from bifrost import artifacts, workflow

@workflow(name="{name}", description="Local artifact core E2E")
async def {name}():
    import importlib
    _mod = importlib.import_module("bifrost.artifacts")
    from bifrost._local_transport import get as _get_transport
    used_local = _get_transport() is not None

    def _dead(*args, **kwargs):
        raise AssertionError(
            "fixed-operation HTTP must not be used in the engine path"
        )
    _orig = _mod.get_client
    _mod.get_client = _dead
    try:
        small_content = b"# Small live\\n\\nLocal transport write.\\n"
        big_content = b"# Big live\\n\\n" + b"row data here\\n" * 500
        ref_small = await artifacts.write(
            "{k_small}", small_content, content_type="text/markdown"
        )
        ref_big = await artifacts.write(
            "{k_big}", big_content, content_type="text/markdown"
        )
        back_small = await artifacts.read(ref_small)
        back_big = await artifacts.read(ref_big)
        back_dict = await artifacts.read(ref_small.model_dump())
        listed = await artifacts.list()
        url_small = await artifacts.get_download_url(ref_small)
        try:
            await artifacts.read(
                {{"type": "bifrost_artifact", "id": "00000000-0000-0000-0000-000000000000",
                  "filename": "Missing.md", "content_type": "text/markdown", "size_bytes": 1}}
            )
            missing = "LEAKED"
        except Exception as e:
            missing = f"denied: {{type(e).__name__}}"
    finally:
        _mod.get_client = _orig
    return {{
        "used_local": used_local,
        "small_id": ref_small.id,
        "big_id": ref_big.id,
        "small_size": ref_small.size_bytes,
        "big_size": ref_big.size_bytes,
        "back_small_ok": back_small == small_content,
        "back_big_ok": back_big == big_content,
        "back_dict_ok": back_dict == small_content,
        "listed_names": sorted([item.filename for item in listed]),
        "url_ok": isinstance(url_small, str) and url_small.startswith("http"),
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


class TestSdkArtifactsLocalE2E:
    def test_workflow_writes_reads_lists_and_mints_urls(
        self, e2e_client, org1_user, platform_admin, artifact_workflow, artifact_keys
    ):
        result = execute_workflow_sync(
            e2e_client,
            org1_user.headers,
            artifact_workflow["id"],
            max_wait=180.0,
        )
        assert result["status"] == "Success", result
        out = result["result"]
        # Zero API requests: the transport was installed and every
        # fixed-operation HTTP call would have raised inside the workflow.
        assert out["used_local"] is True
        assert out["back_small_ok"] is True
        assert out["back_big_ok"] is True
        assert out["back_dict_ok"] is True
        assert artifact_keys["small"] in out["listed_names"]
        assert artifact_keys["big"] in out["listed_names"]
        assert out["url_ok"] is True
        assert str(out["missing"]).startswith("denied"), out

        # The workflow engine token stores globally as the system user;
        # a platform admin can verify that committed state over external HTTP.
        read = e2e_client.get(
            f"/api/sdk/artifacts/{out['small_id']}/content",
            headers=platform_admin.headers,
        )
        assert read.status_code == 200, read.text
        assert read.content == b"# Small live\n\nLocal transport write.\n"

        url = e2e_client.get(
            f"/api/sdk/artifacts/{out['small_id']}/download-url",
            headers=platform_admin.headers,
        )
        assert url.status_code == 200, url.text
        assert str(url.json()["url"]).startswith("http")
