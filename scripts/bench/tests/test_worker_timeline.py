from __future__ import annotations

import json
import sys
import tempfile
import unittest
from contextlib import redirect_stderr
from io import StringIO
from pathlib import Path
from unittest.mock import patch


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import worker_timeline  # noqa: E402


class FakeClock:
    def __init__(self) -> None:
        self.wall_s = 1_000.0
        self.monotonic_s = 10.0

    def time(self) -> float:
        return self.wall_s

    def monotonic(self) -> float:
        return self.monotonic_s

    def sleep(self, seconds: float) -> None:
        self.wall_s += seconds
        self.monotonic_s += seconds


class FakeCluster:
    namespace = "bench-tenant"

    def __init__(self, *, worker_rows: list[dict] | None = None, work: object | None = None) -> None:
        self.worker_rows = worker_rows if worker_rows is not None else []
        self.work = work if work is not None else {
            "executions": 2, "agent_runs": 1, "dispatches": 3, "claimed_dispatches": 1,
        }
        self.verified = 0
        self.sql: list[str] = []
        self.sql_timeouts: list[float] = []
        self.kubectl_calls: list[tuple[tuple[str, ...], dict]] = []

    def verify(self) -> None:
        self.verified += 1

    def driver_sql(self, sql: str, *, timeout_s: float = 3600) -> object:
        self.sql.append(sql)
        self.sql_timeouts.append(timeout_s)
        if sql == worker_timeline.WORKERS_SQL:
            return {"sample_time_epoch_s": 100.0, "workers": self.worker_rows}
        if sql == worker_timeline.WORK_SQL:
            if isinstance(self.work, BaseException):
                raise self.work
            return self.work
        raise AssertionError(f"unexpected SQL: {sql}")

    def kubectl(self, *args: str, **kwargs: object) -> str:
        self.kubectl_calls.append((args, kwargs))
        if args[:2] == ("get", "pods"):
            return json.dumps({"items": [
                {
                    "metadata": {
                        "name": "bifrost-worker-safe", "namespace": "bench-tenant", "uid": "pod-1",
                        "creationTimestamp": "2026-10-31T00:00:00Z",
                    },
                    "spec": {"containers": [{
                        "name": "worker",
                        "resources": {
                            "requests": {"cpu": "650m", "memory": "2Gi"},
                            "limits": {"cpu": "650m", "memory": "2Gi"},
                        },
                    }]},
                    "status": {
                        "phase": "Running", "startTime": "2026-10-31T00:00:01Z",
                        "conditions": [{
                            "type": "Ready", "status": "True", "lastTransitionTime": "2026-10-31T00:00:02Z",
                        }],
                        "containerStatuses": [{
                            "name": "worker", "ready": True, "restartCount": 0,
                            "state": {"running": {"startedAt": "2026-10-31T00:00:01Z"}},
                        }],
                    },
                }
            ]})
        if args[:3] == ("get", "resourcequota", "tenant-envelope"):
            return json.dumps({"status": {
                "hard": {
                    "requests.cpu": "4", "limits.cpu": "4", "requests.memory": "8Gi",
                    "limits.memory": "8Gi", "pods": "20", "not-allowed": "hidden",
                },
                "used": {
                    "requests.cpu": "1", "limits.cpu": "1", "requests.memory": "2Gi",
                    "limits.memory": "2Gi", "pods": "6", "not-allowed": "hidden",
                },
            }})
        raise AssertionError(f"unexpected kubectl: {args}")


def worker(*, heartbeat: object = 95.0, active_runs: object = 1, budget: object = 1000) -> dict:
    return {
        "worker_id": "worker-1", "state": "ready", "resource_class": "standard",
        "job_name": "bifrost-worker-safe", "job_uid": "job-1", "pod_uid": "pod-1",
        "created_at_epoch_s": 80.0, "ready_at_epoch_s": 90.0,
        "heartbeat_at_epoch_s": heartbeat, "ended_at_epoch_s": None,
        "active_runs": active_runs, "memory_pressured": False,
        "memory_budget_bytes": budget, "memory_reserved_bytes": 100,
        "memory_limit_bytes": 2000, "runs_handled": 4,
        "forbidden_token": "must never appear",
    }


class WorkerSnapshotTest(unittest.TestCase):
    def test_worker_sql_scopes_job_rows_to_the_tenant_and_selects_version(self) -> None:
        self.assertIn("version', version", worker_timeline.WORKERS_SQL)
        self.assertIn("form = 'job'", worker_timeline.WORKERS_SQL)
        self.assertIn("kubernetes_namespace = 'bench-tenant'", worker_timeline.WORKERS_SQL)

    def test_stale_heartbeat_is_explicit(self) -> None:
        rows = [worker(), {**worker(heartbeat=70.0), "worker_id": "worker-2", "state": "draining"}]
        snapshot = worker_timeline.worker_snapshot(FakeCluster(worker_rows=rows), stale_after_s=15.0)

        self.assertEqual(snapshot["sample_time_epoch_s"], 100.0)
        self.assertEqual([row["state"] for row in snapshot["workers"]], ["ready", "draining"])
        self.assertEqual(snapshot["workers"][0]["heartbeat_freshness"]["state"], "fresh")
        self.assertEqual(snapshot["workers"][1]["heartbeat_freshness"]["state"], "stale")
        self.assertEqual(snapshot["workers"][1]["heartbeat_freshness"]["age_s"], 30.0)

    def test_unknown_numeric_and_missing_heartbeat_never_become_zero(self) -> None:
        snapshot = worker_timeline.worker_snapshot(
            FakeCluster(worker_rows=[worker(heartbeat=None, active_runs="not-a-number", budget=float("nan"))]),
            stale_after_s=15.0,
        )

        reported = snapshot["workers"][0]["reported_heartbeat_state"]
        self.assertEqual(snapshot["workers"][0]["heartbeat_freshness"], {"state": "unknown", "age_s": None})
        self.assertEqual(reported["active_runs"], {"status": "unknown", "value": None})
        self.assertEqual(reported["memory_budget_bytes"], {"status": "unknown", "value": None})
        self.assertNotIn("forbidden_token", repr(snapshot))

    def test_safe_allowlists_exclude_raw_pod_and_quota_fields(self) -> None:
        cluster = FakeCluster(worker_rows=[worker()])
        pods = worker_timeline.pod_snapshot(cluster)
        quota = worker_timeline.quota_snapshot(cluster)

        self.assertEqual(pods["status"], "observed")
        self.assertEqual(pods["pods"][0]["name"], "bifrost-worker-safe")
        self.assertEqual(
            pods["pods"][0]["containers"][0]["resources"]["requests"],
            {"cpu": "650m", "memory": "2Gi"},
        )
        self.assertNotIn("annotations", repr(pods))
        self.assertNotIn("env", repr(pods))
        self.assertEqual(quota["hard"]["pods"], "20")
        self.assertEqual(quota["used"]["limits.cpu"], "1")
        self.assertNotIn("not-allowed", repr(quota))

    def test_pending_pod_keeps_spec_resources_and_unknown_readiness(self) -> None:
        class PendingCluster(FakeCluster):
            def kubectl(self, *args: str, **kwargs: object) -> str:
                if args[:2] == ("get", "pods"):
                    return json.dumps({"items": [{
                        "metadata": {
                            "name": "bifrost-worker-pending", "namespace": "bench-tenant", "uid": "pod-2",
                            "creationTimestamp": "2026-10-31T00:00:00Z",
                            "deletionTimestamp": "2026-10-31T00:01:00Z",
                        },
                        "spec": {"containers": [{
                            "name": "worker", "image": "bifrost-api:stock",
                            "resources": {
                                "requests": {"cpu": "650m", "memory": "2Gi"},
                                "limits": {"cpu": "650m", "memory": "2Gi"},
                            },
                        }]},
                        "status": {"phase": "Pending"},
                    }]})
                return super().kubectl(*args, **kwargs)

        pod = worker_timeline.pod_snapshot(PendingCluster())["pods"][0]

        self.assertEqual(pod["ready"]["value"], None)
        self.assertEqual(pod["deletion_at"], "2026-10-31T00:01:00Z")
        self.assertEqual(pod["containers"], [{
            "name": "worker", "ready": None, "restart_count": None, "started_at": None,
            "image": "bifrost-api:stock", "image_id": None,
            "resources": {
                "requests": {"cpu": "650m", "memory": "2Gi"},
                "limits": {"cpu": "650m", "memory": "2Gi"},
            },
        }])

    def test_missing_quota_views_fail_closed(self) -> None:
        class IncompleteQuota(FakeCluster):
            def kubectl(self, *args: str, **kwargs: object) -> str:
                if args[:3] == ("get", "resourcequota", "tenant-envelope"):
                    return json.dumps({"status": {"hard": {}, "used": {}}})
                return super().kubectl(*args, **kwargs)

        with self.assertRaisesRegex(ValueError, "quota status"):
            worker_timeline.quota_snapshot(IncompleteQuota())


class RunSafetyTest(unittest.TestCase):
    def test_state_transition_is_retained_as_separate_raw_frames(self) -> None:
        class TransitionCluster(FakeCluster):
            def __init__(self) -> None:
                super().__init__()
                self.rows = [[worker()], [{**worker(), "state": "draining"}]]

            def driver_sql(self, sql: str, *, timeout_s: float = 3600) -> object:
                if sql == worker_timeline.WORKERS_SQL:
                    return {"sample_time_epoch_s": 100.0, "workers": self.rows.pop(0)}
                return super().driver_sql(sql, timeout_s=timeout_s)

        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "timeline.jsonl"
            worker_timeline.run(
                output, duration_s=2.0, interval_s=1.0, max_workers=1, warm_workers=0,
                cluster=TransitionCluster(), clock=FakeClock(),
            )
            frames = [json.loads(line) for line in output.read_text(encoding="utf-8").splitlines()]
            frames = [frame for frame in frames if frame["event"] == "sample"]

        self.assertEqual([frame["worker_db"]["workers"][0]["state"] for frame in frames], ["ready", "draining"])

    def test_failure_checkpoint_is_appended_and_retained(self) -> None:
        class BrokenCluster(FakeCluster):
            def kubectl(self, *args: str, **kwargs: object) -> str:
                raise RuntimeError("sensitive subprocess output must not be copied")

        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "timeline.jsonl"
            with self.assertRaises(RuntimeError):
                worker_timeline.run(
                    output, duration_s=1.0, interval_s=1.0, max_workers=1, warm_workers=0,
                    cluster=BrokenCluster(worker_rows=[worker()]), clock=FakeClock(),
                )
            frames = [json.loads(line) for line in output.read_text(encoding="utf-8").splitlines()]

        self.assertEqual(frames[-1]["event"], "failure")
        self.assertEqual(frames[-1]["failure"], {"stage": "pods", "error_class": "RuntimeError"})
        self.assertEqual(frames[-1]["worker_db"]["workers"][0]["worker_id"], "worker-1")
        self.assertIn("work", frames[-1])
        self.assertNotIn("pods", frames[-1])
        self.assertNotIn("sensitive subprocess output", repr(frames[-1]))
        self.assertIn("started_wall_epoch_s", frames[-1]["timing"])
        self.assertIn("finished_monotonic_s", frames[-1]["timing"])

    def test_work_query_failure_is_a_retained_failure_checkpoint(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "timeline.jsonl"
            with self.assertRaises(RuntimeError):
                worker_timeline.run(
                    output, duration_s=1.0, interval_s=1.0, max_workers=1, warm_workers=0,
                    cluster=FakeCluster(
                        worker_rows=[worker()], work=RuntimeError("secret connection string"),
                    ),
                    clock=FakeClock(),
                )
            frames = [json.loads(line) for line in output.read_text(encoding="utf-8").splitlines()]

        self.assertEqual(frames[-1]["failure"], {"stage": "work", "error_class": "RuntimeError"})
        self.assertNotIn("secret connection string", repr(frames[-1]))

    def test_slow_probe_records_missed_intervals_without_a_catchup_burst(self) -> None:
        clock = FakeClock()

        class SlowCluster(FakeCluster):
            def driver_sql(self, sql: str, *, timeout_s: float = 3600) -> object:
                if sql == worker_timeline.WORKERS_SQL and not getattr(self, "slow", False):
                    self.slow = True
                    clock.sleep(2.5)
                return super().driver_sql(sql, timeout_s=timeout_s)

        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "timeline.jsonl"
            worker_timeline.run(
                output, duration_s=3.5, interval_s=1.0, max_workers=2, warm_workers=1,
                cluster=SlowCluster(worker_rows=[worker()]), clock=clock,
            )
            frames = [json.loads(line) for line in output.read_text(encoding="utf-8").splitlines()]

        samples = [frame for frame in frames if frame["event"] == "sample"]
        gaps = [frame for frame in frames if frame["event"] == "cadence_gap"]
        self.assertEqual(gaps[0]["cadence"]["missed_intervals"], 2)
        self.assertEqual(len(samples), 2)
        self.assertEqual(samples[1]["cadence"]["scheduled_monotonic_s"], 13.0)

    def test_refuses_existing_path_before_cluster_access(self) -> None:
        cluster = FakeCluster()
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "timeline.jsonl"
            output.write_text("existing\n", encoding="utf-8")
            with self.assertRaisesRegex(FileExistsError, "fresh output"):
                worker_timeline.run(
                    output, duration_s=1.0, interval_s=1.0, max_workers=1, warm_workers=0,
                    cluster=cluster, clock=FakeClock(),
                )

        self.assertEqual(cluster.verified, 0)

    def test_warm_four_is_a_valid_max_four_arm(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "timeline.jsonl"
            worker_timeline.run(
                output, duration_s=1.0, interval_s=1.0, max_workers=4, warm_workers=4,
                cluster=FakeCluster(worker_rows=[worker()]), clock=FakeClock(),
            )
            frames = [json.loads(line) for line in output.read_text(encoding="utf-8").splitlines()]

        sample = next(frame for frame in frames if frame["event"] == "sample")
        self.assertEqual(sample["experiment"], {"max_workers": 4, "warm_workers": 4})
        session = next(frame for frame in frames if frame["event"] == "session_started")
        self.assertEqual(session["probe_timeout_s"], 5.0)

    def test_experiment_arms_reject_boolean_and_over_max_warm_counts(self) -> None:
        with self.assertRaises(ValueError):
            worker_timeline._validate(1.0, 1.0, True, 0)
        with self.assertRaises(ValueError):
            worker_timeline._validate(1.0, 1.0, 2, True)
        with self.assertRaises(ValueError):
            worker_timeline._validate(1.0, 1.0, 2, 3)
        with self.assertRaises(ValueError):
            worker_timeline._validate(1.0, 1.0, 2.0, 1)

    def test_read_only_calls_and_frames_do_not_mutate_cluster(self) -> None:
        cluster = FakeCluster(worker_rows=[worker()])
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "timeline.jsonl"
            worker_timeline.run(
                output, duration_s=1.0, interval_s=1.0, max_workers=2, warm_workers=1,
                cluster=cluster, clock=FakeClock(),
            )
            frames = [json.loads(line) for line in output.read_text(encoding="utf-8").splitlines()]

        sample = next(frame for frame in frames if frame["event"] == "sample")
        self.assertEqual(sample["experiment"], {"max_workers": 2, "warm_workers": 1})
        self.assertEqual(
            sample["worker_db"]["workers"][0]["reported_heartbeat_state"]["active_runs"]["status"],
            "reported",
        )
        self.assertEqual(cluster.verified, 1)
        self.assertEqual(cluster.sql_timeouts, [5.0, 5.0])
        self.assertTrue(all(call[0][0] == "get" for call in cluster.kubectl_calls))
        self.assertTrue(all(call[1]["timeout_s"] == 5.0 for call in cluster.kubectl_calls))
        self.assertFalse(any(
            "apply" in call[0] or "delete" in call[0] or "patch" in call[0]
            for call in cluster.kubectl_calls
        ))

    def test_cli_reports_only_the_error_class(self) -> None:
        output = StringIO()
        def fail(*_args: object, **_kwargs: object) -> None:
            raise RuntimeError("secret subprocess output")

        with patch.object(sys, "argv", [
            "worker_timeline.py", "--out", "/tmp/new-timeline.jsonl", "--duration-s", "1",
            "--max-workers", "1", "--warm-workers", "0",
        ]), patch.object(worker_timeline, "run", side_effect=fail):
            with redirect_stderr(output), self.assertRaises(SystemExit) as exited:
                worker_timeline.main()

        self.assertEqual(exited.exception.code, 1)
        self.assertIn("RuntimeError", output.getvalue())
        self.assertNotIn("secret subprocess output", output.getvalue())


if __name__ == "__main__":
    unittest.main()
