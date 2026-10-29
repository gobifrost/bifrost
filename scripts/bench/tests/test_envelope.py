from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import envelope  # noqa: E402


def _container(name: str, cpu: str = "250m", memory: str = "128Mi", *, limits: bool = True) -> dict:
    resources = {"requests": {"cpu": cpu, "memory": memory}}
    if limits:
        resources["limits"] = {"cpu": cpu, "memory": memory}
    return {"name": name, "resources": resources}


class QuantityTest(unittest.TestCase):
    def test_cpu_quantities_are_millicores(self) -> None:
        self.assertEqual(envelope.parse_cpu("250m"), 250)
        self.assertEqual(envelope.parse_cpu("1"), 1000)
        self.assertEqual(envelope.parse_cpu("0.65"), 650)
        self.assertEqual(envelope.parse_cpu("2.5"), 2500)
        self.assertEqual(envelope.parse_cpu(1), 1000)

    def test_rejects_malformed_or_sub_millicore_cpu(self) -> None:
        for value in ("", "1Mi", "-1", "1e3", "0.0005", True):
            with self.subTest(value=value), self.assertRaises(envelope.EnvelopeError):
                envelope.parse_cpu(value)

    def test_memory_quantities_support_binary_decimal_and_plain_bytes(self) -> None:
        self.assertEqual(envelope.parse_memory("1.25Gi"), 1342177280)
        self.assertEqual(envelope.parse_memory("2Mi"), 2 * 1024**2)
        self.assertEqual(envelope.parse_memory("3G"), 3 * 1000**3)
        self.assertEqual(envelope.parse_memory("42"), 42)

    def test_rejects_malformed_or_fractional_byte_memory(self) -> None:
        for value in ("", "1MB", "-1", "1e3", "0.5", False):
            with self.subTest(value=value), self.assertRaises(envelope.EnvelopeError):
                envelope.parse_memory(value)


class ResourcesTest(unittest.TestCase):
    def test_arithmetic_fits_and_format(self) -> None:
        resources = envelope.Resources(1250, 2 * 1024**3) + envelope.Resources(250, 128 * 1024**2)
        self.assertEqual(resources, envelope.Resources(1500, 2281701376))
        self.assertEqual(2 * envelope.Resources(2000, 4 * 1024**3), envelope.Resources(4000, 8 * 1024**3))
        self.assertTrue(resources.fits(envelope.Resources(1500, 3 * 1024**3)))
        self.assertFalse(resources.fits(envelope.Resources(1499, 3 * 1024**3)))
        self.assertEqual(envelope.Resources(4000, 8254392320).format(), "4.000 CPU / 7.69 GiB")

    def test_rejects_non_integer_resource_scalar(self) -> None:
        with self.assertRaises(envelope.EnvelopeError):
            envelope.Resources(1, 1) * 1.5


class ContainerAndPodTest(unittest.TestCase):
    def test_container_requires_complete_equal_requests_and_limits(self) -> None:
        self.assertEqual(envelope.container_resources(_container("api")), envelope.Resources(250, 128 * 1024**2))
        cases = [
            {"name": "missing", "resources": {}},
            {"name": "memory", "resources": {"requests": {"cpu": "1"}, "limits": {"cpu": "1", "memory": "1Mi"}}},
            {"name": "unequal", "resources": {"requests": {"cpu": "1", "memory": "1Mi"}, "limits": {"cpu": "2", "memory": "1Mi"}}},
            {"name": "invalid", "resources": {"requests": {"cpu": "bad", "memory": "1Mi"}, "limits": {"cpu": "bad", "memory": "1Mi"}}},
        ]
        for container in cases:
            with self.subTest(container=container), self.assertRaisesRegex(envelope.EnvelopeError, container["name"]):
                envelope.container_resources(container)

    def test_effective_request_uses_largest_init_container(self) -> None:
        spec = {
            "containers": [_container("api", "250m", "128Mi"), _container("probe", "250m", "128Mi")],
            "initContainers": [_container("migrate", "1", "128Mi"), _container("warm", "100m", "512Mi")],
        }
        self.assertEqual(envelope.pod_effective(spec, "api"), envelope.Resources(1000, 512 * 1024**2))

    def test_sidecar_init_containers_are_refused(self) -> None:
        spec = {
            "containers": [_container("api", "250m", "128Mi")],
            "initContainers": [{**_container("log", "100m", "64Mi"), "restartPolicy": "Always"}],
        }
        with self.assertRaisesRegex(envelope.EnvelopeError, "sidecar init container log"):
            envelope.pod_effective(spec, "api")

    def test_pod_requires_an_app_container(self) -> None:
        with self.assertRaisesRegex(envelope.EnvelopeError, "no containers"):
            envelope.pod_effective({}, "empty")


class EnvelopeTest(unittest.TestCase):
    def test_occupancy_and_headroom(self) -> None:
        fixed = {"api": envelope.Resources(1250, 1280 * 1024**2), "redis": envelope.Resources(150, 256 * 1024**2)}
        occupancy = envelope.max_occupancy(fixed, envelope.Resources(650, 2 * 1024**3), 2)
        self.assertEqual(occupancy, envelope.Resources(2700, 5632 * 1024**2))
        headroom = envelope.check_fits(occupancy, {
            "requests.cpu": "4", "limits.cpu": "3", "requests.memory": "8Gi", "limits.memory": "7Gi",
        })
        self.assertEqual(headroom, envelope.Resources(300, 1536 * 1024**2))

    def test_occupancy_rejects_negative_workers(self) -> None:
        with self.assertRaises(envelope.EnvelopeError):
            envelope.max_occupancy({}, envelope.Resources(1, 1), -1)

    def test_quota_requires_all_keys_and_reports_each_overflow(self) -> None:
        with self.assertRaisesRegex(envelope.EnvelopeError, "limits.cpu"):
            envelope.check_fits(envelope.Resources(1, 1), {"requests.cpu": "1"})
        with self.assertRaisesRegex(envelope.EnvelopeError, r"requests.cpu.*limits.cpu.*requests.memory.*limits.memory"):
            envelope.check_fits(envelope.Resources(2000, 2 * 1024**2), {
                "requests.cpu": "1", "limits.cpu": "1", "requests.memory": "1Mi", "limits.memory": "1Mi",
            })

    def test_reconcile_includes_terminating_and_excludes_terminal_pods(self) -> None:
        running = {"metadata": {"name": "running"}, "status": {"phase": "Running"}, "spec": {"containers": [_container("app")]}}
        terminating = {"metadata": {"name": "terminating", "deletionTimestamp": "now"}, "status": {"phase": "Running"}, "spec": {"containers": [_container("app")]}}
        complete = {"metadata": {"name": "complete"}, "status": {"phase": "Succeeded"}, "spec": {"containers": [_container("app", "9", "9Gi")]}}
        result = envelope.reconcile([running, terminating, complete], {"used": {"requests.cpu": "500m", "requests.memory": "256Mi"}})
        self.assertEqual(result["computed"], envelope.Resources(500, 256 * 1024**2))
        self.assertEqual(result["used"], result["computed"])
        self.assertTrue(result["match"])
        self.assertFalse(envelope.reconcile([running], {"used": {"requests.cpu": "251m", "requests.memory": "128Mi"}})["match"])
        self.assertTrue(envelope.reconcile([running], {"used": {"requests.cpu": "251m", "requests.memory": "128Mi"}}, tolerance_m=1)["match"])

    def test_reconcile_requires_quota_usage_keys(self) -> None:
        with self.assertRaises(envelope.EnvelopeError):
            envelope.reconcile([], {"used": {}})

    def test_worker_resources(self) -> None:
        self.assertEqual(envelope.worker_job_resources(2048, "0.65"), envelope.Resources(650, 2 * 1024**3))
        for memory in (-1, 1.5, True):
            with self.subTest(memory=memory), self.assertRaises(envelope.EnvelopeError):
                envelope.worker_job_resources(memory, "1")


if __name__ == "__main__":
    unittest.main()
