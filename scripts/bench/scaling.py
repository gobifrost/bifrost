#!/usr/bin/env python3
"""Independent fixed-worker rate search and strict confirmation on a selected image.

Every rate gets a fresh tenant and the same catalog and component allocations.
An invalid or inconclusive observation stops the arm without a replacement.
The adaptive ramp is diagnostic; only a fresh strict confirmation accepts a rate.
"""

from __future__ import annotations

import argparse
import copy
import fcntl
import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import capacity  # noqa: E402
import deploy  # noqa: E402
import elasticity  # noqa: E402
import preflight  # noqa: E402
import run  # noqa: E402
import search  # noqa: E402
from cluster import Cluster  # noqa: E402


def fixed_count(sample: dict, workers: int) -> bool:
    """Sampled exact ready-worker count; heartbeat activity is not child concurrency."""
    database = sample.get("worker_db", {})
    rows = database.get("workers")
    pods = sample.get("pods", {})
    if not isinstance(rows, list) or pods.get("status") != "observed":
        return False
    live = []
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get("lifecycle"), dict):
            return False
        ended = row["lifecycle"].get("ended_at_epoch_s")
        if row.get("state") in {"reserved", "starting", "ready", "draining"}:
            if ended is not None:
                return False
            live.append(row)
        elif ended is None:
            return False
    pod_rows = elasticity.live_worker_pods(pods)
    if pod_rows is None or len(live) != workers or len(pod_rows) != workers:
        return False
    if any(row.get("state") != "ready" or row.get("heartbeat_freshness", {}).get("state") != "fresh"
           for row in live):
        return False
    if any(pod.get("phase") != "Running" or pod.get("ready", {}).get("value") is not True
           or pod.get("deletion_at") is not None for pod in pod_rows):
        return False
    db_uids = {row.get("pod_uid") for row in live}
    pod_uids = {pod.get("pod_uid") for pod in pod_rows}
    return None not in db_uids and len(db_uids) == workers and db_uids == pod_uids


def run_arm(workers: int, mix_name: str, api_image: str, out: Path,
            *, start_rate: float = 1.0, max_rate: float = 16.0, cluster: Cluster | None = None) -> Path:
    """Search one fixed count. The caller owns the canonical host lock."""
    if workers not in (1, 2, 4) or isinstance(workers, bool):
        raise ValueError("workers must be 1, 2, or 4")
    if any(isinstance(value, bool) or not isinstance(value, (float, int))
           or not math.isfinite(value) or value <= 0 for value in (start_rate, max_rate)) or not start_rate <= max_rate <= 16:
        raise ValueError("rates must be finite and positive with start <= maximum <= 16/s")
    if out.exists():
        raise FileExistsError(f"refusing to replace scaling evidence: {out}")
    profile = elasticity.build_profile(workers, workers)
    if mix_name not in profile["capacity"]["mixes"]:
        raise ValueError("unknown declared mix")
    deploy.check_profile(profile)
    selected = cluster or Cluster()
    selected.verify()
    current = run.Run(selected, "capacity", f"fixed-{workers}", profile, "fake", out, api_image)
    current.result.update({
        "mode": "fixed-worker-scaling", "mix_name": mix_name,
        "api_image": api_image, "steps": [], "confirmations": [],
        "sustainable_rate": None, "workers": workers,
        "declaration": {
            "start_rate_per_s": start_rate, "max_rate_per_s": max_rate,
            "factor": 1.25, "bisections": 2,
            "rate_trials": "independent reset/seed, effective policy readback and exact idle-ready floor before each trial",
            "invalid_or_inconclusive": "stop arm without replacement",
            "confirmation": "highest passing ramp rate once, after fresh reset; failure stays a failure",
            "worker_observation": "five-second sequential samples; DB heartbeat activity is not exact child concurrency",
            "scope": "synthetic workload on a local cached-image node; no cloud capacity or billing claim",
        },
    })
    current.save()
    timeline = out / "timeline.jsonl"
    phase = "initial"
    observed: list[dict] = []

    def checkpoint(sample: dict, label: str = "floor") -> None:
        elasticity._write_jsonl(timeline, {"phase": phase, "label": label, **sample})
        if label == "measured":
            observed.append(sample)

    requested = {name: profile["worker_policy"][name] for name in elasticity.POLICY_FIELDS}
    plan = {**profile["capacity"], "mix": profile["capacity"]["mixes"][mix_name]}

    def fresh(label: str) -> dict:
        nonlocal phase
        phase = label
        deploy.reset(selected)
        ids = run._prepare(selected, current, profile, "fake", api_image)
        controls = elasticity._assert_policy(selected, requested)
        floor = elasticity.wait_for_floor(selected, workers, checkpoint=checkpoint)
        current.result.setdefault("phase_setups", []).append({
            "phase": label, "policy": controls, "floor": floor,
            "api_image_provenance": copy.deepcopy(current.result["api_image_provenance"]),
        })
        current.save()
        return ids

    def measured(action, key: str) -> dict:
        observed.clear()

        def retained() -> dict:
            value = action()
            current.result[key].append(value)
            current.save()  # retain raw results even if a concurrent observation fails
            return value

        value = elasticity._during(selected, "measured", checkpoint, retained)
        final = elasticity.observe(selected)
        checkpoint(final, "measured")
        controls = elasticity._assert_policy(selected, requested)
        valid_count = bool(observed) and all(fixed_count(sample, workers) for sample in observed)
        value["fixed_worker_evidence"] = {
            "status": "observed" if valid_count else "invalid",
            "required_ready": workers, "samples": len(observed), "policy_after": controls,
            "scope": "samples during warmup/measure/drain; transitions between samples may be missed",
        }
        if not valid_count:
            value["overall"] = "invalid"
            value.setdefault("validity", {}).setdefault("invalid", []).append("fixed ready-worker count not preserved in every sample")
        current.save()
        return value

    failure: BaseException | None = None
    try:
        ids = fresh("correctness-preflight")
        candidate = preflight.run(selected, ids, 3.90625, plan, out / "preflight-logs",
                                  lambda value: (current.result.update(preflight=value), current.save()))
        current.result["preflight"] = candidate
        current.save()
        if candidate["overall"] != "pass":
            current.result.update(status="preflight-stopped", stopped=candidate["overall"])
        else:
            history: list[tuple[float, str]] = []
            rate: float | None = start_rate
            while rate is not None:
                ids = fresh(f"ramp-{len(history)}")
                value = measured(lambda rate=rate, ids=ids: search.run_step(
                    selected, ids, rate, plan, plan["seed"], plan["disk"],
                ), "steps")
                if value["overall"] in {"invalid", "inconclusive"}:
                    current.result.update(status="ramp-stopped", stopped=value["overall"])
                    break
                history.append((rate, value["overall"]))
                next_rate = capacity.search_next(history, 1.25, 2)
                rate = (None if rate == max_rate and value["overall"] == "pass"
                        else min(next_rate, max_rate) if next_rate is not None else None)
            else:
                passing = [trial_rate for trial_rate, outcome in history if outcome == "pass"]
                failed = [trial_rate for trial_rate, outcome in history if outcome != "pass"]
                current.result["bracket"] = [max(passing, default=None), min(failed, default=None)]
                current.result["capacity_bound"] = "bracketed" if passing and failed else "lower-bound-only" if passing else "none"
                if passing:
                    rate = max(passing)
                    ids = fresh("strict-confirmation")
                    value = measured(lambda: search.confirm(selected, ids, rate, plan, plan["disk"]), "confirmations")
                    if value["overall"] == "pass":
                        current.result["sustainable_rate"] = rate
                    current.result.update(status="complete", stopped=None)
                else:
                    current.result.update(status="complete-no-passing-rate", stopped=None)
        current.save()
    except BaseException as error:
        failure = error
        current.result.update(status="invalid", error_class=type(error).__name__)
    finally:
        try:
            deploy.reset(selected)
            current.result["cleanup"] = {"verified": True}
        except BaseException as error:
            current.result["cleanup"] = {"verified": False, "error_class": type(error).__name__}
            failure = failure or error
        current.result["finished_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        current.save()
    if failure is not None:
        raise failure
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workers", type=int, choices=(1, 2, 4), required=True)
    parser.add_argument("--mix", choices=("sdk-heavy", "mixed"), default="sdk-heavy")
    parser.add_argument("--api-image", required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--start-rate", type=float, default=1.0)
    parser.add_argument("--max-rate", type=float, default=16.0)
    args = parser.parse_args()
    with elasticity.LOCK_PATH.open("a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        print(run_arm(args.workers, args.mix, args.api_image, args.out,
                      start_rate=args.start_rate, max_rate=args.max_rate))


if __name__ == "__main__":
    main()
