#!/usr/bin/env python3
"""Guarded local recovery drills for D3, D5, and D6.

This runner deliberately creates a fresh tenant and a fresh append-only effect
fixture for each invocation.  D7--D9 remain unimplemented: D7 needs the
stale-worker packet-drop/gated-request proof; D8 needs the D3-versus-D7
database-outage cancellation comparison; D9 needs the separate M-ha
competing-controller correctness profile and leader-kill reconciliation proof.
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
import jsoncodec  # noqa: E402
import recovery  # noqa: E402
import recovery_driver  # noqa: E402
import report  # noqa: E402
import run as bench_run  # noqa: E402
import search  # noqa: E402
from cluster import Cluster  # noqa: E402


LOCK_PATH = Path("/tmp/bifrost-capacity-host.lock")
RECOVERY_DRIVER = HERE / "incluster" / "recovery_driver.py"
RECOVERY_CLASS = recovery_driver.RECOVERY_CLASS


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _atomic_write(path: Path, contents: bytes) -> None:
    """Replace one artifact only after complete strict-JSON content reaches disk."""
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        with open(temporary, "xb") as file:
            file.write(contents)
            file.flush()
            os.fsync(file.fileno())
        os.replace(temporary, path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


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

    def __init__(self, cluster: Cluster, profile_name: str, profile: dict, kind: str, rate: float, out: Path,
                 api_image: str | None) -> None:
        out.mkdir(parents=True, exist_ok=False)
        self.cluster, self.out = cluster, out
        self.result = {
            "schema_version": jsoncodec.SCHEMA_VERSION, "kind": "recovery-drill", "drill": kind, "run_id": out.name,
            "started_at": _now(), **bench_run.environment(cluster, api_image), "profile_name": profile_name,
            "profile": profile, "backend": "fake", "api_image_selected": api_image, "rate": rate,
            "api_pod_changes": None, "status": "running", "stage": "created", "deploy": None, "setup": None,
            "d7_d9": {
                "d7": "unimplemented: stale worker requires worker-namespace packet drop, gated wf-io, lease-expiry and stale completion rejection proof",
                "d8": "unimplemented: D3 during long work compared with D7; cancellation requested during DB outage must take effect within 30 s after recovery",
                "d9": "unimplemented: M-ha competing-controller correctness-only profile, leader kill mid-reconcile, no orphan/duplicate Jobs and policy convergence within 15 s",
            },
        }
        self._events = out / "events.jsonl"
        self.save()

    def save(self) -> None:
        encoded = jsoncodec.encode(self.result)
        _atomic_write(self.out / "result.json", json.dumps(encoded, indent=2, allow_nan=False).encode("utf-8"))
        _atomic_write(self.out / "report.md", report.render(encoded).encode("utf-8"))

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
    if owned["execution_id"] != submitted["durable_id"]:
        raise RuntimeError("D6 durable busy owner does not belong to the submitted recovery request")
    sampler = recovery.WorkerRecoverySampler(cluster, owned["worker_id"])
    pre_kill = sampler.snapshot_before_kill()
    sampler.start()
    failure: BaseException | None = None
    try:
        killed = recovery.kill_worker_process(cluster, owned, pod)
        sampler.mark_killed(killed["killed_at"])
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
    slot_release = recovery.worker_slot_release(cluster, owned["worker_id"])
    outcomes = cluster.exec_python("bench-driver", RECOVERY_DRIVER, ["outcomes", json.dumps({"requests": [{"id": request_id}]})], timeout_s=600)
    effects = recovery.effect_assessment([{**submitted, "id": request_id, "class": RECOVERY_CLASS, "sent": submitted["submitted_at"], "effect_token": token}],
                                         outcomes["rows"], _effects_reader(cluster)([request_id]))
    owner_rows = [row for row in outcomes["rows"] if row.get("durable_id") == owned["execution_id"]]
    owner_terminal = len(owner_rows) == 1 and recovery._terminal(owner_rows[0])
    passed = (assessment["lost_path_observed"] and assessment["loss_not_before_heartbeat_timeout"]
              and assessment["loss_within_heartbeat_plus_one_sweep"]
              and assessment["replacement_admitted"] and slot_release["released"] and owner_terminal
              and effects["strict_effects"])
    return {"kind": "busy-worker-kill", "submitted": submitted, "target": {"owned": owned, "pod": pod}, "kill": killed,
            "pre_kill": pre_kill, "sampling": sampled, "assessment": assessment, "slot_release": slot_release,
            "outcomes": outcomes, "owner_terminal": owner_terminal, "effects": effects,
            "passed": passed, "overall": "passed" if passed else "failed"}


def run(kind: str, profile_name: str, rate: float, out: Path, api_image: str | None = None) -> Path:
    profile = bench_run.load_profile(profile_name)
    plan = recovery_plan(profile, rate)
    cluster = Cluster()
    cluster.verify()
    artifact = Artifact(cluster, profile_name, profile, kind, rate, out, api_image)
    failure: BaseException | None = None
    try:
        artifact.event("reset-before-setup")
        deploy.reset(cluster)
        normal_ids = bench_run._prepare(cluster, artifact, profile, "fake", api_image)
        artifact.result["normal_integration_setup"] = {"workflow_classes": sorted(normal_ids["workflows"]), "complete": True}
        artifact.result["runtime_api_image"] = artifact.result.get("api_image_provenance")
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
    parser.add_argument("--api-image", help="local API image tag; retained before deployment and verified after setup")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    with _host_lock():
        print(run(args.kind, args.profile, args.rate, args.out, args.api_image))


if __name__ == "__main__":
    main()
