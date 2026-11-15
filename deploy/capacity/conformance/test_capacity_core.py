"""Deterministic concurrency contracts for the shared capacity engine."""

from __future__ import annotations

import sys
import threading
import unittest
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "python"))

from capacity_core import (  # noqa: E402
    CapacityAdapter,
    CapacityEngine,
    Context,
    Ledger,
    Observation,
    ProtocolError,
    ProviderConfig,
    START_FACT_NEVER_STARTED,
    STATE_PREPARED,
)


class BarrierCountLedger(Ledger):
    """Makes two pre-reservation capacity reads observe the same empty ledger."""

    def __init__(self) -> None:
        super().__init__()
        self.barrier = threading.Barrier(2)
        self.synchronize_counts = True

    def count_live(self, scope: str) -> int:
        count = super().count_live(scope)
        if self.synchronize_counts:
            self.barrier.wait(timeout=1)
        return count


class BarrierPreparedReadLedger(Ledger):
    """Makes two concurrent starts read the same prepared incarnation."""

    def __init__(self) -> None:
        super().__init__()
        self.arm = False
        self.barrier = threading.Barrier(2)

    def read(self, worker_id: str) -> dict[str, object] | None:
        row = super().read(worker_id)
        if self.arm and row is not None and row.get("state") == STATE_PREPARED:
            try:
                self.barrier.wait(timeout=0.2)
            except threading.BrokenBarrierError:
                # Serialized reads cannot rendezvous; only the broken implementation has two readers.
                pass
        return row


class Adapter(CapacityAdapter):
    def __init__(self) -> None:
        self.starts: list[str] = []
        self.start_lock = threading.Lock()
        self.started = threading.Event()
        self.release_start = threading.Event()
        self.block_worker: str | None = None
        self.release_observation = Observation("deleted")
        self.release_handles: list[dict[str, object]] = []

    def prepare(self, _ctx: Context, _spec: dict[str, object]) -> dict[str, object]:
        return {"prepared": True}

    def start(self, ctx: Context, handle: dict[str, object]) -> Observation:
        with self.start_lock:
            self.starts.append(ctx.worker_id)
        if ctx.worker_id == self.block_worker:
            self.started.set()
            if not self.release_start.wait(timeout=2):
                raise RuntimeError("test start was not released")
        return Observation("running", {**handle, "started": True})

    def observe(self, _ctx: Context, handle: dict[str, object]) -> Observation:
        return Observation("prepared" if not handle.get("started") else "running", handle)

    def release(self, _ctx: Context, handle: dict[str, object]) -> Observation:
        self.release_handles.append(handle)
        return self.release_observation


class LostPrepareAdapter(Adapter):
    """The provider accepted metadata before its first reply was lost."""

    def __init__(self) -> None:
        super().__init__()
        self.prepare_operations: list[str] = []
        self.handle: dict[str, object] | None = None
        self.lose_first_prepare = True

    def prepare(self, ctx: Context, _spec: dict[str, object]) -> dict[str, object]:
        self.prepare_operations.append(ctx.operation_id)
        self.handle = self.handle or {"operation": ctx.operation_id, "prepared": True}
        if self.lose_first_prepare:
            self.lose_first_prepare = False
            raise RuntimeError("reply lost after metadata acceptance")
        return self.handle

    def start(self, ctx: Context, handle: dict[str, object]) -> Observation:
        if handle != self.handle or handle.get("operation") != ctx.operation_id:
            raise RuntimeError("start did not receive the recovered prepare handle")
        return super().start(ctx, handle)


class LostStartAdapter(Adapter):
    """Models an accepted start whose response never reaches the core."""

    def __init__(self, *, prove_never_started: bool) -> None:
        super().__init__()
        self.prove_never_started = prove_never_started
        self.lose_first_start = True

    def start(self, ctx: Context, handle: dict[str, object]) -> Observation:
        if self.lose_first_start:
            self.lose_first_start = False
            if not self.prove_never_started:
                with self.start_lock:
                    self.starts.append(ctx.worker_id)
            raise RuntimeError("start reply lost")
        return super().start(ctx, handle)

    def observe(self, _ctx: Context, handle: dict[str, object]) -> Observation:
        return Observation(
            STATE_PREPARED,
            handle,
            start_fact=START_FACT_NEVER_STARTED if self.prove_never_started else None,
        )


class CapacityCoreConcurrencyTests(unittest.TestCase):
    def config(self, *, max_workers: int = 2) -> ProviderConfig:
        return ProviderConfig(
            token="x" * 32,
            scope="test-scope",
            worker_version="test-worker",
            memory_limit_bytes=1024 * 1024 * 1024,
            max_workers=max_workers,
            max_lifetime_seconds=3600,
            execution_handlers=["workflow.execute"],
            compute_capabilities=[],
            starting_grace_seconds=0,
        )

    def reservation(self, worker_id: str) -> dict[str, object]:
        return {
            "worker_id": worker_id,
            "worker_version": "test-worker",
            "memory_limit_bytes": 1024 * 1024 * 1024,
            "max_lifetime_seconds": 3600,
            "drain_grace_seconds": 300,
        }

    def test_concurrent_prepare_cannot_exceed_the_capacity_ceiling(self) -> None:
        ledger = BarrierCountLedger()
        engine = CapacityEngine(self.config(max_workers=1), Adapter(), ledger)
        workers = [str(uuid.uuid4()), str(uuid.uuid4())]
        outcomes: list[object] = []

        def prepare(worker_id: str) -> None:
            try:
                outcomes.append(engine.prepare(worker_id, self.reservation(worker_id)))
            except ProtocolError as exc:
                outcomes.append(exc)

        threads = [threading.Thread(target=prepare, args=(worker_id,)) for worker_id in workers]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=2)

        self.assertTrue(all(not thread.is_alive() for thread in threads))
        ledger.synchronize_counts = False
        self.assertEqual(1, sum(not isinstance(outcome, ProtocolError) for outcome in outcomes))
        self.assertEqual(1, ledger.count_live("test-scope"))

    def test_concurrent_start_invokes_the_adapter_once_per_incarnation(self) -> None:
        ledger = BarrierPreparedReadLedger()
        adapter = Adapter()
        engine = CapacityEngine(self.config(), adapter, ledger)
        worker_id = str(uuid.uuid4())
        prepared = engine.prepare(worker_id, self.reservation(worker_id))
        ledger.arm = True
        errors: list[Exception] = []

        def start() -> None:
            try:
                engine.start(worker_id, {"resource_uid": prepared["resource_uid"]})
            except Exception as exc:  # pragma: no cover - asserted below
                errors.append(exc)

        threads = [threading.Thread(target=start) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=2)

        self.assertTrue(all(not thread.is_alive() for thread in threads))
        self.assertEqual([], errors)
        self.assertEqual([worker_id], adapter.starts)

    def test_starting_one_worker_does_not_block_an_unrelated_worker(self) -> None:
        adapter = Adapter()
        engine = CapacityEngine(self.config(), adapter, Ledger())
        first, second = str(uuid.uuid4()), str(uuid.uuid4())
        first_doc = engine.prepare(first, self.reservation(first))
        second_doc = engine.prepare(second, self.reservation(second))
        adapter.block_worker = first
        first_thread = threading.Thread(
            target=engine.start, args=(first, {"resource_uid": first_doc["resource_uid"]}),
        )
        first_thread.start()
        self.assertTrue(adapter.started.wait(timeout=1))

        second_done = threading.Event()
        second_thread = threading.Thread(
            target=lambda: (engine.start(second, {"resource_uid": second_doc["resource_uid"]}), second_done.set()),
        )
        second_thread.start()
        self.assertTrue(second_done.wait(timeout=0.5))
        adapter.release_start.set()
        first_thread.join(timeout=2)
        second_thread.join(timeout=2)
        self.assertTrue(all(not thread.is_alive() for thread in (first_thread, second_thread)))

    def test_delete_requires_an_adapter_confirmation(self) -> None:
        adapter = Adapter()
        adapter.release_observation = Observation("running", {"still": "live"})
        engine = CapacityEngine(self.config(), adapter, Ledger())
        worker_id = str(uuid.uuid4())
        prepared = engine.prepare(worker_id, self.reservation(worker_id))

        with self.assertRaisesRegex(ProtocolError, "delete outcome unknown") as raised:
            engine.remove(worker_id, {"resource_uid": prepared["resource_uid"]})

        self.assertEqual(502, raised.exception.status)
        self.assertNotEqual("deleted", engine.get(worker_id)["state"])
        row = engine.ledger.read(worker_id)
        assert row is not None
        self.assertEqual("delete", row["intent"])

    def test_start_recovers_a_lost_prepare_reply_with_the_same_operation(self) -> None:
        adapter = LostPrepareAdapter()
        engine = CapacityEngine(self.config(), adapter, Ledger())
        worker_id = str(uuid.uuid4())

        with self.assertRaisesRegex(ProtocolError, "prepare failed"):
            engine.prepare(worker_id, self.reservation(worker_id))

        row = engine.ledger.read(worker_id)
        assert row is not None
        resource_uid, operation_id = row["resource_uid"], row["operation_id"]
        self.assertEqual("prepare", row["intent"])
        self.assertEqual({}, row["handle"])

        started = engine.start(worker_id, {"resource_uid": resource_uid})

        self.assertEqual("running", started["state"])
        self.assertEqual([operation_id, operation_id], adapter.prepare_operations)
        self.assertEqual([worker_id], adapter.starts)

    def test_delete_recovers_a_lost_prepare_reply_before_releasing(self) -> None:
        adapter = LostPrepareAdapter()
        engine = CapacityEngine(self.config(), adapter, Ledger())
        worker_id = str(uuid.uuid4())

        with self.assertRaises(ProtocolError):
            engine.prepare(worker_id, self.reservation(worker_id))
        row = engine.ledger.read(worker_id)
        assert row is not None

        removed = engine.remove(worker_id, {"resource_uid": row["resource_uid"]})

        self.assertEqual("deleted", removed["state"])
        self.assertEqual([row["operation_id"], row["operation_id"]], adapter.prepare_operations)
        self.assertEqual([adapter.handle], adapter.release_handles)

    def test_lost_start_with_a_bare_prepared_observation_never_relaunches(self) -> None:
        adapter = LostStartAdapter(prove_never_started=False)
        engine = CapacityEngine(self.config(), adapter, Ledger())
        worker_id = str(uuid.uuid4())
        prepared = engine.prepare(worker_id, self.reservation(worker_id))
        body = {"resource_uid": prepared["resource_uid"]}

        with self.assertRaisesRegex(ProtocolError, "start outcome unknown"):
            engine.start(worker_id, body)
        with self.assertRaisesRegex(ProtocolError, "start outcome unknown"):
            engine.start(worker_id, body)

        self.assertEqual([worker_id], adapter.starts)
        row = engine.ledger.read(worker_id)
        assert row is not None
        self.assertEqual("start", row["intent"])

    def test_lost_start_retries_only_after_an_explicit_never_started_fact(self) -> None:
        adapter = LostStartAdapter(prove_never_started=True)
        engine = CapacityEngine(self.config(), adapter, Ledger())
        worker_id = str(uuid.uuid4())
        prepared = engine.prepare(worker_id, self.reservation(worker_id))
        body = {"resource_uid": prepared["resource_uid"]}

        with self.assertRaisesRegex(ProtocolError, "start outcome unknown"):
            engine.start(worker_id, body)
        started = engine.start(worker_id, body)

        self.assertEqual("running", started["state"])
        self.assertEqual([worker_id], adapter.starts)


if __name__ == "__main__":
    unittest.main()
