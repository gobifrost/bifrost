"""Live E2E: the engine token installed as SDK credentials is redacted from execution output.

Drives a real workflow execution through the worker pool. The workflow:
- still succeeds end to end, proving an SDK call (config.get) works with the
  process-scoped credentials the pre-minted engine token provides;
- returns and logs the raw env var the SDK client authenticates with
  (BIFROST_ACCESS_TOKEN), proving the stored result and logs never carry it.

Companion unit coverage (no live stack) lives in
tests/unit/engine/test_engine_token_redaction.py.
"""

import re
import uuid

import pytest

from tests.e2e.conftest import execute_workflow_sync, write_and_register

# Bifrost access tokens are JWTs: three dot-separated base64url segments.
_JWT_RE = re.compile(r"[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+")


def _uid() -> str:
    return uuid.uuid4().hex[:8]


@pytest.fixture(scope="module")
def local_key():
    return f"sloc_engine_tok_{_uid()}"


@pytest.fixture(scope="module")
def local_config(e2e_client, platform_admin, org1, local_key):
    resp = e2e_client.post(
        "/api/sdk/config/set",
        headers=platform_admin.headers,
        json={
            "key": local_key,
            "value": "still-works",
            "is_secret": False,
            "scope": org1["id"],
        },
    )
    assert resp.status_code == 204, f"seed failed: {resp.text}"
    yield
    e2e_client.post(
        "/api/sdk/config/delete",
        headers=platform_admin.headers,
        json={"key": local_key, "scope": org1["id"]},
    )


@pytest.fixture(scope="module")
def engine_token_workflow(e2e_client, platform_admin, org1, local_key):
    name = f"e2e_engine_token_redaction_{_uid()}"
    path = f"{name}.py"
    content = f'''"""E2E workflow proving the SDK still works and its credential is redacted."""
import logging
import os

from bifrost import workflow, config

@workflow(name="{name}", description="Engine token redaction E2E")
async def {name}():
    # Proves the SDK client (authenticated with the installed engine token)
    # still reaches the API end to end.
    sdk_still_works = await config.get("{local_key}") == "still-works"

    token = os.environ.get("BIFROST_ACCESS_TOKEN", "")
    logging.getLogger(__name__).info("installed token: %s", token)

    return {{"sdk_still_works": sdk_still_works, "token": token}}
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


class TestEngineTokenRedactionLive:
    def test_execution_succeeds_and_token_is_redacted(
        self, e2e_client, org1_user, engine_token_workflow, local_config
    ):
        result = execute_workflow_sync(
            e2e_client,
            org1_user.headers,
            engine_token_workflow["id"],
            max_wait=120.0,
        )
        assert result["status"] == "Success", result

        out = result["result"]
        assert out["sdk_still_works"] is True
        # The result must carry the redaction marker, not a live token.
        assert out["token"] == "[REDACTED]"
        assert not _JWT_RE.search(str(out["token"]))

        logs_resp = e2e_client.get(
            f"/api/executions/{result['execution_id']}/logs",
            headers=org1_user.headers,
        )
        assert logs_resp.status_code == 200, logs_resp.text
        logs = logs_resp.json()
        joined = " ".join(log["message"] for log in logs)
        assert "installed token" in joined
        assert not _JWT_RE.search(joined)
