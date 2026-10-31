#!/usr/bin/env python3
"""Guarded local recovery drills for D3, D5, and D6.

This runner deliberately creates a fresh tenant and a fresh append-only effect
fixture for each invocation.  D7--D9 remain unimplemented: there is no
network-partition target proof (D7), no object-store outage injector (D8), or
backup/restore fixture and recovery-point contract (D9).
"""

from __future__ import annotations

import argparse
import copy
import fcntl
import json
import os
import sys
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "incluster"))

import deploy  # noqa: E402
import recovery  # noqa: E402
import recovery_driver  # noqa: E402
import run as bench_run  # noqa: E402
import search  # noqa: E402
from cluster import Cluster  # noqa: E402


LOCK_PATH = Path("/tmp/bifrost-capacity-host.lock")
RECOVERY_DRIVER = HERE / "incluster" / "recovery_driver.py"
RECOVERY_CLASS = recovery_driver.RECOVERY_CLASS


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _atomic_write(path: Path, value: dict) -> None:
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    with open(temporary, "x", encoding="utf-8") as file:
        json.dump(value, file, indent=2, allow_nan=False)
        file.write("\n")
        file.flush()
        os.fsync(file.fileno())
    os.replace(temporary, path)


@contextmanager
def _host_lock() -> Iterator[None]:
    """The same lock as capacity/soak, held through tenant cleanup."""
    with open(LOCK_PATH, "a+", encoding="utf-8") as file:
        try:
            fcntl.flock(file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise RuntimeError("another capacity benchmark holds the host lock") from error
        try:
            yield
        finally:
            fcntl.flock(file.fileno(), fcntl.LOCK_UN)


class Artifact:
    """Fresh result directory with append-only stage/error events."""

    def __init__(self, cluster: Cluster, profile_name: str, profile: dict, kind: str, rate: float, out: Path) -> None:
        out.mkdir(parents=True, exist_ok=False)
        self.cluster, self.out = cluster, out
        self.result = {
            "kind": "recovery-drill", "drill": kind, "started_at": _now(), "profile_name": profile_name,
            "profile": profile, "rate": rate, "status": "running", "stage": "created",
            "d7_d9": {"d7": "unimplemented: no partition target proof", "d8": "unimplemented: no object-store outage injector",
                        "d9": "unimplemented: no backup/restore recovery-point contract"},
        }
        self._events = out / "events.jsonl"
        self.save()

    def save(self) -> None:
        _atomic_write(self.out / "result.json", self.result)

    def event(self, stage: str, **evidence: object) -> None:
        event = {"at": _now(), "stage": stage, **evidence}
        with open(self._events, "a", encoding="utf-8") as file:
            file.write(json.dumps(event, allow_nan=False) + "\n")
            file.flush()
            os.fsync(file.fileno())
        self.result["stage"] = stage
        self.save()

    def driver(self, mode: str, args: dict, timeout_s: int) -> dict:
        return self.cluster.exec_python("bench-driver", bench_run.DRIVER, [mode, json.dumps(args)], timeout_s=timeout_s)


def recovery_plan(profile: dict, rate: float) -> dict:
    """Use the capacity profile's timing/envelope, with one explicit recovery workflow class."""
    capacity = profile.get("capacity")
    if not isinstance(capacity, dict) or rate <= 0:
        raise ValueError("recovery requires a capacity profile and positive rate")
    deadline = capacity.get("deadlines_s", {}).get("wf-noop")
    thresholds = capacity.get("thresholds", {})
    if not isinstance(deadline, (int, float)) or not isinstance(thresholds, dict):
        raise ValueError("recovery profile lacks wf-noop deadline or capacity thresholds")
    return {
        **copy.deepcopy(capacity),
        "mix": {RECOVERY_CLASS: 1.0},
        "deadlines_s": {RECOVERY_CLASS: float(deadline)},
        "thresholds": {
            **copy.deepcopy(thresholds),
            "queue_p95_s": {RECOVERY_CLASS: thresholds["queue_p95_s"]["wf-noop"]},
            "e2e_p95_s": {RECOVERY_CLASS: thresholds["e2e_p95_s"]["wf-noop"]},
        },
    }


def _fixture_ids(cluster: Cluster, plan: dict, rate: float, run_nonce: str) -> tuple[dict, dict[str, str], dict]:
    fixture = cluster.exec_python("bench-driver", RECOVERY_DRIVER, ["prepare", "{}"], timeout_s=900)
    window_s = search.window_seconds(rate, plan["mix"], plan["window_min_s"], search.OBSERVATION_TARGET)
    requests = recovery_driver.request_plan(run_nonce, rate, plan["warmup_s"] + window_s, plan["seed"])
    tokens = {request["id"]: request["effect_token"] for request in requests}
    ids = {"workflows": fixture["workflows"], "recovery": {"requests": requests}}
    return ids, tokens, fixture


@contextmanager
def _recovery_loadgen() -> Iterator[None]:
    """Use the token-aware driver only while a recovery step is in flight."""
    original = search.LOADGEN
    search.LOADGEN = RECOVERY_DRIVER
    try:
        yield
    finally:
        search.LOADGEN = original


def _effects_reader(cluster: Cluster):
    def read(request_ids: list[str]) -> list[dict]:
        return cluster.exec_python("bench-driver", RECOVERY_DRIVER, ["effects", json.dumps({"request_ids": request_ids})], timeout_s=600)["rows"]
    return read


def _d5_effects(cluster: Cluster, result: dict, tokens: dict[str, str], plan: dict) -> dict:
    if result.get("overall") == "invalid" or not isinstance(result.get("step"), dict):
        return result
    step = result["step"]
    requests = [{**request, "effect_token": tokens[request["id"]]} for request in step["sent"]]
    effects = recovery.effect_assessment(requests, step["outcomes"]["rows"], _effects_reader(cluster)([request["id"] for request in requests]))
    ready_at = result["injector"].get("ready_at")
    deadlines = recovery.recovery_deadlines(requests, step["outcomes"]["rows"], ready_at, plan["deadlines_s"])
    passed = result["passed"] and effects["terminal_and_loss"] and effects["strict_effects"] and deadlines["recovery_deadline"]
    return {**result, "effects": effects, "deadlines": deadlines, "passed": passed, "overall": "passed" if passed else "failed"}


def _wait_busy_worker(cluster: Cluster, timeout_s: float = 120.0) -> tuple[dict, dict]:
    deadline = time.monotonic() + timeout_s
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            return recovery.busy_worker(cluster)
        except RuntimeError as error:
            last_error = error
            time.sleep(1)
    raise TimeoutError(f"D6 did not obtain one durable busy worker: {last_error}")


def _d6(cluster: Cluster, ids: dict, tokens: dict[str, str]) -> dict:
    request_id, token = next(iter(tokens.items()))
    submitted = cluster.exec_python("bench-driver", RECOVERY_DRIVER, ["start-busy", json.dumps({
        "workflow_id": ids["workflows"][RECOVERY_CLASS], "request_id": request_id, "token": token,
        "hold_s": recovery.WORKER_RECOVERY_BOUND_S + 30,
    })], timeout_s=60)
    owned, pod = _wait_busy_worker(cluster)
    sampler = recovery.WorkerRecoverySampler(cluster, owned["worker_id"])
    sampler.start()
    failure: BaseException | None = None
    try:
        killed = recovery.kill_worker_process(cluster, owned, pod)
        deadline = time.monotonic() + recovery.WORKER_RECOVERY_BOUND_S + 10
        while time.monotonic() < deadline:
            if sampler.first_lost_at is not None and sampler.first_replacement_ready_at is not None:
                break
            time.sleep(0.5)
    except BaseException as error:
        failure = error
        raise
    finally:
        sampled = sampler.stop()
    if failure is not None:  # pragma: no cover - the raise above is the observable failure path
        raise failure
    assessment = recovery.worker_recovery_assessment(killed, sampled)
    outcomes = cluster.exec_python("bench-driver", RECOVERY_DRIVER, ["outcomes", json.dumps({"requests": [{"id": request_id}]})], timeout_s=600)
    effects = recovery.effect_assessment([{**submitted, "id": request_id, "class": RECOVERY_CLASS, "sent": submitted["submitted_at"], "effect_token": token}],
                                         outcomes["rows"], _effects_reader(cluster)([request_id]))
    passed = assessment["loss_within_lease_plus_one_sweep"] and assessment["replacement_admitted"] and effects["strict_effects"]
    return {"kind": "busy-worker-kill", "submitted": submitted, "target": {"owned": owned, "pod": pod}, "kill": killed,
            "sampling": sampled, "assessment": assessment, "outcomes": outcomes, "effects": effects,
            "passed": passed, "overall": "passed" if passed else "failed"}


def run(kind: str, profile_name: str, rate: float, out: Path) -> Path:
    profile = bench_run.load_profile(profile_name)
    plan = recovery_plan(profile, rate)
    cluster = Cluster()
    cluster.verify()
    artifact = Artifact(cluster, profile_name, profile, kind, rate, out)
    failure: BaseException | None = None
    try:
        artifact.event("reset-before-setup")
        deploy.reset(cluster)
        normal_ids = bench_run._prepare(cluster, artifact, profile, "fake")
        artifact.result["normal_integration_setup"] = {"workflow_classes": sorted(normal_ids["workflows"]), "complete": True}
        nonce = uuid.uuid4().hex
        ids, tokens, fixture = _fixture_ids(cluster, plan, rate, nonce)
        artifact.result.update({"stage": "fixture-ready", "fixture": fixture,
                                "recovery_request_mapping": {"run_nonce": nonce, "requests": ids["recovery"]["requests"]}})
        artifact.save()
        with _recovery_loadgen():
            if kind == "d3":
                result = recovery.d3(cluster, ids, plan, plan["disk"], rate=rate, effects=_effects_reader(cluster), effect_tokens=tokens)
            elif kind == "d5":
                result = _d5_effects(cluster, recovery.d5(cluster, ids, plan, plan["disk"], rate=rate), tokens, plan)
            elif kind == "d6":
                result = _d6(cluster, ids, tokens)
            else:
                raise ValueError(f"unsupported recovery drill: {kind}")
        artifact.result.update({"stage": "drill-complete", "result": result,
                                "status": "passed" if result["passed"] else "failed"})
        artifact.save()
    except BaseException as error:
        failure = error
        artifact.result.update({"stage": "exception", "status": "invalid", "error": f"{type(error).__name__}: {error}"})
        artifact.event("exception", error=artifact.result["error"])
    finally:
        try:
            deploy.reset(cluster)
            artifact.result["cleanup"] = {"verified": True}
            artifact.event("cleanup", verified=True)
        except BaseException as error:
            artifact.result["cleanup"] = {"verified": False, "error": f"{type(error).__name__}: {error}"}
            artifact.result["status"] = "invalid"
            if failure is None:
                failure = error
            artifact.event("cleanup", verified=False, error=artifact.result["cleanup"]["error"])
        artifact.result["finished_at"] = _now()
        artifact.save()
    if failure is not None:
        raise failure
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--kind", choices=("d3", "d5", "d6"), required=True)
    parser.add_argument("--profile", default="m-capacity")
    parser.add_argument("--rate", type=float, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    with _host_lock():
        print(run(args.kind, args.profile, args.rate, args.out))


if __name__ == "__main__":
    main()
