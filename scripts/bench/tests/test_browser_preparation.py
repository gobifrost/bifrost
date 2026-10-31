from __future__ import annotations

import sys
import unittest
import json
from unittest.mock import Mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import browser_preparation  # noqa: E402


class BrowserPreparationTest(unittest.TestCase):
    def test_tls_config_keeps_the_real_client_routes_and_listens_only_on_https(self) -> None:
        config = browser_preparation.tls_nginx_config()
        self.assertIn("listen 443 ssl;", config)
        self.assertNotIn("listen 80;", config)
        self.assertIn("ssl_certificate /etc/bench-tls/tls.crt;", config)
        self.assertIn("location ^~ /api", config)
        self.assertIn("location /ws", config)
        self.assertIn("proxy_set_header X-Forwarded-Proto $scheme;", config)

    def test_tls_manifest_replaces_client_service_with_the_same_origin_on_443(self) -> None:
        service, deployment = browser_preparation.tls_workload_documents()
        self.assertEqual(service["metadata"], {"name": "client", "namespace": "bench-tenant"})
        self.assertEqual(service["spec"]["ports"], [{"name": "https", "port": 443, "targetPort": 443, "protocol": "TCP"}])
        container = deployment["spec"]["template"]["spec"]["containers"][0]
        self.assertEqual(container["name"], "client")
        self.assertIn({"containerPort": 443, "name": "https"}, container["ports"])
        self.assertEqual(container["livenessProbe"]["httpGet"], {"path": "/", "port": 443, "scheme": "HTTPS"})
        self.assertEqual(container["readinessProbe"]["httpGet"], {"path": "/", "port": 443, "scheme": "HTTPS"})
        self.assertIn({"name": "bench-client-tls", "mountPath": "/etc/bench-tls", "readOnly": True}, container["volumeMounts"])
        self.assertIn({"name": "bench-client-tls", "secret": {"secretName": "bench-client-tls"}}, deployment["spec"]["template"]["spec"]["volumes"])
        self.assertEqual(browser_preparation.TLS_ORIGIN, "https://client.bench-tenant")

    def test_tls_workload_patch_preserves_the_existing_client_image_and_resources(self) -> None:
        cluster = Mock()
        service, deployment = browser_preparation.tls_workload_documents()
        browser_preparation.patch_tls_workload(cluster, service, deployment)
        service_patch = json.loads(cluster.kubectl.call_args_list[0].args[-1])
        deployment_patch = json.loads(cluster.kubectl.call_args_list[1].args[-1])
        self.assertEqual(service_patch, [{"op": "replace", "path": "/spec/ports", "value": service["spec"]["ports"]}])
        paths = {operation["path"] for operation in deployment_patch}
        self.assertEqual(paths, {
            "/spec/template/spec/containers/0/ports",
            "/spec/template/spec/containers/0/livenessProbe",
            "/spec/template/spec/containers/0/readinessProbe",
            "/spec/template/spec/containers/0/volumeMounts",
            "/spec/template/spec/volumes",
        })
        self.assertFalse(any("image" in operation or "resources" in operation for operation in deployment_patch))

    def test_build_runtime_patch_is_tenant_scoped_offline_and_has_equal_cpu(self) -> None:
        patch = browser_preparation.build_runtime_patch("bifrost-api:kind-a74e45295998-current")
        self.assertEqual(patch["BIFROST_KUBERNETES_JOB_IMAGE"], "bifrost-api:kind-a74e45295998-current")
        self.assertEqual(patch["BIFROST_KUBERNETES_JOB_CPU_REQUEST"], "250m")
        self.assertEqual(patch["BIFROST_KUBERNETES_JOB_CPU_LIMIT"], "250m")
        self.assertEqual(patch["npm_config_offline"], "true")
        self.assertEqual(patch["npm_config_cache"], "/home/bifrost/.npm")
        self.assertNotIn("BIFROST_KUBERNETES_JOB_NAMESPACE", patch)
        service_account = browser_preparation.build_service_account()
        self.assertEqual(service_account["metadata"]["namespace"], "bench-tenant")
        self.assertFalse(service_account["automountServiceAccountToken"])

    def test_policy_transition_keeps_max_workers_and_restores_each_mutable_field(self) -> None:
        original = {
            "warm_workers": 1,
            "max_workers": 2,
            "worker_memory_mib": 2048,
            "largest_worker_mib": 2048,
            "idle_seconds": 300,
            "isolated_jobs_enabled": False,
            "max_isolated_pods": 2,
        }
        prepare = browser_preparation.build_policy_prepare(original)
        self.assertEqual(prepare["warm_workers"], 0)
        self.assertEqual(prepare["idle_seconds"], browser_preparation.DRAIN_IDLE_SECONDS)
        self.assertTrue(prepare["isolated_jobs_enabled"])
        self.assertEqual(prepare["max_isolated_pods"], 1)
        self.assertNotIn("max_workers", prepare)
        self.assertEqual(browser_preparation.restore_policy(original), original)

    def test_cached_build_image_uses_the_fixture_lock_and_non_root_npm_cache(self) -> None:
        dockerfile = (Path(browser_preparation.HERE) / "browser" / "Dockerfile.build-cache").read_text()
        lock = json.loads((Path(browser_preparation.HERE) / "browser" / "fixture" / "package-lock.json").read_text())
        self.assertIn("fixture/package.json fixture/package-lock.json", dockerfile)
        self.assertIn("bifrost-sdk.tgz", dockerfile)
        self.assertIn("npm install --ignore-scripts --no-audit --no-fund --package-lock=false", dockerfile)
        self.assertNotIn("--omit=dev", dockerfile)
        self.assertIn("/home/bifrost/.npm", dockerfile)
        self.assertEqual(lock["packages"][""]["dependencies"], {
            "@vitejs/plugin-react": "6.1.2",
            "react": "19.3.1",
            "react-dom": "19.3.1",
            "vite": "8.3.5",
        })

    def test_build_runtime_restores_added_and_existing_config_keys_without_recording_values(self) -> None:
        cluster = Mock()
        cluster.kubectl.return_value = json.dumps({"data": {
            "BIFROST_KUBERNETES_JOB_CPU_LIMIT": "1",
            "BIFROST_PUBLIC_URL": "https://client.bench-tenant",
        }})
        previous, receipt = browser_preparation.prepare_build_runtime(
            cluster, "bifrost-api:kind-a74e45295998-current"
        )
        self.assertEqual(previous["BIFROST_KUBERNETES_JOB_CPU_LIMIT"], "1")
        self.assertIsNone(previous["npm_config_offline"])
        self.assertNotIn("BIFROST_PUBLIC_URL", receipt)
        browser_preparation.restore_build_runtime(cluster, previous)
        patch_calls = [call for call in cluster.kubectl.call_args_list if call.args[:2] == ("patch", "configmap")]
        self.assertEqual(len(patch_calls), 1)
        operations = json.loads(patch_calls[0].kwargs["p"] if "p" in patch_calls[0].kwargs else patch_calls[0].args[-1])
        self.assertIn({"op": "replace", "path": "/data/BIFROST_KUBERNETES_JOB_CPU_LIMIT", "value": "1"}, operations)
        self.assertIn({"op": "remove", "path": "/data/npm_config_offline"}, operations)

    def test_wait_for_worker_count_ignores_isolated_build_jobs_and_requires_ready_pool_workers(self) -> None:
        cluster = Mock()
        cluster.kubectl.return_value = json.dumps({"items": [{
            "metadata": {"name": "bifrost-worker-ready"},
            "status": {"phase": "Running", "conditions": [{"type": "Ready", "status": "True"}]},
        }]})
        observed = browser_preparation.wait_for_worker_count(cluster, 1, timeout_s=1)
        self.assertEqual(observed, {"live": 1, "ready": 1})
        cluster.kubectl.assert_called_once_with(
            "get", "pods", "-l", "app.kubernetes.io/name=bifrost-pool-worker", "-o", "json"
        )


if __name__ == "__main__":
    unittest.main()
