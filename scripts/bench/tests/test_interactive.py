from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import interactive  # noqa: E402


DIGEST = "sha256:" + "a" * 64
RUNNER = "bifrost-bench-browser:kind-a74e45295998"
BASE = "mcr.microsoft.com/playwright:v1.64.1-noble@" + DIGEST


class InteractiveInputsTest(unittest.TestCase):
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


class InteractiveRunTest(unittest.TestCase):
    def test_rejects_noninteractive_profile_before_cluster_access(self) -> None:
        with tempfile.TemporaryDirectory() as temporary, \
             patch.object(interactive.bench_run, "load_profile", return_value={}), \
             patch.object(interactive, "Cluster") as cluster:
            cache = Path(temporary) / "cache.json"
            cache.write_text(json.dumps({"image": RUNNER, "image_id": DIGEST, "cached_before_measurement": True}))
            with self.assertRaisesRegex(ValueError, "interactive_integration_diagnostic"):
                interactive.run("m", "api", RUNNER, DIGEST, BASE, cache, Path(temporary) / "out")
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
             patch.object(interactive.bench_run, "run_classes", side_effect=RuntimeError("setup failed")):
            cache = Path(temporary) / "cache.json"
            cache.write_text(json.dumps({"image": RUNNER, "image_id": DIGEST, "cached_before_measurement": True}))
            with self.assertRaisesRegex(RuntimeError, "setup failed"):
                interactive.run("m-interactive", "api", RUNNER, DIGEST, BASE, cache, Path(temporary) / "out")
        self.assertGreaterEqual(cluster.verify.call_count, 4)
        self.assertEqual(reset.call_count, 2)
        cluster.kubectl.assert_called()

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


if __name__ == "__main__":
    unittest.main()
