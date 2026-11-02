from __future__ import annotations

import io
import json
import sys
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import cluster  # noqa: E402

SUMMARY = {
    "pods": [
        {
            "podRef": {"name": "bifrost-api-abc-1", "namespace": "bifrost-local", "uid": "u1"},
            "containers": [{
                "name": "api",
                "memory": {"workingSetBytes": 400 * 1024 * 1024, "rssBytes": 380 * 1024 * 1024},
                "cpu": {"usageNanoCores": 25_000_000},
            }],
        },
        {
            "podRef": {"name": "bifrost-worker-9f-x", "namespace": "bifrost-local", "uid": "u2"},
            "containers": [{"name": "worker", "memory": {"workingSetBytes": 85 * 1024 * 1024}, "cpu": {}}],
        },
        {
            "podRef": {"name": "coredns-1", "namespace": "kube-system", "uid": "u3"},
            "containers": [{"name": "coredns", "memory": {"workingSetBytes": 1}, "cpu": {}}],
        },
    ]
}


class StatsSummaryTest(unittest.TestCase):
    def test_only_namespace_containers_with_roles(self) -> None:
        rows = cluster.parse_stats_summary(SUMMARY, "bifrost-local")
        self.assertEqual([row["pod"] for row in rows], ["bifrost-api-abc-1", "bifrost-worker-9f-x"])
        api, worker = rows
        self.assertEqual(api["role"], "api")
        self.assertEqual(api["working_set_mib"], 400.0)
        self.assertEqual(api["rss_mib"], 380.0)
        self.assertEqual(api["cpu_millicores"], 25.0)
        self.assertEqual(worker["role"], "worker")
        self.assertEqual(worker["rss_mib"], 0.0)

    def test_roles(self) -> None:
        self.assertEqual(cluster.role_for_pod("bench-driver"), "bench")
        self.assertEqual(cluster.role_for_pod("postgres-0"), "infra")


class ProbeDeadlineTest(unittest.TestCase):
    def test_constructor_binds_guard_identity_to_an_explicit_owner_root(self) -> None:
        owner = Path("/tmp/bench-capacity-owner")
        identity = Mock()
        with patch.object(cluster.guard, "expected_identity", return_value=identity) as expected:
            instance = cluster.Cluster(owner)
        expected.assert_called_once_with(owner.resolve())
        self.assertIs(instance.identity, identity)
        self.assertEqual(instance.owner_root, owner.resolve())

    def test_status_probe_passes_its_deadline_to_kubectl(self) -> None:
        instance = cluster.Cluster.__new__(cluster.Cluster)
        instance.kubeconfig = Path("/tmp/bench-probe-kubeconfig")
        instance.context = "kind-bench-test"
        instance.namespace = "bench-tenant"
        with patch.object(cluster.subprocess, "run", return_value=Mock(returncode=0, stdout="{}")) as run:
            self.assertEqual(instance.kubectl("get", "pods", timeout_s=2.5), "{}")
        self.assertEqual(run.call_args.kwargs["timeout"], 2.5)

    def test_restart_waits_for_the_complete_ready_api_replica_set(self) -> None:
        instance = cluster.Cluster.__new__(cluster.Cluster)
        instance.kubectl = Mock()
        instance.ready_api_pods = Mock(return_value=[{"metadata": {"name": "bifrost-api-a"}}])
        instance.restart_api()
        instance.kubectl.assert_has_calls([
            (("rollout", "restart", "deployment/bifrost-api"),),
            (("rollout", "status", "deployment/bifrost-api", "--timeout=900s"),),
        ])
        instance.ready_api_pods.assert_called_once_with()

    def test_database_probe_passes_its_deadline_to_the_existing_driver(self) -> None:
        instance = cluster.Cluster.__new__(cluster.Cluster)
        instance.exec_python = Mock(return_value={"value": {"workers": []}})
        self.assertEqual(instance.driver_sql("select 1", timeout_s=2.5), {"workers": []})
        self.assertEqual(instance.exec_python.call_args.kwargs["timeout_s"], 2.5)
        self.assertEqual(instance.exec_python.call_args.args[0], "bench-driver")


class QuotaEventCollectionTest(unittest.TestCase):
    def test_collects_only_sanitized_quota_event_uid_and_count(self) -> None:
        instance = cluster.Cluster.__new__(cluster.Cluster)
        instance.kubectl = Mock(return_value=json.dumps({"items": [
            {"reason": "FailedCreate", "message": "exceeded quota secret=do-not-keep",
             "metadata": {"uid": "core-v1"}, "count": 2},
            {"reason": "FailedCreate", "message": "exceeded quota token=do-not-keep",
             "metadata": {"uid": "events-v1"}, "series": {"count": 3}},
            {"reason": "FailedCreate", "message": "exceeded quota modern-event",
             "metadata": {"uid": "series-wins"}, "count": 0, "series": {"count": 4}},
            {"reason": "FailedCreate", "message": "exceeded quota malformed-series",
             "metadata": {"uid": "bad-series"}, "count": 7, "series": {}},
            {"reason": "FailedCreate", "message": "some unrelated failure",
             "metadata": {"uid": "ignored"}, "count": 9},
        ]}))
        self.assertEqual(instance.quota_denial_events(), [
            {"uid": "core-v1", "count": 2}, {"uid": "events-v1", "count": 3},
            {"uid": "series-wins", "count": 4}, {"uid": "bad-series", "count": None},
        ])
        self.assertEqual(instance.kubectl.call_args.kwargs["timeout_s"], cluster._QUOTA_EVENT_PROBE_TIMEOUT_S)


class DbCountersTest(unittest.TestCase):
    def test_parse_db_counters(self) -> None:
        raw = ('{"xact": 12, "rows_written": 3, "wal_bytes": 4096, '
               '"wal_generated_bytes": 2048, "wal_stats_reset": "2026-10-30T00:00:00Z", '
               '"db_bytes": 1048576, "tables": {"users": [0, 7]}}\n')
        counters = cluster.parse_db_counters(raw)
        self.assertEqual(counters["xact"], 12)
        self.assertEqual(counters["wal_bytes"], 4096)
        self.assertEqual(counters["wal_generated_bytes"], 2048)
        self.assertIs(type(counters["wal_generated_bytes"]), int)
        self.assertEqual(counters["wal_stats_reset"], "2026-10-30T00:00:00Z")
        self.assertEqual(counters["tables"], {"users": [0, 7]})

    def test_empty_table_list_is_an_empty_dict(self) -> None:
        raw = ('{"xact": 1, "rows_written": 0, "wal_bytes": 0, '
               '"wal_generated_bytes": 0, "wal_stats_reset": null, "db_bytes": 0, "tables": null}')
        self.assertEqual(cluster.parse_db_counters(raw)["tables"], {})

    def test_counter_query_keeps_lsn_advance_and_pg_stat_wal_generation_separate(self) -> None:
        self.assertIn("pg_wal_lsn_diff(pg_current_wal_lsn(), '0/0')", cluster.DB_COUNTERS_SQL)
        self.assertIn("'wal_generated_bytes', (select wal_bytes from pg_stat_wal)", cluster.DB_COUNTERS_SQL)
        self.assertIn("'wal_stats_reset', (select stats_reset from pg_stat_wal)", cluster.DB_COUNTERS_SQL)


PROCESSES = """M /proc/1/smaps_rollup 24360
M /proc/15/smaps_rollup 49144
M /proc/40/smaps_rollup 20480
M /proc/41/smaps_rollup 172032
M /proc/52/smaps_rollup 30720
M /proc/1942/smaps_rollup 248
S 1 (python) S 0 1 1 0
S 15 (python) S 1 1 1 0
S 40 (python) S 15 1 1 0
S 41 (my (odd) name) S 1 1 1 0
S 52 (python) S 41 1 1 0
S 1942 (sh) R 0 1942 1942 0
"""


class WorkerProcessesTest(unittest.TestCase):
    def test_depth_from_the_launcher_and_exec_helpers_dropped(self) -> None:
        rows = {row["pid"]: row for row in cluster.parse_worker_processes(PROCESSES)}
        self.assertNotIn(1942, rows)
        self.assertEqual((rows[1]["depth"], rows[15]["depth"], rows[40]["depth"]), (0, 1, 2))
        self.assertEqual(rows[41]["depth"], 1)
        self.assertEqual(rows[52]["private_mib"], 30.0)
        self.assertEqual(rows[41]["private_mib"], 168.0)


class LogSourcesTest(unittest.TestCase):
    def test_api_and_worker_log_sources_include_container_and_immutable_pod_identity(self) -> None:
        instance = cluster.Cluster.__new__(cluster.Cluster)
        instance.ready_api_pods = lambda: [
            {"metadata": {"name": "bifrost-api-2", "uid": "api-uid-2"}},
            {"metadata": {"name": "bifrost-api-1", "uid": "api-uid-1"}},
        ]
        pods = {"items": [
            {"metadata": {"name": "bifrost-worker-1", "uid": "worker-uid"},
             "status": {"containerStatuses": [{"name": "worker"}]}},
            {"metadata": {"name": "bifrost-worker-no-container", "uid": "ignored"}, "status": {}},
        ]}

        def kubectl(*args, **_kwargs):
            if args[:3] == ("get", "pods", "-o"):
                return json.dumps(pods)
            return f"logs:{args[1]}:{args[3]}"

        instance.kubectl = kubectl
        self.assertEqual(instance.api_log_sources(), [
            {"pod": "bifrost-api-1", "pod_uid": "api-uid-1", "container": "api", "logs": "logs:bifrost-api-1:api"},
            {"pod": "bifrost-api-2", "pod_uid": "api-uid-2", "container": "api", "logs": "logs:bifrost-api-2:api"},
        ])
        self.assertEqual(instance.worker_log_sources(), [{
            "pod": "bifrost-worker-1", "pod_uid": "worker-uid", "container": "worker", "logs": "logs:bifrost-worker-1:worker",
        }])


class ApiPodsTest(unittest.TestCase):
    @staticmethod
    def _pod(name: str, uid: str, *, ready: bool = True, deleting: bool = False, image_id: str = "") -> dict:
        metadata = {"name": name, "uid": uid}
        if deleting:
            metadata["deletionTimestamp"] = "2026-11-02T00:00:00Z"
        return {
            "metadata": metadata,
            "status": {
                "conditions": [{"type": "Ready", "status": "True" if ready else "False"}],
                "containerStatuses": [{"name": "api", "imageID": image_id}],
            },
        }

    def _cluster(self, replicas: int, pods: list[dict]) -> cluster.Cluster:
        instance = cluster.Cluster.__new__(cluster.Cluster)

        def kubectl(*args, **_kwargs):
            if args[:3] == ("get", "deployment/bifrost-api", "-o"):
                return json.dumps({"spec": {"replicas": replicas}})
            if args[:4] == ("get", "pods", "-l", "app.kubernetes.io/name=bifrost-api"):
                return json.dumps({"items": pods})
            raise AssertionError(args)

        instance.kubectl = kubectl
        return instance

    def test_ready_api_pods_requires_exact_active_ready_replicas_and_sorts_records(self) -> None:
        instance = self._cluster(2, [self._pod("bifrost-api-b", "b"), self._pod("bifrost-api-a", "a")])
        self.assertEqual(instance.api_pod_records(), [
            {"pod": "bifrost-api-a", "pod_uid": "a"}, {"pod": "bifrost-api-b", "pod_uid": "b"},
        ])
        self.assertEqual(instance.api_pod(), ("bifrost-api-a", "a"))

    def test_ready_api_pods_rejects_zero_unready_and_extra_active_replicas(self) -> None:
        for replicas, pods, message in (
            (0, [], "positive replica count"),
            (2, [self._pod("bifrost-api-a", "a"), self._pod("bifrost-api-b", "b", ready=False)], "expected 2 ready API pods"),
            (1, [self._pod("bifrost-api-a", "a"), self._pod("bifrost-api-b", "b")], "expected 1 active API pods"),
        ):
            with self.subTest(replicas=replicas, message=message):
                with self.assertRaisesRegex(RuntimeError, message):
                    self._cluster(replicas, pods).ready_api_pods()

    def test_image_evidence_checks_every_ready_replica_and_keeps_single_api_field(self) -> None:
        instance = self._cluster(2, [
            self._pod("bifrost-api-a", "a", image_id=f"containerd://{CONFIG}"),
            self._pod("bifrost-api-b", "b", image_id=f"containerd://{CONFIG}"),
        ])
        provenance = {"node": {"index_id": INDEX, "manifest_id": MANIFEST, "config_id": CONFIG}}
        self.assertEqual(instance.api_image_evidence(provenance), {
            "api_pods": [
                {"pod": "bifrost-api-a", "pod_uid": "a", "image_id": f"containerd://{CONFIG}", "digest": CONFIG},
                {"pod": "bifrost-api-b", "pod_uid": "b", "image_id": f"containerd://{CONFIG}", "digest": CONFIG},
            ],
        })

        single = self._cluster(1, [self._pod("bifrost-api-a", "a", image_id=f"containerd://{CONFIG}")])
        self.assertEqual(single.api_image_evidence(provenance)["api_pod"]["pod"], "bifrost-api-a")

        wrong = self._cluster(2, [
            self._pod("bifrost-api-a", "a", image_id=f"containerd://{CONFIG}"),
            self._pod("bifrost-api-b", "b", image_id=f"containerd://{OTHER}"),
        ])
        with self.assertRaisesRegex(RuntimeError, "outside the verified OCI chain"):
            wrong.api_image_evidence(provenance)


if __name__ == "__main__":
    unittest.main()


class ExecPythonTest(unittest.TestCase):
    def test_payload_travels_on_stdin_not_the_command_line(self) -> None:
        payload = "x" * 200_000  # above Linux's 128 KiB single-argument limit
        script = Path(cluster.__file__)
        calls: list[dict] = []

        def fake_run(command, input, capture_output, timeout):  # noqa: A002 - mirrors subprocess.run
            calls.append({"command": command, "input": input})
            return type("Completed", (), {"returncode": 0, "stdout": b'{"ok": true}\n', "stderr": b""})()

        instance = cluster.Cluster.__new__(cluster.Cluster)
        instance.kubeconfig, instance.context = Path("/tmp/kubeconfig"), "kind-test"
        original = cluster.subprocess.run
        cluster.subprocess.run = fake_run
        try:
            self.assertEqual(instance.exec_python("bench-driver", script, ["outcomes", payload]), {"ok": True})
        finally:
            cluster.subprocess.run = original
        (call,) = calls
        self.assertTrue(all(len(argument) < 4096 for argument in call["command"]))
        source = script.read_bytes()
        self.assertEqual(call["input"], source + payload.encode("utf-8"))
        self.assertEqual(call["command"][-2:], [str(len(source)), "outcomes"])


class UidDeleteTest(unittest.TestCase):
    def test_delete_uses_loopback_proxy_and_uid_precondition(self) -> None:
        captured: dict = {}

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
                return b'{"metadata":{"uid":"redis-old"}}'

        instance = cluster.Cluster.__new__(cluster.Cluster)
        instance.kubeconfig, instance.context = Path("/tmp/kubeconfig"), "kind-test"
        instance.verify = lambda: None
        original_popen = cluster.subprocess.Popen
        original_select = cluster.select.select
        original_open = cluster.urllib.request.urlopen

        def fake_popen(command, **kwargs):
            captured["command"] = command
            captured["popen"] = kwargs
            return Proxy()

        def fake_open(request, timeout):
            captured["url"] = request.full_url
            captured["method"] = request.get_method()
            captured["body"] = request.data
            captured["content_type"] = request.get_header("Content-type")
            captured["timeout"] = timeout
            return Response()

        cluster.subprocess.Popen = fake_popen
        cluster.select.select = lambda *_args: ([_args[0][0]], [], [])
        cluster.urllib.request.urlopen = fake_open
        try:
            response = instance.delete_pod_uid("redis-0", "redis-old")
        finally:
            cluster.subprocess.Popen = original_popen
            cluster.select.select = original_select
            cluster.urllib.request.urlopen = original_open

        self.assertEqual(response["metadata"]["uid"], "redis-old")
        self.assertIn("--address=127.0.0.1", captured["command"])
        self.assertIn("--port=0", captured["command"])
        self.assertEqual(captured["url"], "http://127.0.0.1:43123/api/v1/namespaces/bench-tenant/pods/redis-0")
        self.assertEqual(captured["method"], "DELETE")
        self.assertEqual(json.loads(captured["body"]), {
            "apiVersion": "v1", "kind": "DeleteOptions", "preconditions": {"uid": "redis-old"},
        })


INDEX = "sha256:" + "1" * 64
MANIFEST = "sha256:" + "2" * 64
CONFIG = "sha256:" + "3" * 64
OTHER = "sha256:" + "4" * 64


class ImageProvenanceTest(unittest.TestCase):
    def test_index_resolves_the_exact_node_platform_to_its_config(self) -> None:
        content = {
            INDEX: json.dumps({"manifests": [
                {"digest": OTHER, "platform": {"os": "linux", "architecture": "arm64"}},
                {"digest": MANIFEST, "platform": {"os": "linux", "architecture": "amd64"}},
            ]}),
            MANIFEST: json.dumps({"config": {"digest": CONFIG}}),
        }
        self.assertEqual(
            cluster.resolve_oci_chain(INDEX, "linux", "amd64", content.__getitem__),
            {"index_id": INDEX, "manifest_id": MANIFEST, "config_id": CONFIG},
        )

    def test_tag_mismatch_fails_before_a_deploy_can_use_it(self) -> None:
        instance = cluster.Cluster.__new__(cluster.Cluster)
        instance._node_platform = lambda: ("linux", "amd64")
        contents = {
            INDEX: json.dumps({"manifests": [{"digest": MANIFEST, "platform": {"os": "linux", "architecture": "amd64"}}]}),
            MANIFEST: json.dumps({"config": {"digest": CONFIG}}),
        }
        instance._node_command = lambda *args: contents[args[-1]] if args[0] == "ctr" else json.dumps({"status": {"id": OTHER}})
        with self.assertRaisesRegex(RuntimeError, "resolves to"):
            instance.image_provenance("bifrost-api:selected", INDEX)

    def test_legacy_config_root_requires_its_platform_and_config_shape(self) -> None:
        valid = json.dumps({"rootfs": {"type": "layers"}, "os": "linux", "architecture": "amd64"})
        self.assertEqual(
            cluster.resolve_oci_chain(CONFIG, "linux", "amd64", lambda _: valid),
            {"index_id": None, "manifest_id": None, "config_id": CONFIG},
        )
        with self.assertRaisesRegex(RuntimeError, "neither an index"):
            cluster.resolve_oci_chain(CONFIG, "linux", "amd64", lambda _: "{}")

    def test_api_pod_image_outside_the_verified_chain_fails(self) -> None:
        instance = cluster.Cluster.__new__(cluster.Cluster)
        instance.ready_api_pods = lambda: [{
            "metadata": {"name": "bifrost-api-1", "uid": "api-uid"},
            "status": {"containerStatuses": [{"name": "api", "imageID": f"docker-pullable://api@{OTHER}"}]},
        }]
        provenance = {"node": {"index_id": INDEX, "manifest_id": MANIFEST, "config_id": CONFIG}}
        with self.assertRaisesRegex(RuntimeError, "outside the verified OCI chain"):
            instance.api_image_evidence(provenance)


class _Process:
    def __init__(self, stdout: bytes = b"", *, running: bool = False) -> None:
        self.stdout = io.BytesIO(stdout)
        self.stderr = io.BytesIO()
        self.stdin = io.BytesIO()
        self.pid = 54321
        self.returncode = None if running else 0

    def poll(self):
        return self.returncode

    def wait(self, timeout=None):
        self.returncode = 0
        return 0


def _finished_writer() -> threading.Thread:
    writer = threading.Thread(target=lambda: None)
    writer.start()
    return writer


class FramedPythonTest(unittest.TestCase):
    def _framed(self, output: bytes, remote_cancel=Mock(), *, running: bool = False):
        process = _Process(output, running=running)
        framed = cluster.FramedPython(process, Path("/tmp/loadgen.py"), _finished_writer(), [], remote_cancel)
        return framed, process

    def test_malformed_and_error_frames_cancel_the_remote_driver(self) -> None:
        for output in (b"{not json}\n", b'{"event":"error","error":"driver error"}\n'):
            with self.subTest(output=output), patch.object(cluster.os, "killpg") as killpg:
                order: list[str] = []
                remote = Mock(side_effect=lambda: order.append("delete"))
                killpg.side_effect = lambda *_: order.append("reap")
                framed, _ = self._framed(output, remote, running=True)
                with self.assertRaisesRegex(RuntimeError, "frame error"):
                    framed.result(1, lambda _: None)
                self.assertEqual(order, ["delete", "reap"])

    def test_timeout_deletes_remote_driver_before_reaping_local_kubectl(self) -> None:
        class HangingStdout:
            def __init__(self) -> None:
                self.release = threading.Event()

            def __iter__(self):
                return self

            def __next__(self):
                self.release.wait()
                raise StopIteration

        with patch.object(cluster.os, "killpg") as killpg:
            order: list[str] = []
            remote = Mock(side_effect=lambda: order.append("delete"))
            killpg.side_effect = lambda *_: order.append("reap")
            process = _Process(running=True)
            stdout = HangingStdout()
            process.stdout = stdout
            framed = cluster.FramedPython(process, Path("/tmp/loadgen.py"), _finished_writer(), [], remote)
            with self.assertRaises(TimeoutError):
                framed.result(0.001, lambda _: None)
            stdout.release.set()
            self.assertEqual(order, ["delete", "reap"])

    def test_cleanup_failure_surfaces_after_local_reap(self) -> None:
        with patch.object(cluster.os, "killpg") as killpg:
            framed, _ = self._framed(b"", Mock(side_effect=RuntimeError("delete failed")), running=True)
            with self.assertRaises(cluster.RemoteCleanupError):
                framed.cancel()
            killpg.assert_called_once()

    def test_normal_final_frame_does_not_delete_the_driver(self) -> None:
        remote = Mock()
        framed, _ = self._framed(b'{"ok":true}\n', remote)
        self.assertEqual(framed.result(1, lambda _: None), {"ok": True})
        remote.assert_not_called()

    def test_start_exec_cancellation_uses_the_captured_driver_uid_before_local_reap(self) -> None:
        instance = cluster.Cluster.__new__(cluster.Cluster)
        instance.kubeconfig, instance.context = Path("/tmp/kubeconfig"), "kind-test"
        instance.verify = Mock()
        instance._bench_driver_identity = Mock(return_value=("bench-driver", "captured-uid"))
        order: list[object] = []
        instance._delete_bench_driver_uid = Mock(side_effect=lambda uid: order.append(("delete", uid)))
        process = _Process(running=True)
        with patch.object(cluster.subprocess, "Popen", return_value=process), \
             patch.object(cluster.os, "killpg", side_effect=lambda *_: order.append(("reap", None))):
            framed = instance.start_exec_python("bench-driver", Path(cluster.__file__), ["step", "{}"], cancel_driver=True)
            framed.cancel()
        self.assertEqual(order, [("delete", "captured-uid"), ("reap", None)])


class UidDeleteGuardsTest(unittest.TestCase):
    def test_rejects_nonallowlisted_pod_without_starting_a_proxy(self) -> None:
        instance = cluster.Cluster.__new__(cluster.Cluster)
        instance.verify = Mock()
        with patch.object(cluster.subprocess, "Popen") as popen:
            with self.assertRaises(cluster.guard.GuardError):
                instance.delete_pod_uid("anything", "uid", namespace="other")
        popen.assert_not_called()

    def test_always_reaps_proxy_after_request_failure(self) -> None:
        class Proxy:
            def __init__(self) -> None:
                self.stdout = io.StringIO("Starting to serve on 127.0.0.1:43123\n")
                self.returncode = None
                self.terminated = False

            def poll(self):
                return self.returncode

            def terminate(self):
                self.terminated = True
                self.returncode = 0

            def wait(self, timeout=None):
                self.returncode = 0

        proxy = Proxy()
        instance = cluster.Cluster.__new__(cluster.Cluster)
        instance.kubeconfig, instance.context = Path("/tmp/kubeconfig"), "kind-test"
        instance.verify = Mock()
        with patch.object(cluster.subprocess, "Popen", return_value=proxy), \
             patch.object(cluster.select, "select", return_value=([proxy.stdout], [], [])), \
             patch.object(cluster.urllib.request, "urlopen", side_effect=OSError("refused")):
            with self.assertRaises(OSError):
                instance.delete_pod_uid("redis-0", "uid")
        self.assertTrue(proxy.terminated)
