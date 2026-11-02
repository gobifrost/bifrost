#!/usr/bin/env python3
"""BENCHMARK-ONLY warm SDK resource-scaling diagnostic; never a certificate."""

from __future__ import annotations

import argparse
import copy
import fcntl
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import db_profile  # noqa: E402
import deploy  # noqa: E402
import elasticity  # noqa: E402
import hosting  # noqa: E402
import preflight  # noqa: E402
import run  # noqa: E402
import scaling  # noqa: E402
import search  # noqa: E402
from cluster import Cluster  # noqa: E402
from hosting_sizing import _counter_label, _fixture, _stable_api  # noqa: E402

COUNTS = (1, 2, 4)
RATES = (2.0, 4.0, 8.0, 16.0)
FROZEN_IMAGE = "bifrost-api:kind-a74e45295998-hosting-sizing-r1"


def profile_for(workers: int, base_profile: str = "hosting-8gib-fixed-workers-1") -> dict:
    """The common 8 GiB envelope, changing only the exact warm worker count."""
    if workers not in COUNTS or isinstance(workers, bool):
        raise ValueError("workers must be one of 1, 2, or 4")
    profile = copy.deepcopy(run.load_profile(base_profile))
    profile["quota"] = {**profile["quota"], "cpu": "5200m", "memory": "8Gi"}
    profile["worker_cpu"] = "650m"
    profile["worker_policy"] = {"warm_workers": workers, "max_workers": workers,
                                "worker_memory_mib": 1024, "largest_worker_mib": 1024,
                                "idle_seconds": 300}
    profile["capacity"] = {**profile["capacity"], "warmup_s": 30, "window_min_s": 120,
                           "drain_s": 60}
    deploy.check_profile(profile)
    return profile


def _checkpoint(path: Path, phase: str, value: dict) -> None:
    elasticity._write_jsonl(path, {"at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                                   "phase": phase, **value})


def _runtime_proof(current: run.Run, cluster: Cluster, image: str, workers: int) -> dict:
    proof = hosting._immutable_provenance(current.result.get("api_image_provenance"), "API image provenance")
    api_pods = current.result["api_image_provenance"].get("api_pods", [])
    if proof["tag"] != image or len(api_pods) != 2:
        raise RuntimeError("every API replica must prove the selected frozen image")
    worker_images = cluster.worker_image_evidence(current.result["api_image_provenance"])
    if len(worker_images) != workers:
        raise RuntimeError("every fixed worker must prove the selected frozen image")
    return {"api": proof, "api_pods": api_pods, "workers": worker_images}


def parse_counts(value: str) -> tuple[int, ...]:
    try:
        counts = tuple(int(item) for item in value.split(","))
    except ValueError as error:
        raise argparse.ArgumentTypeError("counts must be comma-separated worker counts") from error
    if not counts or tuple(sorted(set(counts))) != counts or any(count not in COUNTS for count in counts):
        raise argparse.ArgumentTypeError("counts must be an increasing subset of 1,2,4")
    return counts


def run_campaign(api_image: str, out: Path, *, owner_root: Path | None, ensure_fixture: Path,
                 base_profile: str = "hosting-8gib-fixed-workers-1", counts: tuple[int, ...] = COUNTS,
                 cluster: Cluster | None = None) -> Path:
    """Measure N=1,2,4 sequential rate cohorts; the CLI caller owns the host lock."""
    if out.exists():
        raise FileExistsError(f"refusing to replace resource-curve evidence: {out}")
    if api_image != FROZEN_IMAGE:
        raise ValueError(f"resource curve requires frozen image {FROZEN_IMAGE}")
    if not ensure_fixture.is_file():
        raise FileNotFoundError(f"canonical bench_writes fixture is missing: {ensure_fixture}")
    if not counts or tuple(sorted(set(counts))) != counts or any(count not in COUNTS for count in counts):
        raise ValueError("counts must be an increasing subset of 1,2,4")
    profiles = [(workers, profile_for(workers, base_profile)) for workers in counts]
    selected = cluster or Cluster(owner_root)
    selected.verify()
    base = profiles[0][1]
    current = run.Run(selected, "capacity", "hosting-8gib-resource-curve", base, "fake", out, api_image)
    current.result.update({
        "mode": "resource-curve-warm-sdk-diagnostic", "api_image": api_image, "base_profile": base_profile,
        "counts": [], "status": "preparing",
        "declaration": {
            "scope": "diagnostic warm sequential cohorts, not reset per rate, no cloud throughput/no 0.1% certificate",
            "rates": list(RATES), "workers": list(counts), "cohorts": "one seeded warm tenant per count; UUID-scoped loadgen requests per rate",
            "timing": "warmup 30 s, measurement max(120 s, 480/rate), drain 60 s",
            "stop": "first fail, invalid, or inconclusive stops that count; no retry, midpoint, descent, or strict certificate",
        },
    })
    current.save()
    timeline = out / "timeline.jsonl"
    failure: BaseException | None = None
    active_count: dict | None = None
    try:
        for workers, profile in profiles:
            plan = {**profile["capacity"], "mix": profile["capacity"]["mixes"]["sdk-heavy"]}
            count = {"workers": workers, "profile": profile, "steps": [], "bracket": [None, None], "status": "preparing"}
            active_count = count
            current.result["counts"].append(count)
            current.result.update(status="seeding", stage=f"workers-{workers}")
            current.save()
            deploy.reset(selected)
            ids = run._prepare(selected, current, profile, "fake", api_image)
            setup = {"ids": ids, "fixture": _fixture(selected, ensure_fixture),
                     "policy": elasticity._assert_policy(selected, {name: profile["worker_policy"][name] for name in elasticity.POLICY_FIELDS})}
            setup["floor"] = elasticity.wait_for_floor(selected, workers,
                checkpoint=lambda sample: _checkpoint(timeline, f"workers-{workers}:floor", sample))
            api_before = selected.api_pod_records()
            setup["api_identities"] = api_before
            setup["runtime_image"] = _runtime_proof(current, selected, api_image, workers)
            count["setup"] = setup
            current.result.update(status="preflight", stage=f"workers-{workers}")
            current.save()
            candidate = preflight.run(selected, ids, RATES[0], plan, out / "preflight-logs" / str(workers),
                                      lambda value: _checkpoint(timeline, f"workers-{workers}:preflight", value))
            _stable_api(selected, api_before, candidate)
            count["preflight"] = candidate
            current.save()
            if candidate["overall"] != "pass":
                count.update(status="preflight-stopped", stopped=candidate["overall"])
                deploy.reset(selected)
                count["cleanup"] = {"verified": True}
                active_count = None
                current.save()
                continue
            for index, rate in enumerate(RATES):
                phase = f"workers-{workers}:rate-{index}"
                samples: list[dict] = []
                value: dict | None = None
                retained = False

                def observed(sample: dict, label: str) -> None:
                    if "worker_db" in sample and "pods" in sample:
                        samples.append(sample)
                    _checkpoint(timeline, f"{phase}:observed:{label}", sample)

                def retain(raw: dict) -> None:
                    nonlocal retained, value
                    if retained:
                        return
                    value = raw
                    raw["cohort_uuid"] = uuid.uuid4().hex
                    count["steps"].append(raw)
                    retained = True
                    current.save()

                def action() -> dict:
                    raw: dict | None = None
                    try:
                        with db_profile.measure(selected, _counter_label(phase), out / "sql-counters" / f"{phase}.json",
                                                delay_s=30, duration_s=60):
                            raw = search.run_step(selected, ids, rate, plan, plan["seed"] + index, plan["disk"],
                                                  checkpoint=lambda data: _checkpoint(timeline, f"{phase}:raw", data))
                    except BaseException:
                        if raw is not None:
                            retain(raw)
                        raise
                    return raw

                elasticity._during(selected, phase, observed, action, on_result=retain)
                assert value is not None
                value["policy_after"] = elasticity._assert_policy(
                    selected, {name: profile["worker_policy"][name] for name in elasticity.POLICY_FIELDS},
                )
                _stable_api(selected, api_before, value)
                exact = bool(samples) and all(scaling.fixed_count(sample, workers) for sample in samples)
                value["fixed_worker_evidence"] = {"status": "observed" if exact else "invalid", "required_ready": workers,
                                                  "samples": len(samples), "scope": "five-second worker DB and pod frames only"}
                if not exact:
                    value["overall"] = "invalid"
                    value.setdefault("validity", {}).setdefault("invalid", []).append("fixed worker count changed")
                _checkpoint(timeline, f"{phase}:complete", {"overall": value["overall"]})
                current.save()
                if value["overall"] == "pass":
                    count["bracket"][0] = rate
                    continue
                if value["overall"] == "fail":
                    count["bracket"][1] = rate
                count.update(status="stopped", stopped=value["overall"])
                break
            else:
                count.update(status="ceiling-pass", stopped=None, capacity_bound="lower-bound-only")
            deploy.reset(selected)
            count["cleanup"] = {"verified": True}
            active_count = None
            current.save()
        current.result["status"] = "complete"
    except BaseException as error:
        failure = error
        current.result.update(status="invalid", error={"class": type(error).__name__, "message": str(error)})
    finally:
        try:
            deploy.reset(selected)
            if active_count is not None:
                active_count["cleanup"] = {"verified": True}
            current.result["cleanup"] = {"verified": True}
        except BaseException as cleanup_error:
            if active_count is not None:
                active_count["cleanup"] = {"verified": False, "error_class": type(cleanup_error).__name__}
            current.result["cleanup"] = {"verified": False, "error_class": type(cleanup_error).__name__}
            failure = failure or cleanup_error
        current.result["finished_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        current.save()
    if failure:
        raise failure
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--owner-root", required=True, type=Path)
    parser.add_argument("--api-image", required=True)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--table-fixture", required=True, type=Path)
    parser.add_argument("--base-profile", default="hosting-8gib-fixed-workers-1")
    parser.add_argument("--counts", type=parse_counts, default=COUNTS)
    args = parser.parse_args()
    elasticity.LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    with elasticity.LOCK_PATH.open("a+") as lock:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise SystemExit("benchmark host lock is held; no resource-curve load was started") from error
        print(run_campaign(args.api_image, args.out, owner_root=args.owner_root, ensure_fixture=args.table_fixture,
                           base_profile=args.base_profile, counts=args.counts))


if __name__ == "__main__":
    main()
