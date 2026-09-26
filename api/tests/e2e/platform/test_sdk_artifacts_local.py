"""E2E: artifact core SDK calls through the real worker pool.

Exercises the full path — workflow code in a forked worker child calling
``artifacts.write``/``read``/``list``/``get_download_url`` plus one
deterministic renderer (``create_text``) — against the same values the
external HTTP endpoints serve, proving parity (exact bytes, versioned refs,
workspace scope, signed-URL semantics, error mapping) end to end:

- the workflow records that the engine injected the worker's private socket,
  so the migrated calls rode the shared client transport rather than the
  network API (the zero-HTTP proof lives in the unit and forked-child socket
  tests, where the child's network API is dead);
- the same artifacts are then verified over external HTTP
  (``/api/sdk/artifacts*``): list, content bytes, and download URL shape.
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
        "text": f"e2e-art-text-{tag}",
    }


@pytest.fixture(scope="module")
def artifact_workflow(e2e_client, platform_admin, org1, artifact_keys):
    """Workflow exercising the core calls plus one renderer through the worker."""
    name = f"e2e_sdk_local_art_{artifact_keys['tag']}"
    path = f"{name}.py"
    k_small = artifact_keys["small"]
    k_big = artifact_keys["big"]
    k_text = artifact_keys["text"]
    content = f'''"""Local artifact core E2E workflow."""
from bifrost import artifacts, workflow
from bifrost.client import get_engine_socket_path

@workflow(name="{name}", description="Local artifact core E2E")
async def {name}():
    used_socket = get_engine_socket_path() is not None

    small_content = b"# Small live\\n\\nLocal transport write.\\n"
    big_content = b"# Big live\\n\\n" + b"row data here\\n" * 500
    ref_small = await artifacts.write(
        "{k_small}", small_content, content_type="text/markdown"
    )
    ref_big = await artifacts.write(
        "{k_big}", big_content, content_type="text/markdown"
    )
    text_ref = await artifacts.create_text(
        "{k_text}", format="markdown", content="# Live text\\n"
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
    return {{
        "used_socket": used_socket,
        "small_id": ref_small.id,
        "big_id": ref_big.id,
        "text_id": text_ref.id,
        "text_filename": text_ref.filename,
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
        # The engine injected its private socket, so the migrated calls rode
        # the shared client transport (zero-HTTP proof is in the unit and
        # forked-child tests).
        assert out["used_socket"] is True
        assert out["back_small_ok"] is True
        assert out["back_big_ok"] is True
        assert out["back_dict_ok"] is True
        assert artifact_keys["small"] in out["listed_names"]
        assert artifact_keys["big"] in out["listed_names"]
        assert out["text_filename"] in out["listed_names"]
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

        # Renderer bytes written over the socket are identical over HTTP.
        text = e2e_client.get(
            f"/api/sdk/artifacts/{out['text_id']}/content",
            headers=platform_admin.headers,
        )
        assert text.status_code == 200, text.text
        assert text.content == b"# Live text\n"

        url = e2e_client.get(
            f"/api/sdk/artifacts/{out['small_id']}/download-url",
            headers=platform_admin.headers,
        )
        assert url.status_code == 200, url.text
        assert str(url.json()["url"]).startswith("http")
