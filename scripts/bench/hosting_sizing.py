#!/usr/bin/env python3
"""Bounded fixed-worker hosting-sizing campaign on one guarded Kind owner.

Each offered-rate arm starts from a fresh tenant.  The diagnostic SDK ramp
never retries invalid or inconclusive observations: after its first ordinary
failure it may take one midpoint between that failure and the last pass.
"""

from __future__ import annotations

import argparse
import fcntl
import json
import math
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import deploy  # noqa: E402
import db_profile  # noqa: E402
import elasticity  # noqa: E402
import preflight  # noqa: E402
import run  # noqa: E402
import search  # noqa: E402
import scaling  # noqa: E402
from cluster import Cluster  # noqa: E402

DEFAULT_RATES = (2.0, 4.0, 8.0, 16.0)
ENSURE_ORGANIZATION_ID = "00000000-0000-0000-0000-000000000002"


def parse_rates(value: str) -> tuple[float, ...]:
    """Parse strictly increasing, positive explicit offered rates."""
    try:
        rates = tuple(float(part) for part in value.split(","))
    except ValueError as error:
        raise argparse.ArgumentTypeError("rates must be comma-separated numbers") from error
    if not rates or any(not math.isfinite(rate) or rate <= 0 for rate in rates):
        raise argparse.ArgumentTypeError("rates must be finite and positive")
    if list(rates) != sorted(set(rates)):
        raise argparse.ArgumentTypeError("rates must be strictly increasing without duplicates")
    return rates


def _fixed_worker_count(profile: dict) -> int:
    policy = profile.get("worker_policy")
    if not isinstance(policy, dict):
        raise ValueError("sizing profile needs a worker_policy")
    warm, maximum = policy.get("warm_workers"), policy.get("max_workers")
    if type(warm) is not int or type(maximum) is not int or warm < 1 or warm != maximum:
        raise ValueError("sizing profile must declare one exact positive fixed worker count")
    return maximum


def _plan(profile: dict, mix_name: str) -> dict:
    capacity = profile.get("capacity")
    if not isinstance(capacity, dict) or not isinstance(capacity.get("mixes"), dict):
        raise ValueError("sizing profile needs a capacity plan and declared mixes")
    mix = capacity["mixes"].get(mix_name)
    if not isinstance(mix, dict) or not mix:
        raise ValueError(f"sizing profile does not declare mix {mix_name!r}")
    if not isinstance(capacity.get("disk"), dict) or type(capacity.get("seed")) is not int:
        raise ValueError("sizing profile capacity plan needs disk identity and integer seed")
    return {**capacity, "mix": mix}


def _append_checkpoint(path: Path, phase: str, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as output:
        output.write(json.dumps({"at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "phase": phase, **value},
                                sort_keys=True, allow_nan=False) + "\n")
        output.flush()
        os.fsync(output.fileno())


def _fixture(cluster: Cluster, source: Path) -> dict:
    value = cluster.exec_python("bench-driver", source, ["ensure", json.dumps({"org_id": ENSURE_ORGANIZATION_ID})], timeout_s=120)
    if not isinstance(value, dict) or value.get("status") != "pass" or value.get("table_rows") != 1 or value.get("documents") != 0:
        raise RuntimeError("bench_writes fixture did not prove one empty organization-scoped table")
    return value


def _stable_api(cluster: Cluster, before: list[dict], step: dict) -> None:
    after = cluster.api_pod_records()
    stable = after == before
    step["api_identity"] = {"before": before, "after": after, "stable": stable}
    if not stable:
        step["overall"] = "invalid"
        step.setdefault("validity", {}).setdefault("invalid", []).append("API replica identities changed during arm")


def _counter_label(phase: str) -> str:
    return re.sub(r"[^a-z0-9-]", "-", f"hosting-{phase}".lower()).strip("-")[:50]


def run_campaign(profile_name: str, api_image: str, out: Path, *, rates: tuple[float, ...] = DEFAULT_RATES,
                 mix_name: str = "sdk-heavy", confirm: bool = False, cluster: Cluster | None = None,
                 owner_root: Path | None = None, ensure_fixture: Path) -> Path:
    """Run one fixed-worker sizing campaign; the CLI caller owns the host lock."""
    if out.exists():
        raise FileExistsError(f"refusing to replace sizing evidence: {out}")
    if mix_name not in {"sdk-heavy", "mixed"}:
        raise ValueError("mix must be sdk-heavy or mixed")
    if not rates or any(type(rate) not in (int, float) or not math.isfinite(rate) or rate <= 0 for rate in rates):
        raise ValueError("rates must be finite and positive")
    if mix_name == "sdk-heavy" and list(rates) != sorted(set(rates)):
        raise ValueError("SDK rates must be strictly increasing without duplicates")
    if mix_name == "mixed" and len(rates) != 1:
        raise ValueError("mixed diagnostic requires one explicit offered rate")
    if not ensure_fixture.is_file():
        raise FileNotFoundError(f"canonical bench_writes fixture is missing: {ensure_fixture}")

    profile = run.load_profile(profile_name)
    workers = _fixed_worker_count(profile)
    plan = _plan(profile, mix_name)
    deploy.check_profile(profile)
    selected = cluster or Cluster(owner_root)
    selected.verify()
    current = run.Run(selected, "capacity", profile_name, profile, "fake", out, api_image)
    current.result.update({
        "mode": "hosting-sizing-fixed-worker", "mix_name": mix_name, "api_image": api_image,
        "rates": list(rates), "steps": [], "confirmations": [], "sustainable_rate": None,
        "workers": workers, "status": "preparing", "stopped": None,
        "confirmation": "requested" if confirm and mix_name == "sdk-heavy" else "skipped for mixed diagnostic" if mix_name == "mixed" else "not requested",
        "declaration": {
            "fresh_seed": "every preflight, rate, midpoint, and confirmation arm resets then seeds a fresh tenant",
            "rate_search": "ordered explicit SDK rates through first failure, then one midpoint with the last pass",
            "invalid_or_inconclusive": "stop without retry or replacement",
            "api_identity": "all ready API pod identities must remain unchanged within each arm",
        },
    })
    current.save()
    checkpoints = out / "timeline.jsonl"

    def checkpoint(phase: str, value: dict) -> None:
        _append_checkpoint(checkpoints, phase, value)
        current.result["last_checkpoint"] = {"phase": phase}
        current.save()

    def fresh(phase: str) -> tuple[dict, list[dict]]:
        current.result.update(status="seeding", stage=phase)
        current.save()
        deploy.reset(selected)
        ids = run._prepare(selected, current, profile, "fake", api_image)
        fixture = _fixture(selected, ensure_fixture)
        controls = elasticity._assert_policy(selected, {name: profile["worker_policy"][name] for name in elasticity.POLICY_FIELDS})
        floor = elasticity.wait_for_floor(selected, workers, checkpoint=lambda value: checkpoint(f"{phase}:floor", value))
        identities = selected.api_pod_records()
        current.result.setdefault("arm_setups", []).append({
            "phase": phase, "ids": ids, "fixture": fixture, "policy": controls,
            "floor": floor, "api_identities": identities,
        })
        checkpoint(f"{phase}:ready", {"api_identities": identities})
        return ids, identities

    def measured(phase: str, ids: dict, identities: list[dict], action: Callable[[], dict], key: str) -> dict:
        value: dict | None = None
        retained = False
        samples: list[dict] = []

        def observed(sample: dict, observed_phase: str) -> None:
            if "probe_failure" not in sample:
                samples.append(sample)
            checkpoint(f"{phase}:observed:{observed_phase}", sample)

        def retain(result: dict) -> None:
            nonlocal retained, value
            value = result
            current.result[key].append(result)
            retained = True
            current.save()

        try:
            def timed() -> dict:
                nonlocal value
                with db_profile.measure(selected, _counter_label(phase), out / "sql-counters" / f"{phase}.json"):
                    value = action()
                return value

            elasticity._during(selected, phase, observed, timed, on_result=retain)
        finally:
            if value is not None:
                if not retained:
                    retain(value)
                _stable_api(selected, identities, value)
                exact_workers = bool(samples) and all(scaling.fixed_count(sample, workers) for sample in samples)
                value["fixed_worker_evidence"] = {
                    "status": "observed" if exact_workers else "invalid",
                    "required_ready": workers, "samples": len(samples),
                    "scope": "five-second sequential observations during warmup, measure, and drain",
                }
                if not exact_workers:
                    value["overall"] = "invalid"
                    value.setdefault("validity", {}).setdefault("invalid", []).append(
                        "fixed ready-worker count was not preserved in every observation",
                    )
                checkpoint(f"{phase}:complete", {"overall": value["overall"]})
        assert value is not None
        return value

    def run_rate(phase: str, rate: float, ids: dict, identities: list[dict]) -> dict:
        return measured(
            phase, ids, identities,
            lambda: search.run_step(selected, ids, rate, plan, plan["seed"], plan["disk"],
                                    checkpoint=lambda raw: checkpoint(f"{phase}:raw", raw)),
            "steps",
        )

    failure: BaseException | None = None
    try:
        preflight_ids, preflight_api = fresh("preflight")
        candidate = preflight.run(selected, preflight_ids, rates[0], plan, out / "preflight-logs",
                                  lambda value: checkpoint("preflight:progress", value))
        _stable_api(selected, preflight_api, candidate)
        current.result["preflight"] = candidate
        checkpoint("preflight:complete", {"overall": candidate["overall"]})
        if candidate["overall"] != "pass":
            current.result.update(status="preflight-stopped", stopped=candidate["overall"])
        elif mix_name == "mixed":
            ids, identities = fresh("mixed-diagnostic")
            value = run_rate("mixed-diagnostic", rates[0], ids, identities)
            current.result.update(status="complete" if value["overall"] == "pass" else "diagnostic-stopped", stopped=None if value["overall"] == "pass" else value["overall"])
        else:
            last_pass: float | None = None
            first_failure: float | None = None
            for index, rate in enumerate(rates):
                ids, identities = fresh(f"sdk-rate-{index}")
                value = run_rate(f"sdk-rate-{index}", rate, ids, identities)
                if value["overall"] == "pass":
                    last_pass = rate
                    continue
                if value["overall"] in {"invalid", "inconclusive"}:
                    current.result.update(status="ramp-stopped", stopped=value["overall"])
                    break
                first_failure = rate
                if last_pass is not None:
                    midpoint = (last_pass + first_failure) / 2
                    ids, identities = fresh("sdk-midpoint")
                    midpoint_value = run_rate("sdk-midpoint", midpoint, ids, identities)
                    if midpoint_value["overall"] == "pass":
                        last_pass = midpoint
                    elif midpoint_value["overall"] in {"invalid", "inconclusive"}:
                        current.result.update(status="ramp-stopped", stopped=midpoint_value["overall"])
                        break
                current.result.update(status="ramp-complete", stopped="first-failure")
                break
            else:
                current.result.update(status="ramp-complete", stopped=None)

            if current.result["status"] == "ramp-complete" and confirm and last_pass is not None:
                ids, identities = fresh("strict-confirmation")
                value = measured(
                    "strict-confirmation", ids, identities,
                    lambda: search.confirm(selected, ids, last_pass, plan, plan["disk"]),
                    "confirmations",
                )
                if value["overall"] == "pass":
                    current.result["sustainable_rate"] = last_pass
            elif current.result["status"] == "ramp-complete" and last_pass is not None:
                current.result["sustainable_rate"] = None
        current.save()
    except BaseException as error:
        failure = error
        current.result.update(status="invalid", error={"class": type(error).__name__, "message": str(error)})
    finally:
        try:
            deploy.reset(selected)
            current.result["cleanup"] = {"verified": True}
        except BaseException as cleanup_error:
            current.result["cleanup"] = {"verified": False, "error_class": type(cleanup_error).__name__}
            failure = failure or cleanup_error
        current.result["finished_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        current.save()
    if failure is not None:
        raise failure
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--owner-root", required=True, type=Path)
    parser.add_argument("--profile", required=True)
    parser.add_argument("--api-image", required=True)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--rates", type=parse_rates, default=DEFAULT_RATES)
    parser.add_argument("--mix", choices=("sdk-heavy", "mixed"), default="sdk-heavy")
    parser.add_argument("--confirm", action="store_true")
    parser.add_argument("--table-fixture", required=True, type=Path)
    args = parser.parse_args()
    elasticity.LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    with elasticity.LOCK_PATH.open("a+") as lock:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise SystemExit("benchmark host lock is held; no sizing load was started") from error
        print(run_campaign(args.profile, args.api_image, args.out, rates=args.rates, mix_name=args.mix,
                           confirm=args.confirm, owner_root=args.owner_root, ensure_fixture=args.table_fixture))


if __name__ == "__main__":
    main()
