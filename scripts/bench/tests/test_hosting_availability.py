from __future__ import annotations

import contextlib
import io
import json
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import hosting_availability  # noqa: E402


def _api_pod(name: str, uid: str, *, ready: bool = True, deleting: bool = False) -> dict:
    metadata = {
        "name": name,
        "uid": uid,
        "namespace": "bench-tenant",
        "labels": {"app.kubernetes.io/name": "bifrost-api"},
    }
    if deleting:
        metadata["deletionTimestamp"] = "2026-11-02T00:00:00Z"
    return {
        "metadata": metadata,
        "status": {
            "conditions": [{"type": "Ready", "status": "True" if ready else "False"}],
            "containerStatuses": [{"name": "api", "ready": ready}],
        },
    }


def _profile(*, warm_workers: int = 2, max_workers: int = 2) -> dict:
    return {
        "worker_policy": {
            "warm_workers": warm_workers,
            "max_workers": max_workers,
            "worker_memory_mib": 512,
            "largest_worker_mib": 512,
            "idle_seconds": 300,
        },
        "capacity": {
            "mixes": {"sdk-heavy": {"wf-sdk": 100}},
            "disk": {"name": "nvme0n1", "id": "259:0"},
            "seed": 17,
            "warmup_s": 10,
            "drain_s": 10,
        },
        "seed": {"workflow": 1100},
        "table_docs": 200,
    }


class MemoryRun:
    def __init__(self, _cluster, _kind, _profile_name, _profile, _backend, out: Path, _api_image) -> None:
        self.out = out
        self.result: dict = {"api_image_provenance": {"tag": "candidate:local"}}

    def save(self) -> None:
        self.out.mkdir(parents=True, exist_ok=True)
        (self.out / "result.json").write_text(json.dumps(self.result), encoding="utf-8")


class ReadyInjector:
    instances: list["ReadyInjector"] = []

    def __init__(self, _cluster, target: dict, survivor: dict, *, measured_offset_s: float, checkpoint) -> None:
        self.target, self.survivor = target, survivor
        self.measured_offset_s, self.checkpoint = measured_offset_s, checkpoint
        self.state = {"status": "waiting"}
        self.frames: list[dict] = []
        self.instances.append(self)

    def start(self) -> None:
        return None

    def observe(self, frame: dict) -> None:
        self.frames.append(frame)

    def cancel(self, _reason: str) -> None:
        self.state["status"] = "cancelled"

    def join(self, _timeout_s: float) -> dict:
        self.state = {
            "status": "ready",
            "target": {"name": self.target["metadata"]["name"], "uid": self.target["metadata"]["uid"]},
            "survivor": {"name": self.survivor["metadata"]["name"], "uid": self.survivor["metadata"]["uid"]},
            "replacement": {"name": "bifrost-api-c", "uid": "uid-c"},
        }
        self.checkpoint(self.state)
        return self.state


class ErrorInjector(ReadyInjector):
    def join(self, _timeout_s: float) -> dict:
        self.state = {"status": "error", "error": "RuntimeError: delete rejected", "attempts": 1}
        self.checkpoint(self.state)
        return self.state


class HostingAvailabilityTest(unittest.TestCase):
    def setUp(self) -> None:
        ReadyInjector.instances.clear()

    @staticmethod
    def _fixture(directory: Path) -> Path:
        source = directory / "ensure_bench_writes.py"
        source.write_text("fixture", encoding="utf-8")
        return source

    def test_raw_api_capture_requires_exactly_two_ready_labelled_tenant_replicas(self) -> None:
        cluster = Mock()
        cluster.kubectl.return_value = json.dumps({"items": [_api_pod("bifrost-api-a", "uid-a"), _api_pod("bifrost-api-b", "uid-b")]})

        target, survivor = hosting_availability.capture_api_pair(cluster)

        self.assertEqual((target["metadata"]["name"], survivor["metadata"]["uid"]), ("bifrost-api-a", "uid-b"))
        cluster.api_pod.assert_not_called()

    def test_run_resets_prepares_full_profile_holds_the_fixed_floor_and_retains_one_fault_attempt(self) -> None:
        profile = _profile()
        cluster = Mock()
        target, survivor = _api_pod("bifrost-api-a", "uid-a"), _api_pod("bifrost-api-b", "uid-b")
        cluster.api_image_evidence.return_value = {"api_pods": [{"pod": "bifrost-api-b"}, {"pod": "bifrost-api-c"}]}

        def step(_cluster, _ids, rate, _plan, _seed, _disk, **kwargs):
            kwargs["on_event"]({"event": "started", "started": 1.0})
            kwargs["on_event"]({"event": "request_sent", "request_id": "request", "sent": 12.0})
            kwargs["checkpoint"]({"phase": "sent", "sent": {"requests": [{"id": "request"}]}})
            kwargs["checkpoint"]({"phase": "outcomes", "outcomes": {"duplicates": []}})
            return {"overall": "pass", "rate": rate, "storage": {"before": {"ok": True}, "after": {"ok": True}}}

        with tempfile.TemporaryDirectory() as directory, \
             patch.object(hosting_availability.run, "load_profile", return_value=profile), \
             patch.object(hosting_availability.deploy, "check_profile") as check_profile, \
             patch.object(hosting_availability.run, "Run", MemoryRun), \
             patch.object(hosting_availability.deploy, "reset") as reset, \
             patch.object(hosting_availability.run, "_prepare", return_value={"workflows": {"wf-sdk": "sdk"}}) as prepare, \
             patch.object(hosting_availability.elasticity, "_assert_policy", side_effect=lambda _cluster, controls: controls), \
             patch.object(hosting_availability.elasticity, "wait_for_floor", return_value={"floor": "ready"}) as floor, \
             patch.object(hosting_availability, "capture_api_pair", return_value=(target, survivor)), \
             patch.object(hosting_availability, "ApiAvailabilityInjector", ReadyInjector), \
             patch.object(hosting_availability.search, "run_step", side_effect=step) as run_step:
            out = Path(directory) / "availability"
            fixture = self._fixture(Path(directory))
            cluster.exec_python.return_value = {"status": "pass", "table_rows": 1, "documents": 0}
            self.assertEqual(hosting_availability.run_arm("hosting-4gb-fixed-workers-2", "candidate:local", out, 3.0,
                                                          cluster=cluster, ensure_fixture=fixture), out)
            result = json.loads((out / "result.json").read_text(encoding="utf-8"))
            timeline = (out / "timeline.jsonl").read_text(encoding="utf-8")

        check_profile.assert_called_once_with(profile)
        prepare.assert_called_once_with(cluster, unittest.mock.ANY, profile, "fake", "candidate:local")
        floor.assert_called_once_with(cluster, 2)
        self.assertEqual(reset.call_count, 2)
        self.assertEqual(run_step.call_args.args[2], 3.0)
        self.assertEqual(run_step.call_args.args[3]["mix"], {"wf-sdk": 100})
        self.assertEqual(run_step.call_args.kwargs["on_event"], ReadyInjector.instances[0].observe)
        self.assertIn("checkpoint", run_step.call_args.kwargs)
        self.assertEqual(result["search_checkpoints"], {
            "sent": {"phase": "sent", "sent": {"requests": [{"id": "request"}]}},
            "outcomes": {"phase": "outcomes", "outcomes": {"duplicates": []}},
        })
        self.assertIn('"phase": "search:outcomes"', timeline)
        cluster.exec_python.assert_called_once()
        self.assertEqual(result["availability"]["fault"]["status"], "ready")
        self.assertEqual(result["availability"]["storage_endpoints"], {"before": {"ok": True}, "after": {"ok": True}})
        self.assertTrue(result["cleanup"]["verified"])

    def test_run_rejects_nonfixed_or_nonsizing_profiles_before_cluster_access(self) -> None:
        with tempfile.TemporaryDirectory() as directory, \
             patch.object(hosting_availability.run, "load_profile", return_value=_profile(warm_workers=0)) as load, \
             patch.object(hosting_availability, "Cluster") as cluster:
            with self.assertRaisesRegex(ValueError, "fixed worker floor"):
                hosting_availability.run_arm("hosting-4gb-elastic-warm0-max2", "candidate:local", Path(directory) / "out", 1.0,
                                             ensure_fixture=self._fixture(Path(directory)))
        load.assert_called_once_with("hosting-4gb-elastic-warm0-max2")
        cluster.assert_not_called()

    def test_run_requires_a_canonical_table_fixture_before_cluster_access(self) -> None:
        with tempfile.TemporaryDirectory() as directory, \
             patch.object(hosting_availability.run, "load_profile", return_value=_profile()), \
             patch.object(hosting_availability, "Cluster") as cluster:
            with self.assertRaisesRegex(FileNotFoundError, "canonical bench_writes fixture"):
                hosting_availability.run_arm("hosting-4gb-fixed-workers-2", "candidate:local", Path(directory) / "out", 1.0,
                                             ensure_fixture=Path(directory) / "missing.py")
        cluster.assert_not_called()

    def test_fault_failure_is_saved_before_the_final_reset_and_propagates(self) -> None:
        profile = _profile()
        cluster = Mock()
        target, survivor = _api_pod("bifrost-api-a", "uid-a"), _api_pod("bifrost-api-b", "uid-b")
        step = {"overall": "pass", "storage": {"before": {}, "after": {}}}
        with tempfile.TemporaryDirectory() as directory, \
             patch.object(hosting_availability.run, "load_profile", return_value=profile), \
             patch.object(hosting_availability.deploy, "check_profile"), \
             patch.object(hosting_availability.run, "Run", MemoryRun), \
             patch.object(hosting_availability.deploy, "reset") as reset, \
             patch.object(hosting_availability.run, "_prepare", return_value={}), \
             patch.object(hosting_availability.elasticity, "_assert_policy", side_effect=lambda _cluster, controls: controls), \
             patch.object(hosting_availability.elasticity, "wait_for_floor", return_value={}), \
             patch.object(hosting_availability, "capture_api_pair", return_value=(target, survivor)), \
             patch.object(hosting_availability, "ApiAvailabilityInjector", ErrorInjector), \
             patch.object(hosting_availability.search, "run_step", return_value=step):
            out = Path(directory) / "availability"
            with self.assertRaisesRegex(RuntimeError, "did not prove"):
                fixture = self._fixture(Path(directory))
                cluster.exec_python.return_value = {"status": "pass", "table_rows": 1, "documents": 0}
                hosting_availability.run_arm("hosting-4gb-fixed-workers-2", "candidate:local", out, 3.0,
                                             cluster=cluster, ensure_fixture=fixture)
            result = json.loads((out / "result.json").read_text(encoding="utf-8"))

        self.assertEqual(result["status"], "invalid")
        self.assertEqual(result["availability"]["fault"]["status"], "error")
        self.assertTrue(result["cleanup"]["verified"])
        self.assertEqual(reset.call_count, 2)

    def test_replacement_wait_records_each_raw_survivor_sample_and_aborts_on_unready_survivor(self) -> None:
        target = _api_pod("bifrost-api-a", "uid-a")
        survivor = _api_pod("bifrost-api-b", "uid-b")
        cluster = Mock()
        cluster.kubectl.return_value = json.dumps({"items": [target, _api_pod("bifrost-api-b", "uid-b", ready=False)]})
        samples: list[dict] = []

        with self.assertRaisesRegex(RuntimeError, "survivor API Pod disappeared or became unready"):
            hosting_availability.wait_for_replacement(
                cluster, target, survivor, {}, threading.Event(), on_sample=samples.append,
            )

        self.assertEqual(len(samples), 1)
        self.assertEqual(samples[0]["survivor"], {"name": "bifrost-api-b", "uid": "uid-b", "present": True, "ready": False})
        cluster.api_pod.assert_not_called()

    def test_api_delete_uses_a_loopback_uid_precondition_proxy(self) -> None:
        class Proxy:
            def __init__(self) -> None:
                self.stdout = io.StringIO("Starting to serve on 127.0.0.1:43123\n")
                self.returncode = None

            def poll(self):
                return self.returncode

            def terminate(self) -> None:
                self.returncode = 0

            def wait(self, timeout=None) -> None:
                self.returncode = 0

        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return None

            def read(self) -> bytes:
                return b'{"metadata":{"uid":"uid-a"}}'

        cluster = Mock()
        cluster.kubeconfig, cluster.context = Path("/tmp/kubeconfig"), "kind-owner"
        captured: dict = {}

        def request(request, timeout):
            captured.update({"url": request.full_url, "method": request.get_method(), "body": request.data, "timeout": timeout})
            return Response()

        with patch.object(hosting_availability.subprocess, "Popen", return_value=Proxy()) as popen, \
             patch.object(hosting_availability.select, "select", return_value=([popen.return_value.stdout], [], [])), \
             patch.object(hosting_availability.urllib.request, "urlopen", side_effect=request):
            result = hosting_availability.delete_api_pod_uid(cluster, _api_pod("bifrost-api-a", "uid-a"))

        self.assertEqual(result["metadata"]["uid"], "uid-a")
        self.assertIn("--address=127.0.0.1", popen.call_args.args[0])
        self.assertEqual(captured["method"], "DELETE")
        self.assertEqual(captured["url"], "http://127.0.0.1:43123/api/v1/namespaces/bench-tenant/pods/bifrost-api-a")
        self.assertEqual(json.loads(captured["body"]), {
            "apiVersion": "v1", "kind": "DeleteOptions", "preconditions": {"uid": "uid-a"},
        })

    def test_cli_binds_the_explicit_owner_root_and_acquires_the_lock_only_at_the_boundary(self) -> None:
        selected = Mock()
        with patch.object(hosting_availability, "host_lock", return_value=contextlib.nullcontext()) as locked, \
             patch.object(hosting_availability, "Cluster", return_value=selected) as cluster, \
             patch.object(hosting_availability, "run_arm", return_value=Path("/tmp/result")) as arm, \
            patch.object(sys, "argv", ["hosting_availability.py", "--profile", "hosting-4gb-fixed-workers-2", "--owner-root", "/tmp/owner", "--api-image", "candidate:local", "--out", "/tmp/result", "--rate", "3", "--table-fixture", "/tmp/ensure.py"]):
            hosting_availability.main()

        locked.assert_called_once_with()
        cluster.assert_called_once_with(Path("/tmp/owner"))
        arm.assert_called_once_with("hosting-4gb-fixed-workers-2", "candidate:local", Path("/tmp/result"), 3.0,
                                    cluster=selected, ensure_fixture=Path("/tmp/ensure.py"))


if __name__ == "__main__":
    unittest.main()
