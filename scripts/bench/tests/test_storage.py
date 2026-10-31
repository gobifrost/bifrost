from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "incluster"))

import storage  # noqa: E402
import storage_metrics  # noqa: E402


def pod(name: str, namespace: str, labels: dict[str, str], container: str) -> dict:
    return {
        "metadata": {"name": name, "namespace": namespace, "uid": f"{name}-uid", "labels": labels},
        "status": {"containerStatuses": [{"name": container, "ready": True}]},
    }


POSTGRES = pod("bifrost-db-1", "bench-tenant", {"cnpg.io/cluster": "bifrost-db"}, "postgres")
REDIS = pod("redis-0", "bench-tenant", {"app.kubernetes.io/name": "redis"}, "redis")
SEAWEED = pod("seaweedfs-0", "bench-infra", {"app.kubernetes.io/name": "seaweedfs"}, "seaweedfs")
POOLER = pod("bifrost-db-pooler-abc", "bench-tenant", {"cnpg.io/poolerName": "bifrost-db-pooler"}, "pgbouncer")
POOLER_METRICS = """# exporter comment
cnpg_pgbouncer_lists_used_clients 3
cnpg_pgbouncer_lists_used_servers 2
cnpg_pgbouncer_pools_cl_active{database=\"bifrost\",user=\"bifrost\"} 2
cnpg_pgbouncer_pools_cl_waiting{database=\"bifrost\",user=\"bifrost\"} 1
cnpg_pgbouncer_pools_sv_active{database=\"bifrost\",user=\"bifrost\"} 2
cnpg_pgbouncer_pools_sv_idle{database=\"bifrost\",user=\"bifrost\"} 0
cnpg_pgbouncer_pools_maxwait{database=\"bifrost\",user=\"bifrost\"} 4
cnpg_pgbouncer_stats_total_wait_time{database=\"bifrost\"} 50
"""


class StorageCollectionTest(unittest.TestCase):
    def _cluster(self) -> Mock:
        cluster = Mock()
        cluster.driver_sql.return_value = {
            "database": {"name": "bifrost", "bytes": 10, "wal_retained_bytes": 3,
                         "data_directory": "/var/lib/postgresql/data/pgdata", "settings": {"archive_mode": "off"}},
            "relations": [{"name": "executions", "total_bytes": 7}],
            "connections": [{"application_name": "api", "state": "idle", "wait_event": "ClientRead", "connections": 2}],
        }
        cluster.api_pod.return_value = ("bifrost-api-1", "api-uid")
        cluster.exec_python.return_value = {
            "basis": "visible_current_logical_payload", "local_not_billable": True,
            "prefixes": {"_repo/": {"object_count": 1, "object_bytes": 9}}, "total": {"object_count": 1, "object_bytes": 9},
            "retained_versions": {"status": "unmeasured", "reason": "no version listing"},
        }
        cluster.kubectl.side_effect = [
            json.dumps({"items": [POSTGRES]}), json.dumps({"spec": {}}), "4 /var/lib/postgresql/data/pgdata\n", "5120 /var/lib/postgresql/data/pgdata\n",
            json.dumps({"items": [REDIS]}),
            "used_memory:11\nused_memory_rss:12\nmem_fragmentation_ratio:1.1\n",
            "connected_clients:3\nblocked_clients:0\n",
            "total_connections_received:4\ntotal_commands_processed:5\ninstantaneous_ops_per_sec:6\nkeyspace_hits:7\nkeyspace_misses:8\nevicted_keys:9\nexpired_keys:10\n",
            "maxmemory\n0\nmaxmemory-policy\nnoeviction\n",
            json.dumps({"items": [POOLER]}), POOLER_METRICS,
            json.dumps({"items": [SEAWEED]}), "13 /data\n", "14 /data\n",
        ]
        return cluster

    @patch.object(storage.time, "time", return_value=123.0)
    @patch.object(storage.subprocess, "run")
    def test_collects_distinct_logical_and_physical_evidence(self, node_run: Mock, _now: Mock) -> None:
        cluster = self._cluster()
        node_run.return_value = Mock(returncode=0, stdout="12\n34\n", stderr="")

        result = storage.collect(cluster)

        self.assertEqual(result["schema_version"], 1)
        self.assertEqual(result["collected_at"], 123.0)
        self.assertEqual(result["postgres"]["database"]["bytes"], 10)
        self.assertEqual(result["postgres"]["physical_volume"]["allocated_bytes"], 4096)
        self.assertEqual(result["object_storage"]["logical_current_payload"]["total"]["object_bytes"], 9)
        self.assertEqual(result["object_storage"]["physical_volume"]["apparent_bytes"], 14)
        self.assertTrue(result["object_storage"]["logical_current_payload"]["local_not_billable"])
        self.assertEqual(result["pgbouncer"]["status"], "observed")
        self.assertEqual(result["pgbouncer"]["pools"]["cl_waiting"], 1)
        self.assertEqual(result["pgbouncer"]["max_wait_seconds"], 4.0)
        self.assertNotIn('user="', repr(result["pgbouncer"]))
        self.assertNotIn('database="', repr(result["pgbouncer"]))
        self.assertEqual(result["container_logs"]["retained_bytes"], 46)
        self.assertEqual(result["container_logs"]["files"], 2)
        cluster.verify.assert_called_once_with()
        cluster.exec_python.assert_called_once_with(
            "bifrost-api-1", storage.METRICS, ["logical-objects", "{}"], timeout_s=90, namespace=storage.guard.TENANT_NS,
        )
        raw_calls = [call for call in cluster.kubectl.call_args_list if call.args[:2] == ("get", "--raw")]
        self.assertEqual(len(raw_calls), 1)
        self.assertEqual(raw_calls[0].args[:2], ("get", "--raw"))
        self.assertIn("/api/v1/namespaces/bench-tenant/pods/bifrost-db-pooler-abc:9127/proxy/metrics", raw_calls[0].args)
        self.assertFalse(raw_calls[0].kwargs["namespaced"])

    def test_unexpected_redis_info_gap_invalidates_the_frame(self) -> None:
        cluster = self._cluster()
        calls = list(cluster.kubectl.side_effect)
        calls[5] = "used_memory:11\n"
        cluster.kubectl.side_effect = calls

        with self.assertRaisesRegex(RuntimeError, "Redis INFO memory omitted"):
            storage.collect(cluster)

    def test_rejects_unexpected_pod_selector_result(self) -> None:
        cluster = self._cluster()
        calls = list(cluster.kubectl.side_effect)
        calls[0] = json.dumps({"items": []})
        cluster.kubectl.side_effect = calls

        with self.assertRaisesRegex(RuntimeError, "exactly one Postgres"):
            storage.collect(cluster)

    def test_postgres_query_captures_the_declared_storage_and_connection_evidence(self) -> None:
        for fragment in (
            "pg_database_size(current_database())",
            "pg_ls_waldir()",
            "pg_total_relation_size(c.oid)",
            "pg_relation_size(c.oid)",
            "pg_indexes_size(c.oid)",
            "s.n_live_tup",
            "s.n_dead_tup",
            "s.last_autovacuum",
            "application_name",
            "wait_event",
            "archive_mode",
            "min_wal_size",
            "max_wal_size",
            "checkpoint_timeout",
        ):
            with self.subTest(fragment=fragment):
                self.assertIn(fragment, storage.POSTGRES_SQL)

    def test_du_uses_separate_commands_so_a_failure_cannot_be_hidden_by_a_pipeline(self) -> None:
        cluster = Mock()
        cluster.kubectl.side_effect = ["4 /data\n", "4097 /data\n"]

        result = storage._du(cluster, "bench-infra", {"name": "seaweedfs-0", "uid": "u"}, "seaweedfs", "/data", "SeaweedFS")

        self.assertEqual(result["allocated_bytes"], 4096)
        self.assertEqual(result["apparent_bytes"], 4097)
        self.assertEqual(cluster.kubectl.call_count, 2)
        self.assertEqual(cluster.kubectl.call_args_list[0].args[-3:], ("du", "-sk", "/data"))
        self.assertEqual(cluster.kubectl.call_args_list[1].args[-3:], ("du", "-bs", "/data"))

    @patch.object(storage.subprocess, "run")
    def test_container_logs_are_scoped_to_benchmark_namespaces_without_contents(self, run: Mock) -> None:
        run.return_value = Mock(returncode=0, stdout="1\n20\n", stderr="")
        cluster = Mock(identity=Mock(node_name="bifrost-frozen-control-plane"))

        result = storage._container_logs(cluster)

        self.assertEqual(result["retained_bytes"], 21)
        self.assertEqual(result["files"], 2)
        command = run.call_args.args[0]
        self.assertEqual(command[:3], ["docker", "exec", "bifrost-frozen-control-plane"])
        self.assertIn("bench-tenant_*", command[-1])
        self.assertIn("bench-infra_*", command[-1])
        self.assertNotIn("cat ", command[-1])


class LogicalObjectMetricsTest(unittest.TestCase):
    def test_uses_fixed_prefixes_without_emitting_keys(self) -> None:
        self.assertEqual(storage_metrics._prefix_for("_repo/a.py"), "_repo/")
        self.assertEqual(storage_metrics._prefix_for("unknown/private-key"), "other")
        self.assertNotIn("unknown/private-key", storage_metrics.TOP_LEVEL_PREFIXES)

    def test_retained_versions_are_explicitly_unmeasured_in_the_contract(self) -> None:
        self.assertEqual(storage_metrics.TOP_LEVEL_PREFIXES, ("_apps/", "_repo/", "_audit/", "apps/", "workflows/", ".bifrost/"))


class RedisParserTest(unittest.TestCase):
    def test_rejects_non_finite_redis_info_values(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "non-finite"):
            storage._info("used_memory:nan\n", ("used_memory",), "memory")


class PgBouncerMetricParserTest(unittest.TestCase):
    def test_aggregates_safe_exporter_metrics_and_strips_labels(self) -> None:
        result = storage._parse_pgbouncer_metrics(POOLER_METRICS)

        self.assertEqual(result["status"], "observed")
        self.assertEqual(result["pools"]["cl_active"], 2)
        self.assertEqual(result["stats"]["total_wait_time"], 50)
        self.assertNotIn("bifrost", repr(result))

    def test_missing_or_nonfinite_required_metric_is_explicitly_unmeasured(self) -> None:
        missing = storage._parse_pgbouncer_metrics(POOLER_METRICS.replace("cnpg_pgbouncer_pools_maxwait{database=\"bifrost\",user=\"bifrost\"} 4\n", ""))
        invalid = storage._parse_pgbouncer_metrics(POOLER_METRICS.replace(" 4\n", " NaN\n", 1))

        self.assertEqual(missing["status"], "unmeasured")
        self.assertEqual(invalid["status"], "unmeasured")


if __name__ == "__main__":
    unittest.main()
