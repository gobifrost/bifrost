from __future__ import annotations

import asyncio
import json
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import Mock, patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "incluster"))

import recovery_extended  # noqa: E402
import recovery_extended_driver  # noqa: E402


class D7ContractTest(unittest.TestCase):
    def test_stale_attempt_is_not_a_pass_when_the_worker_dies_before_release(self) -> None:
        result = recovery_extended.d7_assessment({"process_alive_before_release": False})
        self.assertEqual(result["overall"], "not_exercised")
        self.assertIn("stale attempt not exercised", result["reason"])

    def test_d7_requires_netns_failure_heartbeat_hold_stale_rejection_and_one_post_gate_effect(self) -> None:
        result = recovery_extended.d7_assessment({
            "process_alive_before_release": True, "netns_connect_failed": True, "drop_installed": True,
            "last_db_heartbeat_at": 100.0, "lost_db_at": 130.0, "slot_released": True,
            "stale": {"stored_failed_result_unchanged": True, "renewal_rejected_log": True, "completion_rejected_log": True},
            "effects": {"count": 1, "append_after_gate": True, "no_replay_started": True},
        })
        self.assertEqual(result["overall"], "passed")
        self.assertEqual(result["bound"]["heartbeat_seconds"], 30.0)
        self.assertIn("liveness.py", result["bound"]["liveness_source"])

    def test_drop_commands_are_worker_netns_only_and_have_scoped_cleanup(self) -> None:
        chain = recovery_extended.d7_chain("request-1")
        commands = recovery_extended.d7_drop_commands("kind-bench", 123, chain)
        self.assertTrue(chain.startswith("BIFROST_D7_"))
        self.assertIn("nsenter", commands["install"][0])
        self.assertEqual(commands["install"][-1][-2:], ["-j", "DROP"])
        self.assertEqual(commands["cleanup"][-1][-2:], ["-X", chain])

    def test_drop_install_rolls_back_partial_rules(self) -> None:
        cluster = Mock()
        cluster.identity.node_name = "kind-bench"
        pod = {"metadata": {"uid": "pod", "name": "worker"}, "spec": {"nodeName": "kind-bench"},
               "status": {"containerStatuses": [{"name": "worker", "containerID": "containerd://cid"}]}}
        owned = {"pod_uid": "pod", "worker_id": "worker"}
        with patch.object(recovery_extended.recovery, "busy_worker", return_value=(owned, pod)), \
             patch.object(recovery_extended, "_fake_api_ip", return_value="10.96.0.1"), \
             patch.object(recovery_extended, "_worker_socket_probe", return_value={"connected": True, "timeout": False, "errno": None}), \
             patch.object(recovery_extended, "_worker_container_pid", return_value=("cid", 123)), \
             patch.object(recovery_extended.subprocess, "run", side_effect=[Mock(), RuntimeError("iptables unavailable"), Mock(returncode=0), Mock(returncode=0), Mock(returncode=0), Mock(returncode=0)]) as run:
            with self.assertRaisesRegex(RuntimeError, "iptables unavailable"):
                recovery_extended.install_d7_worker_drop(cluster, owned, pod, "request")
        self.assertEqual(run.call_count, 6)

    def test_d7_command_or_probe_unavailability_cannot_pass(self) -> None:
        result = recovery_extended.d7_assessment({"process_alive_before_release": True, "drop_installed": True,
                                                  "netns_connect_failed": False})
        self.assertEqual(result["overall"], "failed")


class D8ContractTest(unittest.TestCase):
    def test_d8_preserves_unaccepted_boundary_and_allows_only_a_receipted_new_retry(self) -> None:
        result = recovery_extended.d8_assessment(
            [{"id": "worker", "state": "ready"}], [{"id": "worker", "state": "ready"}],
            {"http_accepted": False, "durable_accepted": False, "db_ready_at": 100.0, "terminal_at": 120.0,
             "retry": {"new_request_receipt": True}},
        )
        self.assertEqual(result["overall"], "failed")
        self.assertTrue(result["submitted_unaccepted"])
        self.assertEqual(result["cancellation_clock"], "db-recovery")

    def test_d8_rejects_shared_outage_that_marks_a_previously_healthy_worker_lost(self) -> None:
        result = recovery_extended.d8_assessment(
            [{"id": "worker", "state": "ready"}], [{"id": "worker", "state": "lost"}],
            {"http_accepted": True, "accepted_at": 100.0, "terminal_at": 110.0},
        )
        self.assertFalse(result["checks"]["no_healthy_worker_lost_after_shared_outage"])

    def test_d8_rejects_negative_cancellation_interval(self) -> None:
        result = recovery_extended.d8_assessment([], [], {"http_accepted": True, "accepted_at": 100.0, "terminal_at": 99.0})
        self.assertFalse(result["checks"]["cancellation_terminal_within_bound"])


class D9ContractTest(unittest.TestCase):
    def test_d9_does_not_invent_elected_pool_leader_or_replica_ack(self) -> None:
        result = recovery_extended.d9_assessment(
            {"job-a": "uid-a"}, {"job-a": "uid-a"}, {"changed_at": 10.0, "converged_at": 20.0, "duplicate_job_bindings": []}, None
        )
        self.assertEqual(result["overall"], "passed")
        self.assertEqual(result["controller_model"], "all live worker-pool controllers reconcile; no elected pool leader exists")
        self.assertEqual(result["per_replica_ack"], "unmeasured")

    def test_m_ha_profile_is_correctness_only_and_declares_methodology_envelope(self) -> None:
        profile = json.loads((ROOT / "profiles" / "m-ha.json").read_text(encoding="utf-8"))
        self.assertTrue(profile["correctness_only"])
        self.assertEqual(profile["components"]["api"], {"replicas": 2, "cpu": "750m", "memory": "1Gi"})
        self.assertEqual(profile["worker_policy"], {"warm_workers": 1, "max_workers": 2, "worker_memory_mib": 1536, "largest_worker_mib": 1536})
        self.assertEqual(profile["worker_cpu"], "500m")

class FixtureContractTest(unittest.TestCase):
    def test_gated_fixture_token_is_deterministic_and_source_appends_only_after_gate(self) -> None:
        self.assertEqual(recovery_extended_driver.token("r"), recovery_extended_driver.token("r"))
        self.assertIn("params={\"gate\": gate", recovery_extended_driver.SOURCE)
        self.assertLess(recovery_extended_driver.SOURCE.index("response.raise_for_status()"), recovery_extended_driver.SOURCE.index("await tables.insert"))
        self.assertIn("bench_recovery_extended_effects_v1", recovery_extended_driver.SOURCE)

    def test_driver_uses_running_execution_cancel_route_and_orm_error_message_column(self) -> None:
        source = Path(recovery_extended_driver.__file__).read_text(encoding="utf-8")
        self.assertIn('"POST", f"/api/executions/{durable_id}/cancel"', source)
        self.assertNotIn('/api/workflows/executions/{durable_id}/cancel', source)
        self.assertIn("status,result,error_message,extract(epoch from completed_at)", source)
        self.assertNotIn("status,result,error,extract(epoch from completed_at)", source)

    def test_cancel_calls_running_execution_route(self) -> None:
        class Response:
            status_code = 202
            text = "accepted"

        class Session:
            def __init__(self) -> None:
                class Http:
                    async def aclose(self) -> None:
                        return None
                self.http = Http()
                self.calls = []

            async def login(self) -> None:
                return None

            async def request(self, method: str, path: str, **_kwargs: object):
                self.calls.append((method, path))
                return Response()

        session = Session()
        with patch.object(recovery_extended_driver, "Session", return_value=session):
            result = asyncio.run(recovery_extended_driver.cancel("execution-1"))
        self.assertEqual(session.calls, [("POST", "/api/executions/execution-1/cancel")])
        self.assertTrue(result["http_accepted"])

    def test_outcome_queries_orm_error_message_column(self) -> None:
        class Connection:
            def __init__(self) -> None:
                self.sql = ""

            async def fetch(self, sql: str, _request_id: str):
                self.sql = sql
                return [("execution-1", "Failed", {"reason": "x"}, "stored error", 12.0)]

            async def close(self) -> None:
                return None

        connection = Connection()

        async def connect(*_args: object, **_kwargs: object) -> Connection:
            return connection

        with patch.dict(sys.modules, {"asyncpg": types.SimpleNamespace(connect=connect)}), \
             patch.dict("os.environ", {"BENCH_DB_URL": "postgresql://unused"}):
            result = asyncio.run(recovery_extended_driver.outcome("request-1"))
        self.assertIn("error_message", connection.sql)
        self.assertEqual(result["rows"][0]["error"], "stored error")


if __name__ == "__main__":
    unittest.main()
