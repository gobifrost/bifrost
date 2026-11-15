"""SDK-fake contracts for the Floci reference adapters.

These are adapter-level checks, not claims about eventual consistency in the
real cloud services that Floci emulates.
"""

from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path


CAPACITY_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(CAPACITY_ROOT / "python"))

from capacity_core import CapacityEngine, Context, Ledger, ProtocolError, ProviderConfig  # noqa: E402


def load_adapter(provider: str):
    path = CAPACITY_ROOT / "providers" / "floci" / provider / "capacity.py"
    spec = importlib.util.spec_from_file_location(f"test_floci_{provider}", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FakeEcs:
    def __init__(self) -> None:
        self.definitions: dict[str, str] = {}
        self.registered: list[str] = []
        self.stopped: list[str] = []
        self.deregistered: list[str] = []
        self.tasks: list[dict | None] = []
        self.owned: list[str] = []
        self.run_calls: list[tuple[str, str, str, str]] = []

    def task_definition(self, family: str) -> str | None:
        return self.definitions.get(family)

    def register_task_definition(self, family: str, _container: dict) -> str:
        arn = f"arn:aws:ecs:test:task-definition/{family}:1"
        self.registered.append(family)
        self.definitions[family] = arn
        return arn

    def stop_task(self, _cluster: str, arn: str) -> None:
        self.stopped.append(arn)

    def run_task(self, cluster: str, family: str, started_by: str, client_token: str) -> str:
        self.run_calls.append((cluster, family, started_by, client_token))
        self.owned = ["task"]
        return "task"

    def owned_tasks(self, _cluster: str, _started_by: str) -> list[str]:
        return list(self.owned)

    def describe_task(self, _cluster: str, _arn: str) -> dict | None:
        return self.tasks.pop(0)

    def deregister(self, arn: str) -> None:
        self.deregistered.append(arn)


class FakeCloudRun:
    def __init__(self) -> None:
        self.deleted: list[str] = []
        self.pools: list[dict | None] = []
        self.created: list[str] = []
        self.scaled: list[tuple[str, int]] = []

    def create_pool(self, pool_id: str, _container: dict) -> None:
        self.created.append(pool_id)

    def scale(self, pool_id: str, count: int) -> None:
        self.scaled.append((pool_id, count))

    def delete_pool(self, pool_id: str) -> None:
        self.deleted.append(pool_id)

    def pool(self, _pool_id: str) -> dict | None:
        return self.pools.pop(0)


class FakeAci:
    def __init__(self) -> None:
        self.deleted: list[str] = []
        self.groups: list[dict | None] = []
        self.created: list[str] = []

    def create_group(self, group: str, _container: dict) -> None:
        self.created.append(group)

    def delete_group(self, group: str) -> None:
        self.deleted.append(group)

    def group(self, _group: str) -> dict | None:
        return self.groups.pop(0)


class FlociAdapterContracts(unittest.TestCase):
    def ctx(self) -> Context:
        return Context("scope", "worker-1234567890", "resource", "operation-1234567890")

    def aws_adapter(self, ecs: FakeEcs):
        module = load_adapter("aws")
        adapter = module.Adapter.__new__(module.Adapter)
        adapter.ecs = ecs
        adapter.cluster = "cluster"
        adapter.image = "worker:test"
        adapter.cpu_units = 2048
        adapter.memory_mib = 4096
        adapter.env = {}
        adapter.entrypoint = ["python"]
        adapter.command = ["-m", "worker"]
        return adapter

    def engine(self, adapter) -> CapacityEngine:
        return CapacityEngine(
            ProviderConfig(
                token="x" * 32,
                scope="scope",
                worker_version="worker:test",
                memory_limit_bytes=1024 * 1024 * 1024,
                max_workers=1,
                max_lifetime_seconds=3600,
                starting_grace_seconds=0,
            ),
            adapter,
            Ledger(),
        )

    def reservation(self, worker_id: str) -> dict:
        return {
            "worker_id": worker_id,
            "worker_version": "worker:test",
            "memory_limit_bytes": 1024 * 1024 * 1024,
            "max_lifetime_seconds": 3600,
            "drain_grace_seconds": 300,
        }

    def accepted_start_lost(self, engine: CapacityEngine, worker_id: str) -> dict:
        prepared = engine.prepare(worker_id, self.reservation(worker_id))
        original_start = engine.adapter.start

        def lose_reply(ctx: Context, handle: dict):
            original_start(ctx, handle)
            raise RuntimeError("accepted start reply lost")

        engine.adapter.start = lose_reply
        with self.assertRaisesRegex(ProtocolError, "start outcome unknown"):
            engine.start(worker_id, {"resource_uid": prepared["resource_uid"]})
        engine.adapter.start = original_start
        return prepared

    def test_aws_prepare_reuses_the_existing_operation_definition_after_a_lost_reply(self) -> None:
        ecs = FakeEcs()
        adapter = self.aws_adapter(ecs)
        ctx = self.ctx()

        first = adapter.prepare(ctx, {})  # Provider accepted it; caller lost this reply.
        recovered = adapter.prepare(ctx, {})

        self.assertEqual(first, recovered)
        self.assertEqual(1, len(ecs.registered))
        self.assertIn(ctx.operation_id.replace("-", ""), first["family"])

    def test_aws_release_waits_for_a_terminal_task_readback(self) -> None:
        ecs = FakeEcs()
        adapter = self.aws_adapter(ecs)
        handle = {"family": "family", "task_definition_arn": "definition", "task_arn": "task", "started": True}
        ecs.tasks = [{"lastStatus": "RUNNING"}, {"lastStatus": "STOPPED"}]

        pending = adapter.release(self.ctx(), handle)
        confirmed = adapter.release(self.ctx(), handle)

        self.assertEqual("running", pending.state)
        self.assertEqual("deleted", confirmed.state)
        self.assertEqual(["definition"], ecs.deregistered)

    def test_gcp_release_waits_for_absent_resource_even_after_stopped_readback(self) -> None:
        module = load_adapter("gcp")
        cloud = FakeCloudRun()
        adapter = module.Adapter.__new__(module.Adapter)
        adapter.cloud = cloud
        handle = {"pool_id": "pool", "started": True}
        cloud.pools = [
            {"scaling": {"manualInstanceCount": 1}},
            {"terminalCondition": {"state": "CONDITION_FAILED"}},
            None,
        ]

        pending = adapter.release(self.ctx(), handle)
        stopped = adapter.release(self.ctx(), handle)
        confirmed = adapter.release(self.ctx(), handle)

        self.assertEqual("running", pending.state)
        self.assertEqual("stopped", stopped.state)
        self.assertEqual("deleted", confirmed.state)

    def test_azure_release_waits_for_absent_resource_even_after_stopped_readback(self) -> None:
        module = load_adapter("azure")
        aci = FakeAci()
        adapter = module.Adapter.__new__(module.Adapter)
        adapter.aci = aci
        handle = {"group": "group", "started": True}
        aci.groups = [
            {"properties": {"provisioningState": "Deleting", "instanceView": {"state": "Running"}}},
            {"properties": {"provisioningState": "Succeeded", "instanceView": {"state": "Stopped"}}},
            None,
        ]

        pending = adapter.release(self.ctx(), handle)
        stopped = adapter.release(self.ctx(), handle)
        confirmed = adapter.release(self.ctx(), handle)

        self.assertEqual("running", pending.state)
        self.assertEqual("stopped", stopped.state)
        self.assertEqual("deleted", confirmed.state)

    def test_aws_lost_start_reply_is_reconciled_without_a_second_launch_and_cleanup_finds_it(self) -> None:
        ecs = FakeEcs()
        adapter = self.aws_adapter(ecs)
        engine = self.engine(adapter)
        worker_id = self.ctx().worker_id
        prepared = self.accepted_start_lost(engine, worker_id)
        ecs.tasks = [{"lastStatus": "RUNNING"}, {"lastStatus": "RUNNING"}, {"lastStatus": "STOPPED"}]

        started = engine.start(worker_id, {"resource_uid": prepared["resource_uid"]})
        with self.assertRaises(ProtocolError):
            engine.remove(worker_id, {"resource_uid": prepared["resource_uid"]})
        removed = engine.remove(worker_id, {"resource_uid": prepared["resource_uid"]})

        self.assertEqual("running", started["state"])
        self.assertEqual("deleted", removed["state"])
        self.assertEqual(1, len(ecs.run_calls))
        row = engine.ledger.read(worker_id)
        assert row is not None
        self.assertEqual(row["operation_id"], ecs.run_calls[0][3])
        self.assertEqual(1, len(ecs.deregistered))

    def test_aws_cleanup_finds_an_accepted_lost_start_by_worker_identity(self) -> None:
        ecs = FakeEcs()
        adapter = self.aws_adapter(ecs)
        engine = self.engine(adapter)
        worker_id = self.ctx().worker_id
        prepared = self.accepted_start_lost(engine, worker_id)
        ecs.tasks = [{"lastStatus": "RUNNING"}, {"lastStatus": "STOPPED"}]

        with self.assertRaises(ProtocolError):
            engine.remove(worker_id, {"resource_uid": prepared["resource_uid"]})
        removed = engine.remove(worker_id, {"resource_uid": prepared["resource_uid"]})

        self.assertEqual("deleted", removed["state"])
        self.assertEqual(1, len(ecs.run_calls))
        self.assertEqual(["task", "task"], ecs.stopped)

    def test_gcp_lost_start_reply_is_reconciled_without_a_second_launch_and_cleanup_finds_it(self) -> None:
        module = load_adapter("gcp")
        cloud = FakeCloudRun()
        adapter = module.Adapter.__new__(module.Adapter)
        adapter.cloud = cloud
        adapter.image = "worker:test"
        adapter.cpu = "2000m"
        adapter.memory = "4096Mi"
        adapter.env = {}
        adapter.command = ["python"]
        adapter.args = ["-m", "worker"]
        engine = self.engine(adapter)
        worker_id = self.ctx().worker_id
        prepared = self.accepted_start_lost(engine, worker_id)
        cloud.pools = [
            {"scaling": {"manualInstanceCount": 1}},
            {"scaling": {"manualInstanceCount": 1}},
            None,
        ]

        started = engine.start(worker_id, {"resource_uid": prepared["resource_uid"]})
        with self.assertRaises(ProtocolError):
            engine.remove(worker_id, {"resource_uid": prepared["resource_uid"]})
        removed = engine.remove(worker_id, {"resource_uid": prepared["resource_uid"]})

        self.assertEqual("running", started["state"])
        self.assertEqual(1, len(cloud.scaled))
        self.assertEqual("deleted", removed["state"])

    def test_azure_lost_start_reply_is_reconciled_without_a_second_launch_and_cleanup_finds_it(self) -> None:
        module = load_adapter("azure")
        aci = FakeAci()
        adapter = module.Adapter.__new__(module.Adapter)
        adapter.aci = aci
        adapter.image = "worker:test"
        adapter.cpu = 2.0
        adapter.memory_gb = 4.0
        adapter.env = {}
        adapter.command = ["python", "-m", "worker"]
        engine = self.engine(adapter)
        worker_id = self.ctx().worker_id
        prepared = self.accepted_start_lost(engine, worker_id)
        aci.groups = [
            {"properties": {"provisioningState": "Succeeded", "instanceView": {"state": "Running"}}},
            {"properties": {"provisioningState": "Deleting", "instanceView": {"state": "Running"}}},
            None,
        ]

        started = engine.start(worker_id, {"resource_uid": prepared["resource_uid"]})
        with self.assertRaises(ProtocolError):
            engine.remove(worker_id, {"resource_uid": prepared["resource_uid"]})
        removed = engine.remove(worker_id, {"resource_uid": prepared["resource_uid"]})

        self.assertEqual("running", started["state"])
        self.assertEqual(1, len(aci.created))
        self.assertEqual("deleted", removed["state"])


if __name__ == "__main__":
    unittest.main()
