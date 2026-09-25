"""E2E: ``ai.complete`` and ``ai.model_info`` through the real worker pool.

Exercises the full path — workflow code in a forked worker child calling
the fixed ``bifrost.ai`` operations — proving parity with the external
HTTP API without reaching a real provider (no paid external API):

- the workflow hard-disables fixed-operation HTTP in-engine
  (``bifrost.ai.get_client`` raises if touched) and records that the
  local transport is installed, so success proves zero API requests for
  the migrated operations;
- ``ai.complete`` with file inputs but no user message fails before any
  provider call on both transports (503 ``SdkAIError`` detail), and the
  local ``RuntimeError`` text matches the external HTTP detail exactly;
- ``ai.get_model_info`` round-trips inside the workflow through the
  parent-local ``shared.sdk_ai`` service and matches the external HTTP
  body (or its 404 detail) exactly.
"""

import uuid

import pytest

from tests.e2e.conftest import execute_workflow_sync, write_and_register


def _uid() -> str:
    return uuid.uuid4().hex[:8]


@pytest.fixture(scope="module")
def live_ai_keys():
    return {"tag": _uid()}


@pytest.fixture(scope="module")
def live_ai_workflow(e2e_client, platform_admin, org1, live_ai_keys):
    """Workflow exercising ai.complete/ai.model_info through the live worker."""
    name = f"e2e_sdk_local_ai_{live_ai_keys['tag']}"
    path = f"{name}.py"
    content = f'''"""Local ai.complete/model_info E2E workflow."""
from bifrost import workflow

@workflow(name="{name}", description="Local ai E2E")
async def {name}():
    import importlib
    from bifrost._local_transport import get as _get_transport
    used_local = _get_transport() is not None

    _amod = importlib.import_module("bifrost.ai")
    def _dead(*args, **kwargs):
        raise AssertionError(
            "fixed-operation HTTP must not be used in the engine path"
        )
    _orig = _amod.get_client
    _amod.get_client = _dead
    try:
        from bifrost.ai import ai
        from bifrost.models import AIInputFile
        try:
            await ai.complete(
                messages=[{{"role": "system", "content": "sys"}}],
                files=[AIInputFile(
                    filename="a.txt",
                    content_type="text/plain",
                    data=b"x",
                )],
            )
            complete_error = None
        except RuntimeError as e:
            complete_error = str(e)
        try:
            info = await ai.get_model_info()
            model_info = {{"ok": True, "body": info}}
        except RuntimeError as e:
            model_info = {{"ok": False, "error": str(e)}}
    finally:
        _amod.get_client = _orig
    return {{
        "used_local": used_local,
        "complete_error": complete_error,
        "model_info": model_info,
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


class TestSdkAILocalLiveE2E:
    def test_workflow_ai_matches_http(
        self,
        e2e_client,
        org1_user,
        platform_admin,
        org1,
        live_ai_workflow,
    ):
        import base64

        result = execute_workflow_sync(
            e2e_client,
            org1_user.headers,
            live_ai_workflow["id"],
            max_wait=120.0,
        )
        assert result["status"] == "Success", result
        out = result["result"]
        # Zero API requests: the transport was installed and every
        # fixed-operation HTTP call would have raised inside the workflow.
        assert out["used_local"] is True

        # The file-inputs/user-message rule fails before any provider
        # call: the local RuntimeError text must match the HTTP detail.
        http_complete = e2e_client.post(
            "/api/sdk/ai/complete",
            headers=org1_user.headers,
            json={
                "messages": [{"role": "system", "content": "sys"}],
                "input_files": [
                    {
                        "filename": "a.txt",
                        "content_type": "text/plain",
                        "data_base64": base64.b64encode(b"x").decode(),
                    }
                ],
            },
        )
        assert http_complete.status_code == 503, http_complete.text
        assert out["complete_error"] == (
            f"AI completion failed: {http_complete.json()['detail']}"
        )

        # Model info matches exactly on both transports (body or 404 text).
        http_info = e2e_client.get(
            "/api/sdk/ai/info", headers=org1_user.headers
        )
        if http_info.status_code == 200:
            assert out["model_info"] == {"ok": True, "body": http_info.json()}
            assert set(out["model_info"]["body"]) == {"provider", "model"}
        else:
            assert http_info.status_code == 404, http_info.text
            assert out["model_info"] == {
                "ok": False,
                "error": (
                    "Failed to get AI model info: "
                    f"{http_info.json()['detail']}"
                ),
            }
