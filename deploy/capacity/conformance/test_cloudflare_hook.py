"""Unit tests for Cloudflare's capacity-conformance observation hook."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import re
import urllib.error

import pytest


HOOK_PATH = Path(__file__).parent / "hooks" / "cloudflare.py"
SPEC = importlib.util.spec_from_file_location("cloudflare_capacity_hook", HOOK_PATH)
assert SPEC is not None and SPEC.loader is not None
cloudflare = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(cloudflare)


WORKER_ID = "1b1cd6e3-b27e-4d38-9fcd-806a3d56b336"
OTHER_WORKER_ID = "bc92eaf8-1f25-4c07-99fc-6c237891cf50"
ACTOR_ID = "0b3d6ac93c56f20bb466430dfe89b23bf9e3680a037209a6a9906feb1e1f07c9"
INSTANCE_ID = "d03e8a53-48a7-4c6d-bfc5-6d76b54f0f9d"
PLACEMENT_ID = "c2f2af4e-7004-a3b8-e184-df4154283c33"


def _telemetry(*, starts: list[str] | None = None, worker_id: str = WORKER_ID) -> dict:
    starts = starts or ["c0e0906b-c984-47fd-89e2-9b473c72c250"]
    return {
        "instances": [
            {
                "id": INSTANCE_ID,
                "current_placement": {
                    "id": PLACEMENT_ID,
                    "deployment_id": INSTANCE_ID,
                    "durable_object_actor_id": ACTOR_ID,
                    "status": {"container_status": "running"},
                    "events": [
                        {"id": event_id, "time": "2026-11-14T21:04:18.270Z", "name": "ContainerStarted"}
                        for event_id in starts
                    ] + [{"id": "vm-start", "time": "2026-11-14T20:55:59.552Z", "name": "VMStarted"}],
                },
            }
        ],
        "durable_objects": [
            {
                "id": ACTOR_ID,
                "name": worker_id,
                "deployment_id": INSTANCE_ID,
                "placement_id": PLACEMENT_ID,
            }
        ],
    }


@pytest.fixture
def hook_state(monkeypatch, tmp_path):
    monkeypatch.setattr(cloudflare, "STATE_PATH", str(tmp_path / "cloudflare-launches.json"))


@pytest.fixture
def mutation_environment(monkeypatch, tmp_path):
    source = tmp_path / "provider"
    source.mkdir()
    (source / "capacity.mjs").write_text("export default {};\n", encoding="utf-8")
    (source / "capacity-core.mjs").write_text("export const core = true;\n", encoding="utf-8")
    monkeypatch.setenv("CLOUDFLARE_API_TOKEN", "the-admin-token-must-not-be-uploaded")
    monkeypatch.setenv("CAPACITY_TOKEN", "the-capacity-token-must-not-be-uploaded")
    monkeypatch.setenv("CF_ACCOUNT_ID", "account-123")
    monkeypatch.setenv("CF_SCRIPT_NAME", "capacity-provider")
    monkeypatch.setenv("CF_PROVIDER_SOURCE", str(source))
    monkeypatch.setenv("CF_HOOK_ALLOW_MUTATIONS", "1")
    return source


def _worker_settings(scope: str = "demo") -> dict:
    return {
        "bindings": [
            {"name": "WORKER_LEDGER", "type": "durable_object_namespace", "class_name": "WorkerLedger"},
            {"name": "SCOPE_REGISTRY", "type": "durable_object_namespace", "class_name": "ScopeRegistry"},
            {"name": "WORKER_CONTAINER", "type": "durable_object_namespace", "class_name": "WorkerContainer"},
            {"name": "SCOPE", "type": "plain_text", "text": scope},
            {"name": "WORKER_VERSION", "type": "plain_text", "text": "provider2"},
        ],
        "compatibility_date": "2026-11-14",
        "compatibility_flags": ["nodejs_compat"],
    }


def _multipart_metadata(body: bytes) -> dict:
    match = re.search(
        rb'Content-Disposition: form-data; name="metadata"\r\nContent-Type: application/json\r\n\r\n(.+?)\r\n--',
        body,
        re.DOTALL,
    )
    assert match is not None
    return json.loads(match.group(1))


def test_observe_maps_worker_through_durable_object_and_container_start(monkeypatch, hook_state):
    monkeypatch.setattr(cloudflare, "_instances", lambda: _telemetry())

    observed = cloudflare.observe({"worker_id": WORKER_ID})

    assert observed == {
        "launches": [
            f"{INSTANCE_ID}:{PLACEMENT_ID}:c0e0906b-c984-47fd-89e2-9b473c72c250"
        ],
        "running": 1,
    }


def test_observe_counts_two_container_starts_in_one_deployment(monkeypatch, hook_state):
    monkeypatch.setattr(cloudflare, "_instances", lambda: _telemetry(starts=["first", "second"]))

    observed = cloudflare.observe({"worker_id": WORKER_ID})

    assert observed["launches"] == [
        f"{INSTANCE_ID}:{PLACEMENT_ID}:first",
        f"{INSTANCE_ID}:{PLACEMENT_ID}:second",
    ]
    assert observed["running"] == 1


def test_observe_retains_history_after_instance_is_gone(monkeypatch, hook_state):
    monkeypatch.setattr(cloudflare, "_instances", lambda: _telemetry())
    initial = cloudflare.observe({"worker_id": WORKER_ID})
    monkeypatch.setattr(
        cloudflare,
        "_instances",
        lambda: {"instances": [], "durable_objects": [{"id": ACTOR_ID, "name": WORKER_ID}]},
    )

    observed = cloudflare.observe({"worker_id": WORKER_ID})

    assert observed == {"launches": initial["launches"], "running": 0}


def test_observe_excludes_an_unrelated_workers_assignment(monkeypatch, hook_state):
    monkeypatch.setattr(cloudflare, "_instances", lambda: _telemetry(worker_id=OTHER_WORKER_ID))

    observed = cloudflare.observe({"worker_id": WORKER_ID})

    assert observed == {"launches": [], "running": 0}


def test_restart_provider_is_read_only_without_explicit_mutation_opt_in(monkeypatch):
    monkeypatch.delenv("CF_HOOK_ALLOW_MUTATIONS", raising=False)

    with pytest.raises(cloudflare.Unsupported, match="CF_HOOK_ALLOW_MUTATIONS=1"):
        cloudflare.restart_provider({"scope": "demo"})


def test_stop_process_is_read_only_without_explicit_mutation_opt_in(monkeypatch):
    monkeypatch.delenv("CF_HOOK_ALLOW_MUTATIONS", raising=False)

    with pytest.raises(cloudflare.Unsupported, match="CF_HOOK_ALLOW_MUTATIONS=1"):
        cloudflare.stop_process({"worker_id": WORKER_ID, "resource_uid": "a-resource"})


def test_restart_provider_rejects_a_scope_other_than_the_deployed_worker(monkeypatch, mutation_environment):
    paths: list[str] = []

    def get(path: str) -> dict:
        paths.append(path)
        if path.endswith("/settings"):
            return {"success": True, "result": _worker_settings(scope="other-scope")}
        if "/workers/services/" in path:
            return {
                "success": True,
                "result": {"default_environment": {"script": {"migration_tag": "v1"}}},
            }
        raise AssertionError(path)

    monkeypatch.setattr(cloudflare, "_get", get)
    monkeypatch.setattr(cloudflare, "_request", lambda *_args, **_kwargs: pytest.fail("must not upload"))

    with pytest.raises(cloudflare.Unsupported, match="scope"):
        cloudflare.restart_provider({"scope": "demo"})

    assert paths == ["/accounts/account-123/workers/services/capacity-provider", "/accounts/account-123/workers/scripts/capacity-provider/settings"]


def test_restart_provider_reuploads_checked_in_modules_without_secret_or_migration(monkeypatch, mutation_environment):
    uploads: list[tuple[str, str, dict[str, str], bytes]] = []
    version_reads = iter(("version-before", "version-after"))

    def get(path: str) -> dict:
        if "/workers/services/" in path:
            return {
                "success": True,
                "result": {"default_environment": {"script": {"migration_tag": "v1"}}},
            }
        if path.endswith("/settings"):
            return {"success": True, "result": _worker_settings()}
        if path.endswith("/versions?per_page=1"):
            return {"success": True, "result": [{"id": next(version_reads)}]}
        raise AssertionError(path)

    def request(method: str, path: str, *, headers: dict[str, str], data: bytes, **_kwargs) -> dict:
        uploads.append((method, path, headers, data))
        return {"success": True, "result": {}}

    monkeypatch.setattr(cloudflare, "_get", get)
    monkeypatch.setattr(cloudflare, "_request", request)

    result = cloudflare.restart_provider({"scope": "demo"})

    assert result == {"ok": True, "previous_version_id": "version-before", "version_id": "version-after"}
    assert len(uploads) == 1
    method, path, headers, body = uploads[0]
    assert method == "PUT"
    assert path == "/accounts/account-123/workers/scripts/capacity-provider"
    assert headers["Content-Type"].startswith("multipart/form-data; boundary=")
    assert b"the-admin-token-must-not-be-uploaded" not in body
    assert b"the-capacity-token-must-not-be-uploaded" not in body
    metadata = _multipart_metadata(body)
    assert metadata["main_module"] == "capacity.mjs"
    assert metadata["containers"] == [{"class_name": "WorkerContainer"}]
    assert metadata["keep_bindings"] == ["secret_text"]
    assert "migrations" not in metadata
    assert {binding["name"] for binding in metadata["bindings"]} >= {
        "WORKER_LEDGER",
        "SCOPE_REGISTRY",
        "WORKER_CONTAINER",
        "SCOPE",
        "CONFORMANCE_RESTART_NONCE",
    }
    assert b'filename="capacity.mjs"' in body
    assert b'filename="capacity-core.mjs"' in body


def test_restart_provider_requires_a_changed_worker_version(monkeypatch, mutation_environment):
    version_reads = iter(("same-version", "same-version"))

    def get(path: str) -> dict:
        if "/workers/services/" in path:
            return {
                "success": True,
                "result": {"default_environment": {"script": {"migration_tag": "v1"}}},
            }
        if path.endswith("/settings"):
            return {"success": True, "result": _worker_settings()}
        if path.endswith("/versions?per_page=1"):
            return {"success": True, "result": [{"id": next(version_reads)}]}
        raise AssertionError(path)

    monkeypatch.setattr(cloudflare, "_get", get)
    monkeypatch.setattr(cloudflare, "_request", lambda *_args, **_kwargs: {"success": True, "result": {}})

    with pytest.raises(cloudflare.Unsupported, match="new Worker version"):
        cloudflare.restart_provider({"scope": "demo"})


def test_stop_process_does_not_guess_an_instance_from_a_foreign_resource_uid(monkeypatch, mutation_environment):
    monkeypatch.setattr(cloudflare, "_get", lambda *_args, **_kwargs: pytest.fail("must not query a guessed target"))
    monkeypatch.setattr(cloudflare, "_request", lambda *_args, **_kwargs: pytest.fail("must not mutate a guessed target"))

    with pytest.raises(cloudflare.Unsupported, match="no targeted instance stop or delete"):
        cloudflare.stop_process({"worker_id": WORKER_ID, "resource_uid": "foreign-resource-uid"})


def test_cloudflare_errors_are_sanitized(monkeypatch):
    monkeypatch.setenv("CLOUDFLARE_API_TOKEN", "not-for-error-output")

    def fail(_request, timeout):
        raise urllib.error.HTTPError(
            "https://api.cloudflare.com/client/v4/secret-path",
            403,
            "forbidden",
            {},
            None,
        )

    monkeypatch.setattr(cloudflare.urllib.request, "urlopen", fail)

    with pytest.raises(cloudflare.Unsupported) as exc_info:
        cloudflare._get("/accounts/account-123/workers/scripts/capacity-provider/settings")

    assert str(exc_info.value) == "Cloudflare API 403"
    assert "secret-path" not in str(exc_info.value)
    assert "not-for-error-output" not in str(exc_info.value)


@pytest.mark.parametrize(
    "telemetry",
    [
        {"instances": [], "durable_objects": "not-a-list"},
        {
            "instances": [{"id": INSTANCE_ID, "current_placement": {}}],
            "durable_objects": [
                {"id": ACTOR_ID, "name": WORKER_ID, "deployment_id": INSTANCE_ID, "placement_id": PLACEMENT_ID}
            ],
        },
        {
            "instances": [
                {
                    "id": INSTANCE_ID,
                    "current_placement": {
                        "id": PLACEMENT_ID,
                        "deployment_id": INSTANCE_ID,
                        "durable_object_actor_id": ACTOR_ID,
                        "status": {"container_status": "running"},
                        "events": [{"id": "missing-time", "name": "ContainerStarted"}],
                    },
                }
            ],
            "durable_objects": [
                {"id": ACTOR_ID, "name": WORKER_ID, "deployment_id": INSTANCE_ID, "placement_id": PLACEMENT_ID}
            ],
        },
    ],
)
def test_observe_fails_closed_for_malformed_telemetry(monkeypatch, hook_state, telemetry):
    monkeypatch.setattr(cloudflare, "_instances", lambda: telemetry)

    with pytest.raises(cloudflare.Unsupported):
        cloudflare.observe({"worker_id": WORKER_ID})
