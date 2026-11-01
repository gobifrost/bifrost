"""Monotonic, UID-scoped benchmark fault evidence."""

from __future__ import annotations

import copy
import threading
import time
from collections.abc import Callable
from typing import Any


class QuotaEventLedger:
    """Count quota-denial Event occurrences without subtracting expired Events.

    Kubernetes Event retention is not a counter lifetime.  This ledger keeps a
    count for each observed Event UID, adds only a new UID or an increase for a
    known UID, and preserves a UID's last count after it disappears.
    """

    def __init__(self, cluster: Any, *, interval_s: float = 5.0, max_gap_s: float = 30.0,
                 clock: Callable[[], float] = time.monotonic, wall_clock: Callable[[], float] = time.time) -> None:
        self.cluster = cluster
        self.interval_s = interval_s
        self.max_gap_s = max_gap_s
        self._clock = clock
        self._wall_clock = wall_clock
        self._counts: dict[str, int] = {}
        self._total = 0
        self._last_observed_at: float | None = None
        self._max_gap_s = 0.0
        self._invalid: list[str] = []
        self._snapshots: list[dict] = []
        self._lock = threading.Lock()

    @property
    def count(self) -> int:
        with self._lock:
            return self._total

    @property
    def valid(self) -> bool:
        with self._lock:
            return not self._invalid

    def seed(self) -> None:
        """Synchronously capture the pre-traffic Event baseline."""
        self._observe("baseline")

    def sample(self) -> None:
        """Capture one periodic point-in-time Event observation."""
        self._observe("sample")

    def final_read(self) -> None:
        """Capture the final Event observation after traffic has drained."""
        self._observe("final")

    def _invalidate(self, reason: str) -> None:
        if reason not in self._invalid:
            self._invalid.append(reason)

    @staticmethod
    def _sanitized_event(event: object) -> tuple[dict, str | None]:
        if not isinstance(event, dict):
            return {"uid": None, "count": None}, "quota event was not an object"
        uid = event.get("uid")
        count = event.get("count")
        snapshot = {
            "uid": uid if isinstance(uid, str) and uid else None,
            "count": count if isinstance(count, int) and not isinstance(count, bool) else None,
        }
        if snapshot["uid"] is None:
            return snapshot, "quota event UID was missing or malformed"
        if snapshot["count"] is None or snapshot["count"] <= 0:
            return snapshot, "quota event count was missing or malformed"
        return snapshot, None

    def _observe(self, phase: str) -> None:
        # A periodic callback and the synchronous final read may overlap. Keep
        # the probe and its counter update atomic so a delayed read cannot
        # reorder Event UID observations.
        with self._lock:
            observed_at = self._clock()
            snapshot: dict = {
                "phase": phase,
                "observed_at_epoch_s": self._wall_clock(),
                "observed_at_monotonic_s": observed_at,
            }
            if self._last_observed_at is not None:
                gap_s = observed_at - self._last_observed_at
                snapshot["gap_s"] = gap_s
                self._max_gap_s = max(self._max_gap_s, gap_s)
                if gap_s > self.max_gap_s:
                    self._invalidate(f"quota event sampling gap {gap_s:.3f} s exceeded {self.max_gap_s:.3f} s")
            self._last_observed_at = observed_at
            try:
                events = self.cluster.quota_denial_events()
            except Exception as error:  # Probe evidence must fail closed without retaining error text.
                snapshot["probe_failure"] = {"error_class": type(error).__name__}
                self._snapshots.append(snapshot)
                self._invalidate(f"quota event probe failed: {type(error).__name__}")
                return
            if not isinstance(events, list):
                snapshot["events"] = []
                self._snapshots.append(snapshot)
                self._invalidate("quota event probe returned malformed collection")
                return

            sanitized: list[dict] = []
            current: dict[str, int] = {}
            malformed = False
            for event in events:
                row, error = self._sanitized_event(event)
                sanitized.append(row)
                if error is not None:
                    self._invalidate(error)
                    malformed = True
                    continue
                uid, count = row["uid"], row["count"]
                assert isinstance(uid, str) and isinstance(count, int)
                if uid in current:
                    self._invalidate("quota event UID appeared more than once in one observation")
                    malformed = True
                    continue
                current[uid] = count
            snapshot["events"] = sorted(sanitized, key=lambda row: (str(row["uid"]), str(row["count"])))
            self._snapshots.append(snapshot)
            if malformed:
                return

            for uid, count in current.items():
                previous = self._counts.get(uid)
                if previous is None:
                    self._counts[uid] = count
                    if phase != "baseline":
                        self._total += count
                elif count < previous:
                    self._invalidate(f"quota event count decreased for UID {uid}")
                elif count > previous:
                    self._counts[uid] = count
                    self._total += count - previous

    def evidence(self) -> dict:
        with self._lock:
            final = next((snapshot for snapshot in reversed(self._snapshots) if snapshot["phase"] == "final"), None)
            return copy.deepcopy({
                "scope": "observed allowlisted quota-denial Kubernetes Events only; message content is classified in Cluster and never retained",
                "sampling_scope": "point-in-time observations every HostSampler cadence plus one final read; Events created and deleted between observations are outside coverage",
                "cadence_s": self.interval_s,
                "max_gap_s": self.max_gap_s,
                "max_observed_gap_s": self._max_gap_s,
                "coverage": {
                    "observation_count": len(self._snapshots),
                    "baseline_observed": bool(self._snapshots and self._snapshots[0]["phase"] == "baseline"),
                    "final_observed": final is not None and "probe_failure" not in final,
                    "max_gap_within_limit": self._max_gap_s <= self.max_gap_s,
                },
                "snapshots": self._snapshots,
                "final_uid_counts": dict(sorted(self._counts.items())),
                "new_quota_denials": self._total,
                "invalid": list(self._invalid),
            })
