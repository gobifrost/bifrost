from __future__ import annotations

import sys
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import faults  # noqa: E402


class QuotaEventLedgerTest(unittest.TestCase):
    def _ledger(self, observations: list[object], times: list[float] | None = None) -> faults.QuotaEventLedger:
        cluster = Mock()
        cluster.quota_denial_events.side_effect = observations
        clock_values = iter(times or list(range(len(observations))))
        return faults.QuotaEventLedger(
            cluster,
            clock=lambda: next(clock_values),
            wall_clock=lambda: 1_700_000_000.0,
        )

    def test_baseline_uid_expiry_does_not_create_a_negative_fault_count(self) -> None:
        ledger = self._ledger([[{"uid": "old", "count": 11}], []])
        ledger.seed()
        ledger.sample()
        self.assertTrue(ledger.valid)
        self.assertEqual(ledger.count, 0)
        self.assertEqual(ledger.evidence()["final_uid_counts"], {"old": 11})

    def test_new_uid_is_counted_after_an_old_event_expires(self) -> None:
        ledger = self._ledger([[{"uid": "old", "count": 11}], [{"uid": "new", "count": 3}]])
        ledger.seed()
        ledger.sample()
        self.assertTrue(ledger.valid)
        self.assertEqual(ledger.count, 3)

    def test_baseline_uid_count_increase_is_counted(self) -> None:
        ledger = self._ledger([[{"uid": "same", "count": 2}], [{"uid": "same", "count": 5}]])
        ledger.seed()
        ledger.sample()
        self.assertEqual(ledger.count, 3)

    def test_missing_or_malformed_identity_or_count_invalidates_measurement(self) -> None:
        for events in ([{"count": 1}], [{"uid": "event", "count": "one"}]):
            with self.subTest(events=events):
                ledger = self._ledger([[], events])
                ledger.seed()
                ledger.sample()
                self.assertFalse(ledger.valid)
                self.assertEqual(ledger.count, 0)
                self.assertTrue(ledger.evidence()["invalid"])

    def test_per_uid_counter_rollback_invalidates_measurement(self) -> None:
        ledger = self._ledger([[{"uid": "same", "count": 5}], [{"uid": "same", "count": 4}]])
        ledger.seed()
        ledger.sample()
        self.assertFalse(ledger.valid)
        self.assertIn("decreased", " ".join(ledger.evidence()["invalid"]))

    def test_gap_and_probe_failure_are_invalid_with_sanitized_evidence(self) -> None:
        ledger = self._ledger([[], []], times=[0.0, 31.0])
        ledger.seed()
        ledger.sample()
        self.assertFalse(ledger.valid)
        self.assertIn("gap", " ".join(ledger.evidence()["invalid"]))

        cluster = Mock()
        cluster.quota_denial_events.side_effect = [[], RuntimeError("do not retain probe detail")]
        failed = faults.QuotaEventLedger(cluster, clock=iter((0.0, 5.0)).__next__, wall_clock=lambda: 0.0)
        failed.seed()
        failed.sample()
        evidence = failed.evidence()
        self.assertFalse(failed.valid)
        self.assertEqual(evidence["snapshots"][-1]["probe_failure"], {"error_class": "RuntimeError"})
        self.assertNotIn("do not retain probe detail", str(evidence))

    def test_cancellation_exception_propagates_from_a_synchronous_observation(self) -> None:
        cluster = Mock()
        cluster.quota_denial_events.side_effect = SystemExit(143)
        ledger = faults.QuotaEventLedger(cluster)
        with self.assertRaises(SystemExit):
            ledger.seed()

    def test_evidence_is_an_immutable_snapshot(self) -> None:
        ledger = self._ledger([[{"uid": "event", "count": 1}]])
        ledger.seed()
        captured = ledger.evidence()
        captured["snapshots"][0]["events"][0]["count"] = 99
        self.assertEqual(ledger.evidence()["snapshots"][0]["events"][0]["count"], 1)

    def test_final_read_waits_for_an_inflight_periodic_probe(self) -> None:
        class ObservableLock:
            def __init__(self) -> None:
                self._lock = threading.Lock()
                self.contention = threading.Event()

            def __enter__(self):
                if self._lock.locked():
                    self.contention.set()
                self._lock.acquire()
                return self

            def __exit__(self, *_args):
                self._lock.release()

        probe_started = threading.Event()
        release_probe = threading.Event()
        calls: list[str] = []

        def events() -> list[dict]:
            calls.append("probe")
            if len(calls) == 2:
                probe_started.set()
                self.assertTrue(release_probe.wait(1))
            return []

        cluster = Mock()
        cluster.quota_denial_events.side_effect = events
        ledger = faults.QuotaEventLedger(cluster)
        lock = ObservableLock()
        ledger._lock = lock
        ledger.seed()
        periodic = threading.Thread(target=ledger.sample)
        periodic.start()
        self.assertTrue(probe_started.wait(1))
        final = threading.Thread(target=ledger.final_read)
        final.start()
        self.assertTrue(lock.contention.wait(1))
        self.assertEqual(calls, ["probe", "probe"])
        release_probe.set()
        periodic.join(1)
        final.join(1)
        self.assertFalse(periodic.is_alive())
        self.assertFalse(final.is_alive())
        self.assertEqual(calls, ["probe", "probe", "probe"])


if __name__ == "__main__":
    unittest.main()
