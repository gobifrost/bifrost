from __future__ import annotations

import hashlib
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import guard  # noqa: E402


class IdentityTest(unittest.TestCase):
    def test_expected_identity_is_derived_without_environment_overrides(self) -> None:
        root = Path("/work/bench")
        digest = hashlib.sha256(str(root).encode()).hexdigest()[:12]
        identity = guard.expected_identity(root)
        self.assertEqual(identity.worktree_hash, digest)
        self.assertEqual(identity.cluster_name, f"bifrost-{digest}")
        self.assertEqual(identity.context, f"kind-bifrost-{digest}")
        self.assertEqual(identity.state_dir, Path(f"/tmp/bifrost-k8s-{digest}"))
        self.assertEqual(identity.kubeconfig, identity.state_dir / "kubeconfig")
        self.assertEqual(identity.node_name, f"bifrost-{digest}-control-plane")
        self.assertEqual(identity.sentinel_path, identity.state_dir / "sentinel-id")


class CreationTest(unittest.TestCase):
    def setUp(self) -> None:
        self.identity = guard.expected_identity(Path("/work/bench"))

    def test_accepts_default_or_unix_docker_with_expected_name(self) -> None:
        guard.check_creation(None, self.identity.cluster_name, self.identity)
        guard.check_creation("", self.identity.cluster_name, self.identity)
        guard.check_creation("unix:///var/run/docker.sock", self.identity.cluster_name, self.identity)

    def test_rejects_non_local_docker(self) -> None:
        with self.assertRaisesRegex(guard.GuardError, "local unix"):
            guard.check_creation("tcp://production.example:2376", self.identity.cluster_name, self.identity)

    def test_rejects_other_cluster_name(self) -> None:
        with self.assertRaisesRegex(guard.GuardError, self.identity.cluster_name):
            guard.check_creation(None, "production", self.identity)


def _config(identity: guard.Identity, server: str = "https://127.0.0.1:6443") -> dict:
    return {
        "current-context": identity.context,
        "contexts": [{"name": identity.context, "context": {"cluster": "kind"}}],
        "clusters": [{"name": "kind", "cluster": {"server": server}}],
    }


class KubeconfigTest(unittest.TestCase):
    def setUp(self) -> None:
        self.identity = guard.expected_identity(Path("/work/bench"))

    def test_accepts_local_expected_context_among_other_contexts(self) -> None:
        config = _config(self.identity, "https://localhost:6443")
        config["contexts"].append({"name": "other", "context": {"cluster": "other"}})
        guard.check_kubeconfig(config, self.identity)

    def test_rejects_missing_context(self) -> None:
        with self.assertRaisesRegex(guard.GuardError, "missing context"):
            guard.check_kubeconfig({"contexts": [], "clusters": []}, self.identity)

    def test_rejects_context_without_cluster(self) -> None:
        config = _config(self.identity)
        config["contexts"][0]["context"] = {"cluster": "gone"}
        with self.assertRaisesRegex(guard.GuardError, "has no cluster"):
            guard.check_kubeconfig(config, self.identity)

    def test_rejects_non_local_or_non_https_server(self) -> None:
        for server in ("http://127.0.0.1:6443", "https://10.0.0.1:6443"):
            with self.subTest(server=server), self.assertRaisesRegex(guard.GuardError, "local https"):
                guard.check_kubeconfig(_config(self.identity, server), self.identity)

    def test_rejects_another_current_context(self) -> None:
        config = _config(self.identity)
        config["current-context"] = "other"
        with self.assertRaisesRegex(guard.GuardError, "current context"):
            guard.check_kubeconfig(config, self.identity)


class NodesAndSentinelTest(unittest.TestCase):
    def setUp(self) -> None:
        self.identity = guard.expected_identity(Path("/work/bench"))

    def _node(self, name: str | None = None, provider_id: str | None = None) -> dict:
        name = name or self.identity.node_name
        return {"metadata": {"name": name}, "spec": {"providerID": provider_id or f"kind://docker/{name}"}}

    def test_accepts_the_expected_single_kind_node(self) -> None:
        guard.check_nodes({"items": [self._node()]}, self.identity)

    def test_rejects_wrong_node_count_name_and_provider(self) -> None:
        cases = [
            {"items": []},
            {"items": [self._node("other")]},
            {"items": [self._node(provider_id="aws:///wrong")]},
        ]
        for nodes in cases:
            with self.subTest(nodes=nodes), self.assertRaises(guard.GuardError):
                guard.check_nodes(nodes, self.identity)

    def test_sentinel_requires_matching_values(self) -> None:
        guard.check_sentinel({"data": {"cluster-id": "same"}}, "same")
        for configmap, recorded in ((None, "same"), ({"data": {}}, "same"), ({"data": {"cluster-id": "a"}}, "b")):
            with self.subTest(configmap=configmap, recorded=recorded), self.assertRaises(guard.GuardError):
                guard.check_sentinel(configmap, recorded)

    def test_sentinel_helpers_make_the_expected_manifest(self) -> None:
        value = guard.new_sentinel_id()
        self.assertEqual(len(value), 32)
        self.assertEqual(guard.sentinel_configmap("abc"), {
            "apiVersion": "v1",
            "kind": "ConfigMap",
            "metadata": {"name": "bench-sentinel", "namespace": "kube-system"},
            "data": {"cluster-id": "abc"},
        })


class FaultAndDestinationTest(unittest.TestCase):
    def test_fault_target_requires_tenant_namespace_and_labels(self) -> None:
        pod = {"metadata": {"namespace": guard.TENANT_NS, "labels": {"app": "api", "run": "one"}}}
        guard.check_fault_target(pod, guard.TENANT_NS, {"app": "api"})
        with self.assertRaisesRegex(guard.GuardError, guard.TENANT_NS):
            guard.check_fault_target(pod, guard.INFRA_NS, {"app": "api"})
        with self.assertRaisesRegex(guard.GuardError, "run=two"):
            guard.check_fault_target(pod, guard.TENANT_NS, {"run": "two"})

    def test_service_namespaces_cover_all_permitted_forms(self) -> None:
        self.assertEqual(guard.service_namespace("redis:6379/0", guard.TENANT_NS), guard.TENANT_NS)
        self.assertEqual(guard.service_namespace("https://api.bench-infra/path", guard.TENANT_NS), guard.INFRA_NS)
        self.assertEqual(guard.service_namespace("postgres.bench-tenant.svc:5432", guard.INFRA_NS), guard.TENANT_NS)
        self.assertEqual(
            guard.service_namespace("postgresql+asyncpg://u:p@postgres.bench-tenant.svc.cluster.local:5432/db", guard.INFRA_NS),
            guard.TENANT_NS,
        )

    def test_service_namespace_rejects_ip_fqdn_and_localhost(self) -> None:
        for url in ("https://127.0.0.1", "https://localhost", "https://api.example.com"):
            with self.subTest(url=url), self.assertRaises(guard.GuardError):
                guard.service_namespace(url, guard.TENANT_NS)

    def test_destinations_skip_empty_and_list_every_safe_host(self) -> None:
        guard.check_destinations({"empty": "", "none": None, "db": "postgres.bench-tenant"}, guard.TENANT_NS)
        with self.assertRaisesRegex(guard.GuardError, r"one=api\.example\.com, two=127\.0\.0\.1"):
            guard.check_destinations(
                {"one": "https://user:secret@api.example.com/path", "two": "redis://127.0.0.1:6379/0"},
                guard.TENANT_NS,
            )

    def test_egress_probe_requires_reachable_target_denied_by_policy(self) -> None:
        guard.check_egress_probe(True, False, True)
        for case in ((False, False, True), (True, True, True), (True, False, False)):
            with self.subTest(case=case), self.assertRaises(guard.GuardError):
                guard.check_egress_probe(*case)


if __name__ == "__main__":
    unittest.main()
