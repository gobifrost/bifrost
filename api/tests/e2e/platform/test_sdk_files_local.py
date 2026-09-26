"""Gate C4a E2E: files through the real worker pool.

Exercises the full path — workflow code in a forked worker child calling the
files facade (write/read/read_bytes/write_bytes/list/delete/stat/exists/
get_signed_url/search) — against the same storage the external HTTP endpoints
serve, proving parity end to end:

- the workflow records that the engine injected the worker's private socket,
  so the migrated calls rode the shared client transport rather than the
  network API (the zero-HTTP proof lives in the unit and forked-child socket
  tests, where the child's network API is dead);
- a file the workflow writes is readable with identical content over
  external HTTP, and cleanup over HTTP removes it.

A custom ``reports`` location with a granted allow-all policy keeps the test
independent of workspace superuser semantics while still exercising the real
policy-gated file service on both transports.

The grant must be function-scoped: the root autouse ``isolate_file_policies``
fixture wipes every ``FilePolicy`` row before each test, so a module-scoped
grant is deleted again during test setup and every write/read/delete builds a
default-deny 403.
"""

from __future__ import annotations

import uuid

import pytest

from tests.e2e.conftest import execute_workflow_sync, write_and_register
from tests.e2e.file_policy_helpers import grant_file_policy

pytestmark = pytest.mark.e2e

LOCATION = "reports"


def _uid() -> str:
    return uuid.uuid4().hex[:8]


def _read_http(e2e_client, headers, *, path: str, location: str, scope: str) -> str:
    response = e2e_client.post(
        "/api/files/read",
        headers=headers,
        json={
            "path": path,
            "location": location,
            "mode": "cloud",
            "binary": False,
            "scope": scope,
        },
    )
    assert response.status_code == 200, response.text
    return response.json()["content"]


def _delete_http(e2e_client, headers, *, path, location, scope) -> None:
    response = e2e_client.post(
        "/api/files/delete",
        headers=headers,
        json={
            "path": path,
            "location": location,
            "mode": "cloud",
            "expected_version": None,
            "scope": scope,
        },
    )
    # 204 deleted, 404 already gone.
    assert response.status_code in (204, 404), response.text


@pytest.fixture(scope="module")
def live_files_keys():
    tag = _uid()
    prefix = f"e2e_sdk_files_{tag}"
    return {
        "tag": tag,
        "prefix": prefix,
        "note": f"{prefix}/note.txt",
        "blob": f"{prefix}/blob.bin",
        "big": f"{prefix}/big.bin",
        "keep": f"{prefix}/keep.txt",
    }


@pytest.fixture(scope="module")
def live_files_workflow(
    e2e_client, platform_admin, org1, live_files_keys
):
    """Workflow exercising every migrated files method through the worker."""
    name = f"e2e_sdk_local_files_{live_files_keys['tag']}"
    path = f"{name}.py"
    prefix = live_files_keys["prefix"]
    note = live_files_keys["note"]
    blob = live_files_keys["blob"]
    big = live_files_keys["big"]
    keep = live_files_keys["keep"]
    content = f'''"""Gate C4a files E2E workflow."""
from bifrost import workflow, files
from bifrost.client import get_engine_socket_path

@workflow(name="{name}", description="Gate C4a files local transport E2E")
async def {name}():
    used_socket = get_engine_socket_path() is not None
    await files.write("{note}", "e2e-hello", location="{LOCATION}")
    await files.write_bytes("{blob}", b"\\x00\\x01e2e", location="{LOCATION}")
    text = await files.read("{note}", location="{LOCATION}")
    raw = await files.read_bytes("{blob}", location="{LOCATION}")
    exists = await files.exists("{note}", location="{LOCATION}")
    missing = await files.exists("{note}.gone", location="{LOCATION}")
    stat = await files.stat("{note}", location="{LOCATION}")
    listed = sorted(await files.list("{prefix}", location="{LOCATION}"))
    signed = await files.get_signed_url(
        "{blob}", method="GET", location="{LOCATION}",
        content_type="application/octet-stream")
    hits = await files.search("e2e-hello")
    # ~2 MiB binary payload over the real worker socket.
    payload = bytes((i * 7) % 256 for i in range(2 * 1024 * 1024))
    await files.write_bytes("{big}", payload, location="{LOCATION}")
    big_len = len(await files.read_bytes("{big}", location="{LOCATION}"))
    await files.delete("{note}", location="{LOCATION}")
    await files.delete("{blob}", location="{LOCATION}")
    await files.delete("{big}", location="{LOCATION}")
    gone = await files.exists("{note}", location="{LOCATION}")
    gone_big = await files.exists("{big}", location="{LOCATION}")
    await files.write("{keep}", "e2e-keep", location="{LOCATION}")
    return {{
        "used_socket": used_socket,
        "text": text,
        # Hex, not latin-1: the payload contains NUL bytes, which Postgres
        # JSONB rejects (UntranslatableCharacterError). The exact bytes are
        # still asserted by decoding the hex back.
        "raw_hex": raw.hex(),
        "exists": exists,
        "missing": missing,
        "stat_exists": stat["exists"],
        "listed": listed,
        "signed_url": signed["url"],
        "search_keys": sorted(hits.keys()),
        "big_len": big_len,
        "gone": gone,
        "gone_big": gone_big,
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


@pytest.fixture(autouse=True)
def _grant_reports_policy(e2e_client, platform_admin, org1, live_files_keys):
    """Grant the report location's allow-all policy for this test only.

    Function-scoped on purpose: ``isolate_file_policies`` (root conftest,
    autouse, function-scoped) wipes all file policies at *test setup*, so a
    module-scoped grant would be erased before the test body and every
    policy-gated write/read/delete would deny with 403.
    """
    grant_file_policy(
        e2e_client,
        platform_admin.headers,
        location=LOCATION,
        scope=org1["id"],
        allow_all=True,
    )
    yield
    for path in (
        live_files_keys["note"],
        live_files_keys["blob"],
        live_files_keys["big"],
        live_files_keys["keep"],
    ):
        _delete_http(
            e2e_client,
            platform_admin.headers,
            path=path,
            location=LOCATION,
            scope=org1["id"],
        )


class TestSdkFilesLocalLiveE2E:
    def test_workflow_files_match_http(
        self, e2e_client, platform_admin, org1, org1_user,
        live_files_workflow, live_files_keys,
    ):
        result = execute_workflow_sync(
            e2e_client,
            org1_user.headers,
            live_files_workflow["id"],
            max_wait=120.0,
        )
        assert result["status"] == "Success", result
        out = result["result"]
        # The worker injected its private socket, so the migrated calls rode
        # the shared client transport (zero-HTTP proof is in the unit and
        # forked-child tests).
        assert out["used_socket"] is True
        # Text/binary encoding parity with the HTTP routes.
        assert out["text"] == "e2e-hello"
        assert bytes.fromhex(out["raw_hex"]) == b"\x00\x01e2e"
        assert out["exists"] is True
        assert out["missing"] is False
        assert out["stat_exists"] is True
        assert out["listed"] == sorted(
            [live_files_keys["blob"], live_files_keys["note"]]
        )
        assert out["signed_url"]
        assert set(out["search_keys"]) == {
            "query", "total_matches", "files_searched", "results",
            "truncated", "search_time_ms",
        }
        # ~2 MiB payload over the real worker socket.
        assert out["big_len"] == 2 * 1024 * 1024
        # Cleanup committed: the deleted files are gone.
        assert out["gone"] is False
        assert out["gone_big"] is False

        # External HTTP parity: the retained file reads back identically.
        over_http = _read_http(
            e2e_client,
            platform_admin.headers,
            path=live_files_keys["keep"],
            location=LOCATION,
            scope=org1["id"],
        )
        assert over_http == "e2e-keep"
