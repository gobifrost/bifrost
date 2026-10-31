from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import interactive  # noqa: E402


os.environ.setdefault("BENCH_API_URL", "http://bench-api.test")
interactive_app = types.ModuleType("bench_interactive_app")
interactive_app_source = (Path(__file__).resolve().parents[1] / "incluster" / "interactive_app.py").read_text(encoding="utf-8")
exec(compile(interactive_app_source.rsplit("\nmain()", 1)[0], "bench-interactive-app", "exec"), interactive_app.__dict__)


DIGEST = "sha256:" + "a" * 64
RUNNER = "bifrost-bench-browser:kind-a74e45295998"
BASE = "mcr.microsoft.com/playwright:v1.64.1-noble@" + DIGEST
CACHED_BUILD = "bifrost-api:kind-a74e45295998-browser-cache"


class InteractiveInputsTest(unittest.TestCase):
    def test_browser_config_uses_tls_origin_and_bounded_variant_batches(self) -> None:
        config = interactive.browser_config("bench-browser-fixture")
        self.assertEqual(config["client_url"], "https://client.bench-tenant")
        self.assertEqual(config["app_slug"], "bench-browser-fixture")
        self.assertEqual(config["batches"], interactive.browser.observation_batches())
        self.assertNotIn("repetitions", config)

    def test_requires_exact_browser_digest_and_matching_preload_receipt(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            receipt = Path(temporary) / "cache.json"
            receipt.write_text(json.dumps({"image": RUNNER, "image_id": DIGEST, "cached_before_measurement": True, "loaded_at": "before"}))
            self.assertEqual(interactive._prepared_image(RUNNER, DIGEST), (RUNNER, DIGEST))
            self.assertEqual(interactive.load_cache_receipt(receipt, RUNNER, DIGEST)["image"], RUNNER)
            with self.assertRaisesRegex(ValueError, "exact image"):
                interactive._exact_digest("registry.local/browser:latest", "browser image")
            with self.assertRaisesRegex(ValueError, "does not prove"):
                interactive.load_cache_receipt(receipt, RUNNER, "sha256:" + "b" * 64)

    def test_build_cache_receipt_binds_the_local_image_to_the_fixture_lock(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            receipt = Path(temporary) / "build-cache.json"
            receipt.write_text(json.dumps({
                "image": CACHED_BUILD,
                "image_id": DIGEST,
                "cached_before_measurement": True,
                "fixture_lock_sha256": interactive.browser.fixture_lock_sha256(),
            }))
            loaded = interactive.load_build_cache_receipt(receipt, CACHED_BUILD)
            self.assertEqual(loaded["image_id"], DIGEST)
            receipt.write_text(json.dumps({"image": CACHED_BUILD, "image_id": DIGEST, "cached_before_measurement": True}))
            with self.assertRaisesRegex(ValueError, "fixture lock"):
                interactive.load_build_cache_receipt(receipt, CACHED_BUILD)

    def test_browser_pod_is_finite_infra_only_runner_with_secret_login(self) -> None:
        manifest = interactive.browser_pod(RUNNER)
        self.assertEqual(manifest["metadata"]["namespace"], "bench-infra")
        self.assertEqual(manifest["spec"]["restartPolicy"], "Never")
        container = manifest["spec"]["containers"][0]
        self.assertEqual(container["resources"]["limits"], {"cpu": "1", "memory": "1Gi"})
        self.assertEqual(container["image"], RUNNER)
        self.assertEqual(container["envFrom"], [{"secretRef": {"name": "bench-infra"}}])
        self.assertNotIn("BIFROST_DEFAULT_USER_PASSWORD", json.dumps(manifest))

    def test_durable_records_must_match_observed_ids_request_ids_and_variants(self) -> None:
        observed = [
            {"execution_id": "execution-1", "request_id": "request-1", "variant": "control"},
        ]
        cluster = Mock()
        cluster.exec_python.return_value = {"records": [{
            "id": "execution-1", "status": "Success", "result": {"request_id": "request-1", "variant": "control"},
        }]}
        with patch.object(interactive.integration_diagnostic, "validate_execution_records", return_value={"overall": "pass"}):
            records = interactive._durable_browser_records(cluster, observed)
        self.assertEqual(records[0]["id"], "execution-1")
        cluster.exec_python.assert_called_once()
        cluster.exec_python.return_value = {"records": [{
            "id": "execution-1", "status": "Success", "result": {"request_id": "other", "variant": "control"},
        }]}
        with self.assertRaisesRegex(RuntimeError, "request ID"):
            interactive._durable_browser_records(cluster, observed)

    def test_build_preparation_restores_runtime_and_original_worker_policy_before_observation(self) -> None:
        original = {
            "warm_workers": 1, "max_workers": 2, "worker_memory_mib": 2048,
            "largest_worker_mib": 2048, "idle_seconds": 300,
            "isolated_jobs_enabled": False, "max_isolated_pods": 2,
        }
        cluster = Mock()
        with patch.object(interactive.browser_preparation, "restore_build_runtime") as runtime, \
             patch.object(interactive, "_worker_policy", return_value=original) as policy, \
             patch.object(interactive.browser_preparation, "wait_for_worker_count", return_value={"live": 1, "ready": 1}) as wait:
            receipt = interactive._restore_build_preparation(cluster, {"npm_config_offline": None}, original)
        runtime.assert_called_once_with(cluster, {"npm_config_offline": None})
        policy.assert_called_once_with(cluster, "update", original)
        wait.assert_called_once_with(cluster, 1)
        self.assertEqual(receipt["policy"], {"restored": True, "workers": {"live": 1, "ready": 1}})


class InteractiveRunTest(unittest.TestCase):
    def test_rejects_noninteractive_profile_before_cluster_access(self) -> None:
        with tempfile.TemporaryDirectory() as temporary, \
             patch.object(interactive.bench_run, "load_profile", return_value={}), \
             patch.object(interactive, "Cluster") as cluster:
            cache = Path(temporary) / "cache.json"
            cache.write_text(json.dumps({"image": RUNNER, "image_id": DIGEST, "cached_before_measurement": True}))
            with self.assertRaisesRegex(ValueError, "interactive_integration_diagnostic"):
                interactive.run("m", "api", RUNNER, DIGEST, BASE, cache, CACHED_BUILD, Path(temporary) / "missing-build-cache.json", Path(temporary) / "out")
        cluster.assert_not_called()

    def test_error_after_cluster_mutation_resets_guarded_namespaces(self) -> None:
        class Current:
            def __init__(self, *_args: object) -> None:
                self.result: dict = {}

            def save(self) -> None:
                return None

        cluster = Mock()
        profile = {"interactive_integration_diagnostic": True}
        with tempfile.TemporaryDirectory() as temporary, \
             patch.object(interactive.bench_run, "load_profile", return_value=profile), \
             patch.object(interactive, "Cluster", return_value=cluster), \
             patch.object(interactive.bench_run, "Run", Current), \
             patch.object(interactive.deploy, "reset") as reset, \
             patch.object(interactive.bench_run, "_prepare", side_effect=RuntimeError("setup failed")):
            cache = Path(temporary) / "cache.json"
            cache.write_text(json.dumps({"image": RUNNER, "image_id": DIGEST, "cached_before_measurement": True}))
            build_cache = Path(temporary) / "build-cache.json"
            build_cache.write_text(json.dumps({
                "image": CACHED_BUILD, "image_id": DIGEST, "cached_before_measurement": True,
                "fixture_lock_sha256": interactive.browser.fixture_lock_sha256(),
            }))
            with self.assertRaisesRegex(RuntimeError, "setup failed"):
                interactive.run("m-interactive", "api", RUNNER, DIGEST, BASE, cache, CACHED_BUILD, build_cache, Path(temporary) / "out")
        self.assertGreaterEqual(cluster.verify.call_count, 4)
        self.assertEqual(reset.call_count, 2)
        cluster.kubectl.assert_called()

    def test_matrix_failure_retains_raw_pod_logs_and_decoded_runs_before_cleanup(self) -> None:
        instances = []

        class Current:
            def __init__(self, *_args: object) -> None:
                self.result: dict = {}
                instances.append(self)

            def save(self) -> None:
                return None

        original_policy = {
            "warm_workers": 1, "max_workers": 1, "worker_memory_mib": 2048,
            "largest_worker_mib": 2048, "idle_seconds": 300,
            "isolated_jobs_enabled": False, "max_isolated_pods": 1,
        }
        pod = {"status": {"phase": "Succeeded", "containerStatuses": [{
            "name": "browser", "imageID": "docker-pullable://runner@" + DIGEST,
        }]}}
        logs = '{"runs": []}\n'
        cluster = Mock()
        cluster.container_stats.return_value = []
        cluster.exec_python.return_value = {
            "accepted": {"job_id": "deploy-job"},
            "job": {},
            "application": {"slug": "bench-browser"},
        }

        def write_source(destination: Path, _files: dict[str, bytes]) -> dict:
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(b"fixture")
            return {}

        with tempfile.TemporaryDirectory() as temporary, \
             patch.object(interactive.bench_run, "load_profile", return_value={"interactive_integration_diagnostic": True}), \
             patch.object(interactive, "Cluster", return_value=cluster), \
             patch.object(interactive.bench_run, "Run", Current), \
             patch.object(interactive.bench_run, "_prepare"), \
             patch.object(interactive.deploy, "reset"), \
             patch.object(interactive.browser_preparation, "prepare_tls", return_value={}), \
             patch.object(interactive, "_worker_policy", side_effect=[original_policy, {}, original_policy]), \
             patch.object(interactive.browser_preparation, "wait_for_worker_count", return_value={"live": 0, "ready": 0}), \
             patch.object(interactive.browser_preparation, "prepare_build_runtime", return_value=({}, {})), \
             patch.object(interactive, "_restore_build_preparation", return_value={}), \
             patch.object(interactive.browser, "write_source_zip", side_effect=write_source), \
             patch.object(interactive.browser, "build_receipt", return_value={}), \
             patch.object(interactive, "_apply_browser_config"), \
             patch.object(interactive, "_pod_terminal", return_value=(pod, logs)), \
             patch.object(interactive, "_cleanup_browser"), \
             patch.object(interactive, "_durable_browser_records"):
            cache = Path(temporary) / "cache.json"
            cache.write_text(json.dumps({"image": RUNNER, "image_id": DIGEST, "cached_before_measurement": True}))
            build_cache = Path(temporary) / "build-cache.json"
            build_cache.write_text(json.dumps({
                "image": CACHED_BUILD, "image_id": DIGEST, "cached_before_measurement": True,
                "fixture_lock_sha256": interactive.browser.fixture_lock_sha256(),
            }))
            with self.assertRaisesRegex(ValueError, "expected exactly"):
                interactive.run("m-interactive", "api", RUNNER, DIGEST, BASE, cache, CACHED_BUILD, build_cache, Path(temporary) / "out")

        self.assertEqual(instances[0].result["interactive"]["browser"]["raw"], {
            "pod": pod, "logs": logs, "runs": [],
        })

    def test_application_metadata_failure_retains_accepted_deploy_job_receipt(self) -> None:
        instances = []

        class Current:
            def __init__(self, *_args: object) -> None:
                self.result: dict = {}
                instances.append(self)

            def save(self) -> None:
                return None

        profile = {"interactive_integration_diagnostic": True}
        original_policy = {
            "warm_workers": 1, "max_workers": 1, "worker_memory_mib": 2048,
            "largest_worker_mib": 2048, "idle_seconds": 300,
            "isolated_jobs_enabled": False, "max_isolated_pods": 1,
        }
        cluster = Mock()
        cluster.exec_python.return_value = {
            "accepted": {"job_id": "deploy-job"},
            "job": {"id": "deploy-job", "status": "succeeded"},
            "application": None,
            "application_read_error": "RuntimeError: read deployed application failed: HTTP 404",
        }

        def write_source(destination: Path, _files: dict[str, bytes]) -> dict:
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(b"fixture")
            return {"archive_sha256": "fixture"}

        with tempfile.TemporaryDirectory() as temporary, \
             patch.object(interactive.bench_run, "load_profile", return_value=profile), \
             patch.object(interactive, "Cluster", return_value=cluster), \
             patch.object(interactive.bench_run, "Run", Current), \
             patch.object(interactive.bench_run, "_prepare") as prepare, \
             patch.object(interactive.deploy, "reset"), \
             patch.object(interactive.browser_preparation, "prepare_tls", return_value={}), \
             patch.object(interactive, "_worker_policy", side_effect=[original_policy, {}]), \
             patch.object(interactive.browser_preparation, "wait_for_worker_count", return_value={"live": 0, "ready": 0}), \
             patch.object(interactive.browser_preparation, "prepare_build_runtime", return_value=({}, {})), \
             patch.object(interactive, "_restore_build_preparation", return_value={}), \
             patch.object(interactive.browser, "write_source_zip", side_effect=write_source), \
             patch.object(interactive.browser, "build_receipt") as build_receipt:
            cache = Path(temporary) / "cache.json"
            cache.write_text(json.dumps({"image": RUNNER, "image_id": DIGEST, "cached_before_measurement": True}))
            build_cache = Path(temporary) / "build-cache.json"
            build_cache.write_text(json.dumps({
                "image": CACHED_BUILD, "image_id": DIGEST, "cached_before_measurement": True,
                "fixture_lock_sha256": interactive.browser.fixture_lock_sha256(),
            }))
            with self.assertRaisesRegex(RuntimeError, "could not read deployed application metadata"):
                interactive.run("m-interactive", "api", RUNNER, DIGEST, BASE, cache, CACHED_BUILD, build_cache, Path(temporary) / "out")

        prepare.assert_called_once_with(cluster, instances[0], profile, None, "api")
        build_receipt.assert_not_called()
        self.assertEqual(instances[0].result["interactive"]["build"], {
            "source": {"archive_sha256": "fixture"},
            "accepted": {"job_id": "deploy-job"},
            "job": {"id": "deploy-job", "status": "succeeded"},
            "application": None,
            "application_read_error": "RuntimeError: read deployed application failed: HTTP 404",
        })

    def test_terminal_cleanup_resets_namespaces_after_success(self) -> None:
        current = Mock()
        current.result = {"interactive": {}}
        cluster = Mock()
        with patch.object(interactive, "_cleanup_browser") as browser_cleanup, \
             patch.object(interactive.deploy, "reset") as reset:
            interactive._terminal_cleanup(cluster, current, None)
        browser_cleanup.assert_called_once_with(cluster)
        reset.assert_called_once_with(cluster)
        self.assertEqual(current.result["interactive"]["cleanup"]["namespaces"], {"verified": True})
        self.assertEqual(cluster.verify.call_count, 3)

    def test_terminal_cleanup_preserves_body_error_when_cleanup_fails(self) -> None:
        current = Mock()
        current.result = {"interactive": {}}
        cluster = Mock()
        with patch.object(interactive, "_cleanup_browser", side_effect=RuntimeError("browser cleanup failed")), \
             patch.object(interactive.deploy, "reset", side_effect=RuntimeError("namespace cleanup failed")):
            interactive._terminal_cleanup(cluster, current, RuntimeError("body failed"))
        receipt = current.result["interactive"]["cleanup"]
        self.assertFalse(receipt["browser_resources"]["verified"])
        self.assertFalse(receipt["namespaces"]["verified"])


class InteractiveAppPolicyTest(unittest.TestCase):
    def test_policy_helper_uses_the_existing_authenticated_execution_endpoints(self) -> None:
        class Response:
            status_code = 200
            text = ""

            def __init__(self, body: dict) -> None:
                self.body = body

            def json(self) -> dict:
                return self.body

        class Client:
            def __init__(self) -> None:
                self.calls: list[tuple[str, str, dict | None]] = []

            async def get(self, path: str) -> Response:
                self.calls.append(("GET", path, None))
                return Response({"policy": {"warm_workers": 1}})

            async def put(self, path: str, json: dict) -> Response:
                self.calls.append(("PUT", path, json))
                return Response({"policy": json})

            async def aclose(self) -> None:
                return None

        client = Client()

        async def fake_client() -> Client:
            return client

        with patch.object(interactive_app, "_client", fake_client):
            self.assertEqual(asyncio.run(interactive_app.policy("read")), {"policy": {"warm_workers": 1}})
            self.assertEqual(asyncio.run(interactive_app.policy("update", {"warm_workers": 0})), {"policy": {"warm_workers": 0}})
        self.assertEqual(client.calls, [
            ("GET", "/api/admin/kubernetes/execution", None),
            ("PUT", "/api/admin/kubernetes/policy", {"warm_workers": 0}),
        ])


class InteractiveAppDeployTest(unittest.TestCase):
    def test_deploy_reads_by_created_slug_after_normal_deploy_job(self) -> None:
        class Response:
            status_code = 200
            text = ""

            def __init__(self, body: dict) -> None:
                self.body = body

            def json(self) -> dict:
                return self.body

        class Client:
            def __init__(self) -> None:
                self.calls: list[tuple[str, str]] = []

            async def post(self, path: str, **_kwargs: object) -> Response:
                self.calls.append(("POST", path))
                if path == "/api/applications":
                    return Response({"id": "application-id", "slug": "created-browser-app"})
                if path != "/api/applications/application-id/deploy":
                    raise AssertionError(path)
                return Response({"job_id": "deploy-job"})

            async def get(self, path: str) -> Response:
                self.calls.append(("GET", path))
                if path == "/api/platform-jobs/deploy-job":
                    return Response({"id": "deploy-job", "status": "succeeded"})
                if path != "/api/applications/created-browser-app":
                    raise AssertionError(path)
                return Response({"id": "application-id", "slug": "created-browser-app"})

            async def aclose(self) -> None:
                return None

        client = Client()

        async def fake_client() -> Client:
            return client

        with patch.object(interactive_app, "_client", fake_client):
            result = asyncio.run(interactive_app.deploy("Zml4dHVyZQ==", "Fixture", "requested-slug"))

        self.assertEqual(result["accepted"], {"job_id": "deploy-job"})
        self.assertEqual(result["job"], {"id": "deploy-job", "status": "succeeded"})
        self.assertEqual(result["application"]["slug"], "created-browser-app")
        self.assertEqual(client.calls, [
            ("POST", "/api/applications"),
            ("POST", "/api/applications/application-id/deploy"),
            ("GET", "/api/platform-jobs/deploy-job"),
            ("GET", "/api/applications/created-browser-app"),
        ])


if __name__ == "__main__":
    unittest.main()
