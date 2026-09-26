"""Gate C1 E2E: config CRUD through the real worker pool.

Exercises the full path — workflow code in a forked worker child calling
``config.get/set/list/delete`` — against the same seeded values the external
HTTP endpoint serves, proving parity (scope, cascade, typed coercion, secret
decryption/redaction, null/default, error mapping) end to end:

- one-shot workflow reads typed values, a missing key with default, and a
  secret (checked in-workflow, since the engine redacts secrets from
  outputs);
- a second workflow mutates through set/list/delete and commits;
- the same values served over external HTTP ``/api/sdk/config/*`` match;
- a cross-org scope override attempted by a non-bypass caller surfaces a
  denial instead of data.

The zero-HTTP proof for the migrated operations lives in the unit and
forked-child socket tests (``test_worker_sdk_http.py``,
``test_worker_sdk_http_fork.py``), where the child's network API is
unreachable yet the config calls succeed over the worker socket.
"""

import uuid

import pytest

from tests.e2e.conftest import execute_workflow_sync, write_and_register


def _uid() -> str:
    return uuid.uuid4().hex[:8]


@pytest.fixture(scope="module")
def local_keys():
    tag = _uid()
    return {
        "tag": tag,
        "string": f"sloc_str_{tag}",
        "int": f"sloc_int_{tag}",
        "bool": f"sloc_bool_{tag}",
        "json": f"sloc_json_{tag}",
        "secret": f"sloc_sec_{tag}",
        "missing": f"sloc_missing_{tag}",
    }


@pytest.fixture(scope="module")
def local_configs(e2e_client, platform_admin, org1, local_keys):
    """Seed org1-scoped configs of every supported type."""
    seeded = [
        (local_keys["string"], "hello-local", False),
        (local_keys["int"], 42, False),
        (local_keys["bool"], True, False),
        (local_keys["json"], {"nested": [1, 2]}, False),
        (local_keys["secret"], "shh-local-secret", True),
    ]
    for key, value, is_secret in seeded:
        resp = e2e_client.post(
            "/api/sdk/config/set",
            headers=platform_admin.headers,
            json={
                "key": key,
                "value": value,
                "is_secret": is_secret,
                "scope": org1["id"],
            },
        )
        assert resp.status_code == 204, f"seed {key} failed: {resp.text}"

    yield

    for key, _, _ in seeded:
        e2e_client.post(
            "/api/sdk/config/delete",
            headers=platform_admin.headers,
            json={"key": key, "scope": org1["id"]},
        )


@pytest.fixture(scope="module")
def local_workflow(e2e_client, platform_admin, org1, org2, local_keys):
    """Workflow reading every seeded value plus denial probing."""
    name = f"e2e_sdk_local_cfg_{local_keys['tag']}"
    path = f"{name}.py"
    content = f'''"""Stage 1 local config.get E2E workflow."""
from bifrost import workflow, config

@workflow(name="{name}", description="Stage 1 local config.get E2E")
async def {name}():
    out = {{}}
    out["string"] = await config.get("{local_keys["string"]}")
    out["int"] = await config.get("{local_keys["int"]}")
    out["bool"] = await config.get("{local_keys["bool"]}")
    out["json"] = await config.get("{local_keys["json"]}")
    out["missing"] = await config.get("{local_keys["missing"]}", default="fallback")
    # Secrets are redacted from outputs, so verify in-workflow.
    out["secret_ok"] = await config.get("{local_keys["secret"]}") == "shh-local-secret"
    try:
        await config.get("{local_keys["string"]}", scope="{org2["id"]}")
        out["cross_org"] = "LEAKED"
    except Exception as e:
        out["cross_org"] = f"denied: {{type(e).__name__}}"
    return out
'''
    registered = write_and_register(
        e2e_client,
        platform_admin.headers,
        path,
        content,
        name,
        organization_id=org1["id"],
    )
    # Let org members execute it (same pattern as the scope-execution suite).
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


class TestSdkConfigLocalE2E:
    def test_workflow_reads_all_types_and_default(
        self, e2e_client, org1_user, local_workflow, local_configs
    ):
        result = execute_workflow_sync(
            e2e_client,
            org1_user.headers,
            local_workflow["id"],
            max_wait=120.0,
        )
        assert result["status"] == "Success", result
        out = result["result"]
        assert out["string"] == "hello-local"
        assert out["int"] == 42
        assert out["bool"] is True
        assert out["json"] == {"nested": [1, 2]}
        assert out["missing"] == "fallback"
        assert out["secret_ok"] is True
        assert str(out["cross_org"]).startswith("denied"), out

    def test_external_http_serves_same_values(
        self, e2e_client, org1_user, local_keys, local_configs
    ):
        """External SDK callers still use HTTP and see identical values."""
        for key, expected in [
            (local_keys["string"], "hello-local"),
            (local_keys["int"], 42),
            (local_keys["bool"], True),
            (local_keys["json"], {"nested": [1, 2]}),
        ]:
            resp = e2e_client.post(
                "/api/sdk/config/get",
                headers=org1_user.headers,
                json={"key": key},
            )
            assert resp.status_code == 200, resp.text
            body = resp.json()
            assert body["value"] == expected, (key, body)

        missing = e2e_client.post(
            "/api/sdk/config/get",
            headers=org1_user.headers,
            json={"key": local_keys["missing"]},
        )
        assert missing.status_code == 200
        assert missing.json() is None


@pytest.fixture(scope="module")
def live_mutation_keys():
    tag = _uid()
    return {
        "tag": tag,
        "small": f"slive_small_{tag}",
        "secret": f"slive_secret_{tag}",
        "big": f"slive_big_{tag}",
    }


@pytest.fixture(scope="module")
def live_mutation_workflow(e2e_client, platform_admin, org1, live_mutation_keys):
    """Workflow exercising set/get/list/delete through the live worker.

    The engine child reaches config over the worker's injected socket (the
    shared client transport); the zero-HTTP proof lives in the unit and
    forked-child socket tests. This test proves the live worker path commits
    and that external HTTP serves the same state.
    """
    name = f"e2e_sdk_local_mut_{live_mutation_keys['tag']}"
    path = f"{name}.py"
    k_small = live_mutation_keys["small"]
    k_secret = live_mutation_keys["secret"]
    k_big = live_mutation_keys["big"]
    content = f'''"""Stage 2a local config set/list/delete E2E workflow."""
from bifrost import workflow, config

@workflow(name="{name}", description="Stage 2a local config mutation E2E")
async def {name}():
    await config.set("{k_small}", "small-live")
    await config.set("{k_secret}", "live-secret", is_secret=True)
    await config.set("{k_big}", "z" * 5000)
    v_small = await config.get("{k_small}")
    v_big = await config.get("{k_big}")
    listed = await config.list()
    d1 = await config.delete("{k_small}")
    d1_again = await config.delete("{k_small}")
    missing = await config.get("{k_small}", default="gone")
    return {{
        "v_small": v_small,
        "big_len": len(v_big),
        "listed_small": listed["{k_small}"],
        "listed_secret": listed["{k_secret}"],
        "d1": d1,
        "d1_again": d1_again,
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


class TestSdkConfigMutationLiveE2E:
    def test_workflow_mutations_commit_and_match_http(
        self, e2e_client, org1_user, live_mutation_workflow, live_mutation_keys
    ):
        result = execute_workflow_sync(
            e2e_client,
            org1_user.headers,
            live_mutation_workflow["id"],
            max_wait=120.0,
        )
        assert result["status"] == "Success", result
        out = result["result"]
        assert out["v_small"] == "small-live"
        assert out["big_len"] == 5000
        assert out["listed_small"] == "small-live"
        assert out["listed_secret"] == "[SECRET]"
        assert out["d1"] is True
        assert out["d1_again"] is False
        assert out["missing"] == "gone"

        # Committed state verified over external HTTP (parity).
        big = e2e_client.post(
            "/api/sdk/config/get",
            headers=org1_user.headers,
            json={"key": live_mutation_keys["big"]},
        )
        assert big.status_code == 200, big.text
        assert big.json()["value"] == "z" * 5000

        secret = e2e_client.post(
            "/api/sdk/config/get",
            headers=org1_user.headers,
            json={"key": live_mutation_keys["secret"]},
        )
        assert secret.status_code == 200, secret.text
        assert secret.json()["value"] == "live-secret"

        listed = e2e_client.post(
            "/api/sdk/config/list",
            headers=org1_user.headers,
            json={},
        )
        assert listed.status_code == 200, listed.text
        body = listed.json()
        assert body[live_mutation_keys["big"]] == "z" * 5000
        assert body[live_mutation_keys["secret"]] == "[SECRET]"
        assert live_mutation_keys["small"] not in body

        gone = e2e_client.post(
            "/api/sdk/config/get",
            headers=org1_user.headers,
            json={"key": live_mutation_keys["small"]},
        )
        assert gone.status_code == 200
        assert gone.json() is None

        # Cleanup leaves no residue for later runs.
        for key in (
            live_mutation_keys["big"],
            live_mutation_keys["secret"],
        ):
            resp = e2e_client.post(
                "/api/sdk/config/delete",
                headers=org1_user.headers,
                json={"key": key},
            )
            assert resp.status_code == 200, resp.text
