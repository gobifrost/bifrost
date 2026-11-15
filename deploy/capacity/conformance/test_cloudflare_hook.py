"""Unit tests for Cloudflare's capacity-conformance observation hook."""

from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import re
import tempfile
import unittest
import urllib.error
from unittest import mock


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
        "instances": [{
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
        }],
        "durable_objects": [{
            "id": ACTOR_ID,
            "name": worker_id,
            "deployment_id": INSTANCE_ID,
            "placement_id": PLACEMENT_ID,
        }],
    }


def _worker_settings(scope: str = "demo", *, observability: dict | None = None) -> dict:
    settings = {
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
    if observability is not None:
        settings["observability"] = observability
    return settings


def _current_version(version_id: str) -> dict:
    return {
        "id": version_id,
        "resources": {"script_runtime": {"containers": [{
            "name": "capacity-provider-worker-container",
            "class_name": "WorkerContainer",
            "images": {"base": "registry.cloudflare.com/account/image@sha256:abc123"},
        }]}},
    }


def _multipart_metadata(body: bytes) -> dict:
    match = re.search(
        rb'Content-Disposition: form-data; name="metadata"\r\nContent-Type: application/json\r\n\r\n(.+?)\r\n--',
        body,
        re.DOTALL,
    )
    assert match is not None
    return json.loads(match.group(1))


class CloudflareHookTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.environment = mock.patch.dict(os.environ, {}, clear=True)
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.state_path = mock.patch.object(
            cloudflare,
            "STATE_PATH",
            str(Path(self.temporary_directory.name) / "cloudflare-launches.json"),
        )
        self.state_path.start()
        self.addCleanup(self.state_path.stop)

    def replace(self, target: object, attribute: str, value: object) -> None:
        patcher = mock.patch.object(target, attribute, value)
        patcher.start()
        self.addCleanup(patcher.stop)

    def mutation_environment(self) -> Path:
        source = Path(self.temporary_directory.name) / "provider"
        source.mkdir()
        (source / "capacity.mjs").write_text("export default {};\n", encoding="utf-8")
        (source / "capacity-core.mjs").write_text("export const core = true;\n", encoding="utf-8")
        os.environ.update({
            "CLOUDFLARE_API_TOKEN": "the-admin-token-must-not-be-uploaded",
            "CAPACITY_TOKEN": "the-capacity-token-must-not-be-uploaded",
            "CF_ACCOUNT_ID": "account-123",
            "CF_SCRIPT_NAME": "capacity-provider",
            "CF_PROVIDER_SOURCE": str(source),
            "CF_HOOK_ALLOW_MUTATIONS": "1",
        })
        return source

    def test_observe_maps_worker_through_durable_object_and_container_start(self) -> None:
        self.replace(cloudflare, "_instances", lambda: _telemetry())
        self.assertEqual(cloudflare.observe({"worker_id": WORKER_ID}), {
            "launches": [f"{INSTANCE_ID}:{PLACEMENT_ID}:c0e0906b-c984-47fd-89e2-9b473c72c250"],
            "running": 1,
        })

    def test_observe_counts_two_container_starts_in_one_deployment(self) -> None:
        self.replace(cloudflare, "_instances", lambda: _telemetry(starts=["first", "second"]))
        observed = cloudflare.observe({"worker_id": WORKER_ID})
        self.assertEqual(observed["launches"], [
            f"{INSTANCE_ID}:{PLACEMENT_ID}:first", f"{INSTANCE_ID}:{PLACEMENT_ID}:second",
        ])
        self.assertEqual(observed["running"], 1)

    def test_observe_retains_history_after_instance_is_gone(self) -> None:
        self.replace(cloudflare, "_instances", lambda: _telemetry())
        initial = cloudflare.observe({"worker_id": WORKER_ID})
        self.replace(
            cloudflare,
            "_instances",
            lambda: {"instances": [], "durable_objects": [{"id": ACTOR_ID, "name": WORKER_ID}]},
        )
        self.assertEqual(cloudflare.observe({"worker_id": WORKER_ID}), {
            "launches": initial["launches"], "running": 0,
        })

    def test_observe_excludes_an_unrelated_workers_assignment(self) -> None:
        self.replace(cloudflare, "_instances", lambda: _telemetry(worker_id=OTHER_WORKER_ID))
        self.assertEqual(cloudflare.observe({"worker_id": WORKER_ID}), {"launches": [], "running": 0})

    def test_restart_provider_is_read_only_without_explicit_mutation_opt_in(self) -> None:
        with self.assertRaisesRegex(cloudflare.Unsupported, "CF_HOOK_ALLOW_MUTATIONS=1"):
            cloudflare.restart_provider({"scope": "demo"})

    def test_stop_process_is_read_only_without_explicit_mutation_opt_in(self) -> None:
        with self.assertRaisesRegex(cloudflare.Unsupported, "CF_HOOK_ALLOW_MUTATIONS=1"):
            cloudflare.stop_process({"worker_id": WORKER_ID, "resource_uid": "a-resource"})

    def test_restart_provider_rejects_a_scope_other_than_the_deployed_worker(self) -> None:
        self.mutation_environment()
        paths: list[str] = []

        def get(path: str) -> dict:
            paths.append(path)
            if path.endswith("/settings"):
                return {"success": True, "result": _worker_settings(scope="other-scope")}
            if "/workers/services/" in path:
                return {"success": True, "result": {"default_environment": {"script": {"migration_tag": "v1"}}}}
            raise AssertionError(path)

        self.replace(cloudflare, "_get", get)
        self.replace(cloudflare, "_request", lambda *_args, **_kwargs: self.fail("must not upload"))
        with self.assertRaisesRegex(cloudflare.Unsupported, "scope"):
            cloudflare.restart_provider({"scope": "demo"})
        self.assertEqual(paths, [
            "/accounts/account-123/workers/services/capacity-provider",
            "/accounts/account-123/workers/scripts/capacity-provider/settings",
        ])

    def test_restart_provider_preserves_verified_container_metadata_and_observability(self) -> None:
        self.mutation_environment()
        uploads: list[tuple[str, str, dict[str, str], bytes]] = []
        version_reads = iter(("version-before", "version-after"))

        def get(path: str) -> dict:
            if "/workers/services/" in path:
                return {"success": True, "result": {"default_environment": {"script": {"migration_tag": "v1"}}}}
            if path.endswith("/settings"):
                return {"success": True, "result": _worker_settings(observability={"enabled": True})}
            if path.endswith("/versions?per_page=1"):
                return {"success": True, "result": [{"id": next(version_reads)}]}
            if path.endswith("/versions/version-before"):
                return {"success": True, "result": _current_version("version-before")}
            raise AssertionError(path)

        def request(method: str, path: str, *, headers: dict[str, str], data: bytes, **_kwargs) -> dict:
            uploads.append((method, path, headers, data))
            return {"success": True, "result": {}}

        self.replace(cloudflare, "_get", get)
        self.replace(cloudflare, "_request", request)
        result = cloudflare.restart_provider({"scope": "demo"})

        self.assertEqual(result, {"ok": True, "previous_version_id": "version-before", "version_id": "version-after"})
        self.assertEqual(len(uploads), 1)
        method, path, headers, body = uploads[0]
        self.assertEqual(method, "PUT")
        self.assertEqual(path, "/accounts/account-123/workers/scripts/capacity-provider")
        self.assertTrue(headers["Content-Type"].startswith("multipart/form-data; boundary="))
        self.assertNotIn(b"the-admin-token-must-not-be-uploaded", body)
        self.assertNotIn(b"the-capacity-token-must-not-be-uploaded", body)
        metadata = _multipart_metadata(body)
        self.assertEqual(metadata["main_module"], "capacity.mjs")
        self.assertEqual(metadata["containers"], [{
            "name": "capacity-provider-worker-container",
            "class_name": "WorkerContainer",
            "images": {"base": "registry.cloudflare.com/account/image@sha256:abc123"},
        }])
        self.assertEqual(metadata["observability"], {"enabled": True})
        self.assertEqual(metadata["keep_bindings"], ["secret_text"])
        self.assertNotIn("migrations", metadata)
        self.assertGreaterEqual({binding["name"] for binding in metadata["bindings"]}, {
            "WORKER_LEDGER", "SCOPE_REGISTRY", "WORKER_CONTAINER", "SCOPE", "CONFORMANCE_RESTART_NONCE",
        })
        self.assertIn(b'filename="capacity.mjs"', body)
        self.assertIn(b'filename="capacity-core.mjs"', body)

    def test_restart_provider_refuses_to_upload_without_a_verified_current_container_binding(self) -> None:
        self.mutation_environment()
        requests: list[str] = []

        def get(path: str) -> dict:
            requests.append(path)
            if "/workers/services/" in path:
                return {"success": True, "result": {"default_environment": {"script": {"migration_tag": "v1"}}}}
            if path.endswith("/settings"):
                return {"success": True, "result": _worker_settings()}
            if path.endswith("/versions?per_page=1"):
                return {"success": True, "result": [{"id": "version-before"}]}
            if path.endswith("/versions/version-before"):
                return {"success": True, "result": {"id": "version-before", "resources": {}}}
            raise AssertionError(path)

        self.replace(cloudflare, "_get", get)
        self.replace(cloudflare, "_request", lambda *_args, **_kwargs: self.fail("must not upload"))
        with self.assertRaisesRegex(cloudflare.Unsupported, "container metadata"):
            cloudflare.restart_provider({"scope": "demo"})
        self.assertEqual(requests, [
            "/accounts/account-123/workers/services/capacity-provider",
            "/accounts/account-123/workers/scripts/capacity-provider/settings",
            "/accounts/account-123/workers/scripts/capacity-provider/versions?per_page=1",
            "/accounts/account-123/workers/scripts/capacity-provider/versions/version-before",
        ])

    def test_restart_provider_requires_a_changed_worker_version(self) -> None:
        self.mutation_environment()
        version_reads = iter(("same-version", "same-version"))

        def get(path: str) -> dict:
            if "/workers/services/" in path:
                return {"success": True, "result": {"default_environment": {"script": {"migration_tag": "v1"}}}}
            if path.endswith("/settings"):
                return {"success": True, "result": _worker_settings()}
            if path.endswith("/versions?per_page=1"):
                return {"success": True, "result": [{"id": next(version_reads)}]}
            if path.endswith("/versions/same-version"):
                return {"success": True, "result": _current_version("same-version")}
            raise AssertionError(path)

        self.replace(cloudflare, "_get", get)
        self.replace(cloudflare, "_request", lambda *_args, **_kwargs: {"success": True, "result": {}})
        with self.assertRaisesRegex(cloudflare.Unsupported, "new Worker version"):
            cloudflare.restart_provider({"scope": "demo"})

    def test_stop_process_does_not_guess_an_instance_from_a_foreign_resource_uid(self) -> None:
        self.mutation_environment()
        self.replace(cloudflare, "_get", lambda *_args, **_kwargs: self.fail("must not query a guessed target"))
        self.replace(cloudflare, "_request", lambda *_args, **_kwargs: self.fail("must not mutate a guessed target"))
        with self.assertRaisesRegex(cloudflare.Unsupported, "no targeted instance stop or delete"):
            cloudflare.stop_process({"worker_id": WORKER_ID, "resource_uid": "foreign-resource-uid"})

    def test_cloudflare_errors_are_sanitized(self) -> None:
        os.environ["CLOUDFLARE_API_TOKEN"] = "not-for-error-output"

        def fail(_request, timeout):
            raise urllib.error.HTTPError("https://api.cloudflare.com/client/v4/secret-path", 403, "forbidden", {}, None)

        self.replace(cloudflare.urllib.request, "urlopen", fail)
        with self.assertRaises(cloudflare.Unsupported) as raised:
            cloudflare._get("/accounts/account-123/workers/scripts/capacity-provider/settings")
        self.assertEqual(str(raised.exception), "Cloudflare API 403")
        self.assertNotIn("secret-path", str(raised.exception))
        self.assertNotIn("not-for-error-output", str(raised.exception))

    def test_observe_fails_closed_for_non_list_durable_objects(self) -> None:
        self.replace(cloudflare, "_instances", lambda: {"instances": [], "durable_objects": "not-a-list"})
        with self.assertRaises(cloudflare.Unsupported):
            cloudflare.observe({"worker_id": WORKER_ID})

    def test_observe_fails_closed_for_missing_placement(self) -> None:
        telemetry = {
            "instances": [{"id": INSTANCE_ID, "current_placement": {}}],
            "durable_objects": [{
                "id": ACTOR_ID, "name": WORKER_ID, "deployment_id": INSTANCE_ID, "placement_id": PLACEMENT_ID,
            }],
        }
        self.replace(cloudflare, "_instances", lambda: telemetry)
        with self.assertRaises(cloudflare.Unsupported):
            cloudflare.observe({"worker_id": WORKER_ID})

    def test_observe_fails_closed_for_container_start_without_time(self) -> None:
        telemetry = {
            "instances": [{
                "id": INSTANCE_ID,
                "current_placement": {
                    "id": PLACEMENT_ID,
                    "deployment_id": INSTANCE_ID,
                    "durable_object_actor_id": ACTOR_ID,
                    "status": {"container_status": "running"},
                    "events": [{"id": "missing-time", "name": "ContainerStarted"}],
                },
            }],
            "durable_objects": [{
                "id": ACTOR_ID, "name": WORKER_ID, "deployment_id": INSTANCE_ID, "placement_id": PLACEMENT_ID,
            }],
        }
        self.replace(cloudflare, "_instances", lambda: telemetry)
        with self.assertRaises(cloudflare.Unsupported):
            cloudflare.observe({"worker_id": WORKER_ID})


if __name__ == "__main__":
    unittest.main()
