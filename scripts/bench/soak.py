#!/usr/bin/env python3
"""Observational two-hour soak sourced only from a passed mixed campaign.

This is deliberately a standalone harness entry point.  It cannot select a
profile or rate: those facts are read from the immutable confirmation campaign
that established the sustainable mixed rate.
"""

from __future__ import annotations

import argparse
import fcntl
import json
import math
import os
import sys
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "incluster"))

import campaign  # noqa: E402
import deploy  # noqa: E402
import jsoncodec  # noqa: E402
import run as bench_run  # noqa: E402
import search  # noqa: E402
import report  # noqa: E402
import soak_metrics  # noqa: E402
import storage  # noqa: E402
from cluster import Cluster, role_for_pod  # noqa: E402
import guard  # noqa: E402

SOAK_WINDOW_S = 2 * 60 * 60
POST_START_S = 30 * 60
METRIC_INTERVAL_S = 60.0
STORAGE_INTERVAL_S = 5 * 60
# This is also the capacity-search lock: a soak consumes the same host and
# tenant resources.  ``main`` owns it; callers of public ``run`` must already
# hold this canonical lock and must not nest it.
LOCK_PATH = Path("/tmp/bifrost-capacity-host.lock")
METRICS = HERE / "incluster" / "soak_metrics.py"
CONNECTION_STATES_SQL = """
select coalesce(json_agg(json_build_object('state', state, 'connections', connections) order by state), '[]'::json)
from (
  select coalesce(state, 'unknown') as state, count(*)::int as connections
  from pg_stat_activity
  where datname = current_database()
  group by coalesce(state, 'unknown')
) states
"""


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _encoded(value: object) -> bytes:
    return json.dumps(jsoncodec.encode(value), indent=2, allow_nan=False).encode("utf-8")


def _atomic_replace(path: Path, contents: bytes) -> None:
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    with open(temporary, "xb") as file:
        file.write(contents)
        file.flush()
        os.fsync(file.fileno())
    os.replace(temporary, path)


@contextmanager
def _host_lock() -> Iterator[None]:
    """Acquire the canonical capacity host lock for the standalone CLI only."""
    LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(LOCK_PATH, "a+", encoding="utf-8") as handle:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise RuntimeError("another benchmark soak holds the host lock") from error
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def soak_rate(confirmed_rate: object) -> float:
    if not isinstance(confirmed_rate, (int, float)) or isinstance(confirmed_rate, bool):
        raise ValueError("campaign confirmed rate is not a finite number")
    rate = float(confirmed_rate)
    if not math.isfinite(rate) or rate <= 0:
        raise ValueError("campaign confirmed rate must be finite and greater than zero")
    return rate * 0.70


def _source(manifest_path: Path) -> tuple[dict, str, dict, float, dict]:
    """Return only immutable, fully-passing campaign facts before cluster access."""
    aggregate = campaign.aggregate(manifest_path)
    if aggregate.get("overall") != "pass":
        raise ValueError("soak requires a fully passing immutable campaign aggregate")
    manifest = aggregate.get("campaign_manifest")
    digest = aggregate.get("campaign_manifest_sha256")
    if not isinstance(manifest, dict) or not isinstance(digest, str) or not digest:
        raise ValueError("fully passing campaign aggregate is missing immutable manifest evidence")
    profile_name = manifest.get("profile_name")
    profile_digest = manifest.get("resolved_profile_sha256")
    if not isinstance(profile_name, str) or not isinstance(profile_digest, str):
        raise ValueError("campaign aggregate is missing frozen profile evidence")
    profile = bench_run.load_profile(profile_name)
    if campaign._sha256_value(profile) != profile_digest:
        raise ValueError("current profile no longer matches the fully passing campaign")
    capacity = profile.get("capacity")
    if not isinstance(capacity, dict) or manifest.get("mix_name") != "mixed" or not isinstance(manifest.get("mix"), dict):
        raise ValueError("campaign aggregate does not describe the approved mixed capacity plan")
    image = manifest.get("api_image")
    seeds = manifest.get("seeds")
    if (
        not isinstance(image, dict)
        or not isinstance(image.get("tag"), str)
        or not image["tag"]
        or not isinstance(image.get("id"), str)
        or not image["id"]
        or not isinstance(seeds, list)
        or not seeds
        or not isinstance(seeds[0], int)
        or not isinstance(capacity.get("warmup_s"), (int, float))
        or not isinstance(capacity.get("drain_s"), (int, float))
        or not isinstance(capacity.get("disk"), dict)
    ):
        raise ValueError("campaign aggregate is missing immutable runtime evidence")
    plan = {**capacity, "mix": manifest["mix"]}
    return aggregate, digest, profile, soak_rate(manifest.get("rate_per_s")), plan


class SoakRun(bench_run.Run):
    """Use Run's environment/driver/snapshot contract with checkpoint-safe saving."""

    def __init__(self, *args: object, **kwargs: object) -> None:
        super().__init__(*args, **kwargs)
        self._save_lock = threading.RLock()
        self.raw_path = self.out / "raw-checkpoints.jsonl"

    def initialize(self) -> None:
        self.out.mkdir(parents=True, exist_ok=False)
        with open(self.out / "result.json", "xb") as file:
            file.write(_encoded(self.result))
            file.flush()
            os.fsync(file.fileno())
        _atomic_replace(self.out / "report.md", report.render(self.result).encode())

    def save(self) -> None:
        with self._save_lock:
            _atomic_replace(self.out / "result.json", _encoded(self.result))
            _atomic_replace(self.out / "report.md", report.render(self.result).encode())

    def raw(self, kind: str, value: dict, *, save: bool) -> None:
        entry = {"at": time.time(), "kind": kind, "value": value}
        line = json.dumps(jsoncodec.encode(entry), allow_nan=False, separators=(",", ":")) + "\n"
        with self._save_lock:
            with open(self.raw_path, "a", encoding="utf-8") as file:
                file.write(line)
                file.flush()
                os.fsync(file.fileno())
            counts = self.result.setdefault("raw_counts", {})
            counts[kind] = counts.get(kind, 0) + 1
            if save:
                self.save()


class MinuteSampler:
    """Once-per-minute DB-server and visible-current-S3 payload sampling."""

    def __init__(self, cluster: Cluster, api_pod: str, current: SoakRun) -> None:
        self.cluster, self.api_pod, self.current = cluster, api_pod, current
        self.samples: list[dict] = []
        self.error: BaseException | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.started = False
        self.last_storage_sample = 0.0

    def start(self) -> None:
        if self._thread is not None:
            return
        self.started = True
        self._thread = threading.Thread(target=self._run, name="bench-soak-metrics", daemon=True)
        self._thread.start()

    def sample(self) -> dict:
        connections = self.cluster.driver_sql(CONNECTION_STATES_SQL)
        objects = self.cluster.exec_python(
            self.api_pod, METRICS, ["objects", json.dumps({})], timeout_s=60, namespace=guard.TENANT_NS,
        )
        if not isinstance(connections, list) or not isinstance(objects, dict):
            raise RuntimeError("soak metrics did not return the expected aggregate shapes")
        sample = {"at": time.time(), "connection_states": connections, **objects}
        if sample["at"] - self.last_storage_sample >= STORAGE_INTERVAL_S:
            sample["storage"] = storage.collect(self.cluster)
            self.last_storage_sample = sample["at"]
        # The driver checkpoint callback can serialize concurrently.  Keep the
        # list, result reference, raw frame, and resulting checkpoint atomic.
        with self.current._save_lock:
            self.samples.append(sample)
            self.current.result["minute_metrics"] = self.samples
            self.current.raw("minute_metric", sample, save=True)
        return sample

    def _run(self) -> None:
        try:
            while not self._stop.is_set():
                self.sample()
                if self._stop.wait(METRIC_INTERVAL_S):
                    break
        except BaseException as error:
            self.error = error

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(METRIC_INTERVAL_S + 65)
            if self._thread.is_alive():
                self.error = TimeoutError("soak metric sampler did not stop")


def _tenant_path(path: str) -> bool:
    return path.startswith(f"{guard.TENANT_NS}/")


def _slopes(host: list[dict], started_at: float) -> dict[str, object]:
    observed = soak_metrics.memory_slopes(
        host, started_at=started_at, post_start_s=POST_START_S, include=_tenant_path,
    )
    for path, evidence in observed.items():
        evidence["role"] = role_for_pod(path.rsplit("/", 1)[-1])
        evidence["identity"] = "namespace/name@pod-UID" if "@" in path else "legacy namespace/name only; replacement unresolved"
        if evidence["coverage"] != "observed":
            evidence["lifecycle"] = "missing_post_window_coverage"
        elif evidence["seen_before_post_window"]:
            evidence["lifecycle"] = "seen_before_and_after_post_window"
        else:
            evidence["lifecycle"] = "appeared_after_post_window"
    roles = soak_metrics.role_memory_slopes(
        host, started_at=started_at, role_for_name=role_for_pod,
        post_start_s=POST_START_S, include=_tenant_path,
    )
    for evidence in roles.values():
        evidence["lifecycle_limit"] = "role totals can change with worker count; inspect individual UID lifetimes and sample coverage"
    return {
        "scope": "tenant namespace pods only; benchmark infrastructure excluded",
        "identity_limit": "new HostSampler keys include pod UIDs; missing samples remain missing, not zero",
        "per_pod": observed,
        "per_role": roles,
    }


def _object_delta(samples: list[dict]) -> dict[str, int | None]:
    if len(samples) < 2:
        return {"object_bytes": None, "object_count": None}
    first, last = samples[0], samples[-1]
    return {
        "object_bytes": int(last["object_bytes"]) - int(first["object_bytes"]),
        "object_count": int(last["object_count"]) - int(first["object_count"]),
    }


def _database_size_delta(before: dict, after: dict) -> int:
    first, last = before.get("db_bytes"), after.get("db_bytes")
    if type(first) is not int or type(last) is not int or first < 0 or last < 0:
        raise ValueError("database-size counters are missing or invalid during soak")
    return last - first


def _surface_sampler_error(sampler: MinuteSampler | None) -> None:
    if sampler is not None and sampler.error is not None:
        raise sampler.error


def _finalize(current: SoakRun) -> None:
    contents = (current.out / "result.json").read_bytes()
    campaign._write_hash_create(current.out / "result.json", contents)


def run(manifest_path: Path, out: Path) -> Path:
    """Run one observational soak, rejecting all mutable work until source proof passes."""
    aggregate, manifest_digest, profile, rate, plan = _source(manifest_path)
    if out.exists():
        raise ValueError(f"soak output already contains evidence: {out}")
    # Public callers must hold LOCK_PATH before invoking run; the CLI does so in main.
    cluster = Cluster()
    cluster.verify()
    # Re-prove the aggregate's frozen runtime/image/profile inputs before
    # creating an output directory or resetting the tenant.
    profile, plan = campaign._verify_frozen_inputs(cluster, aggregate["campaign_manifest"])
    image = aggregate["campaign_manifest"]["api_image"]
    current = SoakRun(cluster, "soak", aggregate["campaign_manifest"]["profile_name"], profile, "fake", out, image["tag"])
    current.result.update({
        "status": "partial",
        "mode": "observational",
        "not_capacity_certification": True,
        "campaign_manifest_path": str(manifest_path.resolve()),
        "campaign_manifest_sha256": manifest_digest,
        "confirmed_mixed_rate_per_s": aggregate["campaign_manifest"]["rate_per_s"],
        "rate_per_s": rate,
        "warmup_s": plan["warmup_s"],
        "measured_window_s": SOAK_WINDOW_S,
        "drain_s": plan["drain_s"],
        "object_storage": {
            "scope": "visible current S3 object payload bytes/count; outside tenant quota and not free",
            "unmeasured": "version attribution, compression, replication, cloud billing; see periodic storage frames for local allocation",
        },
        "storage_interval_s": STORAGE_INTERVAL_S,
        "minute_metrics": [],
        "raw_counts": {},
    })
    current.initialize()
    failure: BaseException | None = None
    sampler: MinuteSampler | None = None
    try:
        current.result["stage"] = "resetting"
        current.save()
        deploy.reset(cluster)
        current.result["stage"] = "preparing"
        current.save()
        ids = bench_run._prepare(cluster, current, profile, "fake", image["tag"])
        current.result["ids"] = ids
        bench_run.capture_idle_db(current, profile)
        api_pods = cluster.api_pod_records()
        api_pod = api_pods[0]["pod"]
        current.expected_api_pods = tuple((record["pod"], record["pod_uid"]) for record in api_pods)
        counters_before, counters_started = cluster.db_counters(), time.monotonic()
        current.result["database_growth_counters"] = {"before": counters_before, "after": None}
        current.save()

        def observe(frame: dict) -> None:
            _surface_sampler_error(sampler)
            current.raw("frame", frame, save=False)
            if frame.get("event") == "started" and sampler is not None:
                sampler.start()

        def checkpoint(evidence: dict) -> None:
            _surface_sampler_error(sampler)
            with current._save_lock:
                current.result.setdefault("raw_checkpoints", {})[evidence["phase"]] = evidence
                current.raw("checkpoint", evidence, save=False)
                current.save()
        current.result["stage"] = "running"
        current.save()
        sampler = MinuteSampler(cluster, api_pod, current)
        step = search.run_step(
            cluster, ids, rate, plan, int(aggregate["campaign_manifest"]["seeds"][0]), profile["capacity"]["disk"],
            window_s=SOAK_WINDOW_S, on_event=observe, cancel_driver=True, checkpoint=checkpoint,
        )
        if not sampler.started:
            raise RuntimeError("soak driver completed without an observed start frame")
        sampler.stop()
        _surface_sampler_error(sampler)
        sampler.sample()
        current.result["capacity_evidence"] = {
            "overall": step["overall"], "verdict": step["verdict"], "validity": step["validity"],
        }
        current.result["host_memory_slopes"] = _slopes(step["host"], step["started"])
        terminal = soak_metrics.terminal_counts(step["records"], step["outcomes"])
        db_window = bench_run.capture_db_window(
            current, "all_phases_db_window", "warmup+soak+drain", counters_before, counters_started,
        )
        growth_counters = cluster.db_counters()
        current.result["database_growth_counters"]["after"] = growth_counters
        current.result["terminal_counts"] = terminal
        current.result["unfinished_work"] = {"backlog": step["backlog"], "records": terminal["unfinished"]}
        current.result["connection_stability"] = {
            "scope": "PostgreSQL server-backend sessions from pg_stat_activity; not application client-pool or pooler readiness",
            "samples": len(sampler.samples),
            "states": [sample["connection_states"] for sample in sampler.samples],
            "coverage": "observed" if sampler.samples else "missing",
        }
        current.result["growth_per_1000_terminal"] = soak_metrics.per_thousand({
            "db_bytes": _database_size_delta(counters_before, growth_counters),
            "wal_generated_bytes": db_window["wal_generated_bytes"],
            **_object_delta(sampler.samples),
        }, terminal["durably_terminal"])
        current.result["step"] = step
        current.result["stage"] = "complete"
        current.result["status"] = "observational"
        current.save()
        current.snapshot("settled")
    except BaseException as error:
        failure = error
        current.result.update({"stage": "exception", "status": "invalid", "error": f"{type(error).__name__}: {error}"})
    finally:
        if sampler is not None:
            sampler.stop()
            current.result["sampler"] = {"samples": len(sampler.samples), "error": None if sampler.error is None else f"{type(sampler.error).__name__}: {sampler.error}"}
            if sampler.error is not None:
                current.result["status"] = "invalid"
                if failure is None:
                    failure = sampler.error
        try:
            deploy.reset(cluster)
            current.result["cleanup"] = {"verified": True}
        except BaseException as error:
            current.result["cleanup"] = {"verified": False, "error": f"{type(error).__name__}: {error}"}
            current.result["status"] = "invalid"
            if failure is None:
                failure = error
        current.result["finished_at"] = _utc_now()
        current.save()
        _finalize(current)
    if failure is not None:
        raise failure
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--campaign-manifest", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    arguments = parser.parse_args()
    # Do not externally flock this command: it owns the canonical capacity
    # lock for the whole source verification and mutable benchmark lifecycle.
    with _host_lock():
        print(run(arguments.campaign_manifest, arguments.out))


if __name__ == "__main__":
    main()
