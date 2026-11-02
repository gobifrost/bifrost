#!/usr/bin/env python3
"""Bounded fixed-worker hosting-sizing campaign on one guarded Kind owner.

Each offered-rate arm starts from a fresh tenant.  The diagnostic SDK ramp
never retries invalid or inconclusive observations: after its first ordinary
failure it may take one midpoint between that failure and the last pass.
"""

from __future__ import annotations

import argparse
import copy
import fcntl
import hashlib
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
import hosting  # noqa: E402
import jsoncodec  # noqa: E402
import preflight  # noqa: E402
import run  # noqa: E402
import search  # noqa: E402
import scaling  # noqa: E402
from cluster import Cluster  # noqa: E402

DEFAULT_RATES = (2.0, 4.0, 8.0, 16.0)
ENSURE_ORGANIZATION_ID = "00000000-0000-0000-0000-000000000002"
NONFINITE_REPORTER_ERROR = {
    "class": "ValueError",
    "message": "Out of range float values are not JSON compliant: inf",
}


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
    entry = jsoncodec.encode({
        "at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "phase": phase, **value,
    })
    with path.open("a", encoding="utf-8") as output:
        output.write(json.dumps(entry, sort_keys=True, allow_nan=False) + "\n")
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


def _prior_result(path: Path, profile_name: str, profile: dict, api_image: str, rates: tuple[float, ...],
                  mix_name: str, workers: int) -> dict:
    """Validate the one reporter interruption eligible for a fresh continuation."""
    source = path.resolve()
    contents = source.read_bytes()
    loaded = jsoncodec.decode(json.loads(contents))
    if loaded.get("status") != "invalid" or loaded.get("error") != NONFINITE_REPORTER_ERROR:
        raise ValueError("prior result is not the known non-finite reporter interruption")
    if (loaded.get("mode") != "hosting-sizing-fixed-worker" or loaded.get("profile_name") != profile_name
            or loaded.get("profile") != profile or loaded.get("mix_name") != mix_name
            or loaded.get("api_image") != api_image or loaded.get("rates") != list(rates)
            or loaded.get("workers") != workers):
        raise ValueError("prior result profile, configuration, image, or declared rates do not match")
    preflight = loaded.get("preflight")
    if not isinstance(preflight, dict) or preflight.get("overall") != "pass":
        raise ValueError("prior result requires a passing preflight")
    proof = hosting._immutable_provenance(loaded.get("api_image_provenance"), "prior API image provenance")
    if proof["tag"] != api_image:
        raise ValueError("prior result API image proof does not match the selected image")
    provenance = loaded.get("api_image_provenance")
    if not isinstance(provenance, dict):
        raise ValueError("prior result lacks API image provenance")
    api_pods = provenance.get("api_pods")
    if not isinstance(api_pods, list) or not api_pods:
        raise ValueError("prior result must retain every ready API replica image proof")
    steps = loaded.get("steps")
    if not isinstance(steps, list) or len(steps) < 2 or len(steps) > len(rates):
        raise ValueError("prior result requires an increasing rate prefix ending in one ordinary failure")
    for index, step in enumerate(steps):
        if not isinstance(step, dict) or step.get("rate") != rates[index]:
            raise ValueError("prior result steps are not the declared increasing rate prefix")
        expected = "fail" if index == len(steps) - 1 else "pass"
        if step.get("overall") != expected:
            raise ValueError("prior result must end at its first ordinary failure")
        validity = step.get("validity")
        if not isinstance(validity, dict) or validity.get("invalid") != []:
            raise ValueError("prior result has invalid step evidence")
        evidence = step.get("fixed_worker_evidence")
        if (not isinstance(evidence, dict) or evidence.get("status") != "observed"
                or evidence.get("required_ready") != workers
                or type(evidence.get("samples")) is not int or evidence["samples"] < 1):
            raise ValueError("prior result lacks complete fixed-worker evidence")
        identity = step.get("api_identity")
        if not isinstance(identity, dict) or identity.get("stable") is not True:
            raise ValueError("prior result lacks stable API identity evidence")
        before, after = identity.get("before"), identity.get("after")
        if before != after or not isinstance(before, list) or len(before) != len(api_pods):
            raise ValueError("prior result API identity set does not match the runtime image proof")
        if any(not isinstance(record, dict) or not isinstance(record.get("pod"), str)
               or not isinstance(record.get("pod_uid"), str) or not record["pod"] or not record["pod_uid"]
               for record in before):
            raise ValueError("prior result has incomplete API identity records")
    return {
        "source": {"path": str(source), "sha256": hashlib.sha256(contents).hexdigest()},
        "interruption": copy.deepcopy(loaded["error"]),
        "preflight": copy.deepcopy(preflight),
        "steps": copy.deepcopy(steps),
        "last_pass": steps[-2]["rate"],
        "first_failure": steps[-1]["rate"],
    }


def run_campaign(profile_name: str, api_image: str, out: Path, *, rates: tuple[float, ...] = DEFAULT_RATES,
                 mix_name: str = "sdk-heavy", confirm: bool = False, cluster: Cluster | None = None,
                 owner_root: Path | None = None, ensure_fixture: Path, prior_result: Path | None = None) -> Path:
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
    if prior_result is not None and mix_name != "sdk-heavy":
        raise ValueError("prior-result continuation is limited to the SDK sizing ramp")
    if not ensure_fixture.is_file():
        raise FileNotFoundError(f"canonical bench_writes fixture is missing: {ensure_fixture}")

    profile = run.load_profile(profile_name)
    workers = _fixed_worker_count(profile)
    plan = _plan(profile, mix_name)
    continuation = (_prior_result(prior_result, profile_name, profile, api_image, rates, mix_name, workers)
                    if prior_result is not None else None)
    deploy.check_profile(profile)
    selected = cluster or Cluster(owner_root)
    selected.verify()
    current = run.Run(selected, "capacity", profile_name, profile, "fake", out, api_image)
    current.result.update({
        "mode": "hosting-sizing-fixed-worker", "mix_name": mix_name, "api_image": api_image,
        "rates": list(rates), "steps": copy.deepcopy(continuation["steps"]) if continuation else [],
        "confirmations": [], "sustainable_rate": None,
        "workers": workers, "status": "preparing", "stopped": None,
        "confirmation": "requested" if confirm and mix_name == "sdk-heavy" else "skipped for mixed diagnostic" if mix_name == "mixed" else "not requested",
        "declaration": {
            "fresh_seed": "every preflight, rate, midpoint, and confirmation arm resets then seeds a fresh tenant",
            "rate_search": "ordered explicit SDK rates through first failure, then one midpoint with the last pass",
            "invalid_or_inconclusive": "stop without retry or replacement",
            "api_identity": "all ready API pod identities must remain unchanged within each arm",
        },
    })
    if continuation:
        current.result.update({
            "preflight": continuation["preflight"],
            "continuation": {
                "source": continuation["source"], "interruption": continuation["interruption"],
                "inherited_steps": copy.deepcopy(continuation["steps"]),
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
            if "worker_db" in sample and "pods" in sample:
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

    def strict_confirmation(rate: float) -> dict:
        ids, identities = fresh("strict-confirmation")

        def retain_confirmation_segment(segment: dict) -> None:
            raw = copy.deepcopy(segment)
            current.result.setdefault("confirmation_segments", []).append(raw)
            checkpoint("strict-confirmation:segment", {"segment": raw})

        return measured(
            "strict-confirmation", ids, identities,
            lambda: search.confirm(
                selected, ids, rate, plan, plan["disk"],
                on_segment=retain_confirmation_segment,
                checkpoint=lambda raw: checkpoint("strict-confirmation:raw", raw),
            ),
            "confirmations",
        )

    failure: BaseException | None = None
    try:
        if continuation:
            midpoint = (continuation["last_pass"] + continuation["first_failure"]) / 2
            ids, identities = fresh("sdk-midpoint")
            midpoint_value = run_rate("sdk-midpoint", midpoint, ids, identities)
            if midpoint_value["overall"] in {"invalid", "inconclusive"}:
                current.result.update(status="ramp-stopped", stopped=midpoint_value["overall"])
            elif midpoint_value["overall"] == "pass":
                current.result.update(status="ramp-complete", stopped="continued-midpoint")
                if confirm:
                    value = strict_confirmation(midpoint)
                    if value["overall"] == "pass":
                        current.result["sustainable_rate"] = midpoint
            else:
                current.result.update(status="ramp-complete", stopped="continued-midpoint-failure")
        else:
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
                for index, rate in enumerate(rates):
                    ids, identities = fresh(f"sdk-rate-{index}")
                    value = run_rate(f"sdk-rate-{index}", rate, ids, identities)
                    if value["overall"] == "pass":
                        last_pass = rate
                        continue
                    if value["overall"] in {"invalid", "inconclusive"}:
                        current.result.update(status="ramp-stopped", stopped=value["overall"])
                        break
                    if last_pass is not None:
                        midpoint = (last_pass + rate) / 2
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
                    value = strict_confirmation(last_pass)
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
    parser.add_argument("--prior-result", type=Path)
    args = parser.parse_args()
    elasticity.LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    with elasticity.LOCK_PATH.open("a+") as lock:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise SystemExit("benchmark host lock is held; no sizing load was started") from error
        print(run_campaign(args.profile, args.api_image, args.out, rates=args.rates, mix_name=args.mix,
                           confirm=args.confirm, owner_root=args.owner_root, ensure_fixture=args.table_fixture,
                           prior_result=args.prior_result))


if __name__ == "__main__":
    main()
