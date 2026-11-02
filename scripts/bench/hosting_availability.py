#!/usr/bin/env python3
"""One benchmark-only API-replica availability arm for a fixed sizing profile.

The programmatic runner never acquires the canonical host lock; callers that
coordinate benchmark work own it.  The CLI acquires that lock once, binds a
``Cluster`` to an explicit owner worktree, then runs one fresh deployment and
one offered-rate SDK step while deleting one captured API Pod by UID.
"""

from __future__ import annotations

import argparse
import copy
import fcntl
import json
import math
import os
import re
import select
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterator

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import deploy  # noqa: E402
import elasticity  # noqa: E402
import guard  # noqa: E402
import hosting_sizing  # noqa: E402
import jsoncodec  # noqa: E402
import run  # noqa: E402
import search  # noqa: E402
from cluster import Cluster  # noqa: E402


LOCK_PATH = Path("/tmp/bifrost-capacity-host.lock")
API_SELECTOR = "app.kubernetes.io/name=bifrost-api"
API_LABELS = {"app.kubernetes.io/name": "bifrost-api"}
MEASURED_FAULT_OFFSET_S = 5.0
FAULT_TIMEOUT_S = 180.0
_PROXY_ADDRESS = re.compile(r"Starting to serve on 127\.0\.0\.1:(\d+)")
_POD_NAME = re.compile(r"^bifrost-api-[a-z0-9-]+$")


@contextmanager
def host_lock() -> Iterator[None]:
    """Acquire the canonical benchmark lock at the standalone CLI boundary."""
    LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    with LOCK_PATH.open("a+", encoding="utf-8") as handle:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise RuntimeError("another capacity benchmark holds the host lock") from error
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _ready_api(pod: dict) -> bool:
    metadata = pod.get("metadata", {})
    if metadata.get("deletionTimestamp") is not None:
        return False
    conditions = pod.get("status", {}).get("conditions", [])
    container_statuses = pod.get("status", {}).get("containerStatuses", [])
    return (
        any(item.get("type") == "Ready" and item.get("status") == "True" for item in conditions)
        and any(item.get("name") == "api" and item.get("ready") is True for item in container_statuses)
    )


def _raw_api_pods(cluster: Cluster) -> list[dict]:
    """Read API replicas directly so an outage never invokes ``Cluster.api_pod``."""
    raw = json.loads(cluster.kubectl("get", "pods", "-l", API_SELECTOR, "-o", "json"))
    rows = raw.get("items") if isinstance(raw, dict) else None
    if not isinstance(rows, list):
        raise RuntimeError("API selector did not return a pod list")
    active: list[dict] = []
    for pod in rows:
        if not isinstance(pod, dict):
            raise RuntimeError("API selector returned a non-object pod")
        metadata = pod.get("metadata")
        if not isinstance(metadata, dict):
            raise RuntimeError("API pod has no metadata")
        if metadata.get("deletionTimestamp") is not None:
            continue
        guard.check_fault_target(pod, guard.TENANT_NS, API_LABELS)
        name, uid = metadata.get("name"), metadata.get("uid")
        if not isinstance(name, str) or not _POD_NAME.fullmatch(name) or not isinstance(uid, str) or not uid:
            raise RuntimeError("API selector returned an unsafe pod identity")
        active.append(pod)
    return sorted(active, key=lambda pod: (pod["metadata"]["name"], pod["metadata"]["uid"]))


def capture_api_pair(cluster: Cluster) -> tuple[dict, dict]:
    """Capture the deterministic deletion target and its ready survivor by UID."""
    pods = _raw_api_pods(cluster)
    if len(pods) != 2 or not all(_ready_api(pod) for pod in pods):
        raise RuntimeError("availability arm requires exactly two ready API replicas")
    return pods[0], pods[1]


def _identity(pod: dict) -> dict[str, str]:
    metadata = pod["metadata"]
    return {"name": metadata["name"], "uid": metadata["uid"]}


def _captured_ready_pod(cluster: Cluster, captured: dict) -> dict:
    matching = [
        pod for pod in _raw_api_pods(cluster)
        if _identity(pod) == captured and _ready_api(pod)
    ]
    if len(matching) != 1:
        raise RuntimeError("captured API Pod changed before deletion")
    return matching[0]


def _append_checkpoint(path: Path, phase: str, value: dict) -> None:
    """Durably retain a safe partial result before the next benchmark action."""
    path.parent.mkdir(parents=True, exist_ok=True)
    entry = jsoncodec.encode({
        "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "phase": phase,
        "value": value,
    })
    with path.open("a", encoding="utf-8") as output:
        output.write(json.dumps(entry, sort_keys=True, allow_nan=False) + "\n")
        output.flush()
        os.fsync(output.fileno())


def delete_api_pod_uid(cluster: Cluster, target: dict) -> dict:
    """Delete one captured API Pod through a loopback-only UID-precondition proxy."""
    cluster.verify()
    guard.check_fault_target(target, guard.TENANT_NS, API_LABELS)
    name, uid = _identity(target).values()
    if not _POD_NAME.fullmatch(name) or not uid:
        raise guard.GuardError("API delete requires a captured simple pod name and UID")
    path = f"/api/v1/namespaces/{guard.TENANT_NS}/pods/{name}"
    proxy = subprocess.Popen([
        "kubectl", "--kubeconfig", str(cluster.kubeconfig), "--context", cluster.context,
        "proxy", "--address=127.0.0.1", "--port=0", f"--accept-paths=^{re.escape(path)}$",
    ], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    try:
        assert proxy.stdout is not None
        deadline = time.monotonic() + 10
        port = None
        while time.monotonic() < deadline:
            readable, _, _ = select.select([proxy.stdout], [], [], max(0.0, deadline - time.monotonic()))
            if not readable:
                break
            line = proxy.stdout.readline()
            match = _PROXY_ADDRESS.search(line)
            if match:
                port = match.group(1)
                break
            if proxy.poll() is not None:
                raise RuntimeError(f"kubectl proxy failed: {line.strip()}")
        if port is None:
            raise TimeoutError("kubectl proxy did not report its loopback port")
        options = json.dumps({
            "apiVersion": "v1", "kind": "DeleteOptions", "preconditions": {"uid": uid},
        }).encode("utf-8")
        request = urllib.request.Request(
            f"http://127.0.0.1:{port}{path}", data=options, method="DELETE",
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as error:
            body = error.read().decode("utf-8", errors="replace")[-1000:]
            raise RuntimeError(f"UID-preconditioned API delete returned HTTP {error.code}: {body}") from error
    finally:
        if proxy.poll() is None:
            proxy.terminate()
            try:
                proxy.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proxy.kill()
                proxy.wait(timeout=5)


def wait_for_replacement(cluster: Cluster, target: dict, survivor: dict, provenance: dict,
                         cancelled: threading.Event, *, timeout_s: float = FAULT_TIMEOUT_S,
                         on_sample: Callable[[dict], None] | None = None) -> dict:
    """Prove the survivor stays Ready at every raw poll until a replacement is Ready."""
    target_identity, survivor_identity = _identity(target), _identity(survivor)
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        pods = _raw_api_pods(cluster)
        survivor_matches = [pod for pod in pods if _identity(pod) == survivor_identity]
        survivor_ready = len(survivor_matches) == 1 and _ready_api(survivor_matches[0])
        sample = {
            "at_epoch_s": time.time(),
            "survivor": {**survivor_identity, "present": len(survivor_matches) == 1, "ready": survivor_ready},
            "api_pods": [{**_identity(pod), "ready": _ready_api(pod)} for pod in pods],
        }
        if on_sample is not None:
            on_sample(sample)
        if not survivor_ready:
            raise RuntimeError("survivor API Pod disappeared or became unready during replacement wait")
        ready = [pod for pod in pods if _ready_api(pod)]
        identities = {_identity(pod)["uid"] for pod in ready}
        replacement = [pod for pod in ready if _identity(pod)["uid"] not in {
            target_identity["uid"], survivor_identity["uid"],
        }]
        if (len(pods) == 2 and len(ready) == 2 and survivor_identity["uid"] in identities
                and target_identity["uid"] not in identities and len(replacement) == 1):
            image = cluster.api_image_evidence(provenance)
            return {
                "survivor_ready_uid_preserved": True,
                "replacement": _identity(replacement[0]),
                "all_ready_api_image_evidence": image,
            }
        if cancelled.wait(1.0):
            raise RuntimeError("API availability injector cancelled")
    raise TimeoutError("replacement API Pod did not restore the two-ready-replica set")


class ApiAvailabilityInjector:
    """Schedule exactly one UID-preconditioned API deletion from loadgen frames."""

    def __init__(self, cluster: Cluster, target: dict, survivor: dict, *, measured_offset_s: float,
                 checkpoint: Callable[[dict], None]) -> None:
        self.cluster = cluster
        self.target, self.survivor = target, survivor
        self.measured_offset_s, self.checkpoint = measured_offset_s, checkpoint
        self.state: dict = {
            "status": "waiting",
            "target": _identity(target),
            "survivor": _identity(survivor),
            "attempts": 0,
            "scope": "one captured API Pod deletion by Kubernetes UID precondition during the measured SDK window",
        }
        self._wake, self._cancel = threading.Event(), threading.Event()
        self._thread = threading.Thread(target=self._run, name="bench-api-availability-injector")

    def _save(self) -> None:
        self.checkpoint(copy.deepcopy(self.state))

    def _record_survivor_sample(self, sample: dict) -> None:
        self.state.setdefault("survivor_timeline", []).append(sample)
        self._save()

    def start(self) -> None:
        self._save()
        self._thread.start()

    def observe(self, frame: dict) -> None:
        if self.state.get("status") in {"error", "timeout"}:
            raise RuntimeError(self.state.get("error", "API availability injector failed"))
        if frame.get("event") == "started" and "driver_started_at" not in self.state:
            self.state["driver_started_at"] = float(frame["started"])
            self.state["scheduled_fault_at"] = self.state["driver_started_at"] + self.measured_offset_s
            self._save()
        elif frame.get("event") == "request_sent" and "first_request_sent_at" not in self.state:
            self.state["first_request_sent_at"] = float(frame["sent"])
            self.state["first_request_id"] = frame["request_id"]
            self._save()
        self._wake.set()

    def cancel(self, reason: str) -> None:
        self.state.setdefault("cancel_reason", reason)
        self._cancel.set()
        self._wake.set()

    def join(self, timeout_s: float) -> dict:
        self._thread.join(timeout_s)
        if self._thread.is_alive():
            self.cancel("injector timeout")
            self._thread.join(5)
            self.state["status"] = "timeout"
            self._save()
        return copy.deepcopy(self.state)

    def _wait_for(self, predicate: Callable[[], bool], deadline: float, message: str) -> None:
        while not predicate():
            if self._cancel.is_set():
                raise RuntimeError(self.state.get("cancel_reason", "injector cancelled"))
            remaining = deadline - time.time()
            if remaining <= 0:
                raise TimeoutError(message)
            self._wake.wait(min(remaining, 0.2))
            self._wake.clear()

    def _run(self) -> None:
        try:
            self._wait_for(lambda: "driver_started_at" in self.state, time.time() + 60, "loadgen did not emit started")
            self._wait_for(lambda: "first_request_sent_at" in self.state, time.time() + 60, "loadgen sent no request")
            scheduled = self.state["scheduled_fault_at"]
            self._wait_for(lambda: time.time() >= scheduled, scheduled + 60, "fault missed its measured-window schedule")
            self.cluster.verify()
            target = _captured_ready_pod(self.cluster, self.state["target"])
            _captured_ready_pod(self.cluster, self.state["survivor"])
            self.cluster.verify()
            self.state["attempts"] = 1
            self.state["fault_at"] = time.time()
            self.state["delete_response"] = delete_api_pod_uid(self.cluster, target)
            self.state["status"] = "deleted"
            self._save()
            self.state.update(wait_for_replacement(
                self.cluster, target, self.survivor, self.state["api_image_provenance"], self._cancel,
                on_sample=self._record_survivor_sample,
            ))
            self.state["status"] = "ready"
        except BaseException as error:
            if self.state.get("status") != "timeout":
                self.state["status"] = "error"
            self.state["error"] = f"{type(error).__name__}: {error}"
        finally:
            self._save()


def _fixed_sizing_profile(name: str) -> tuple[dict, dict[str, int]]:
    if not isinstance(name, str) or not name.startswith("hosting-"):
        raise ValueError("availability arm requires a named hosting sizing profile")
    profile = run.load_profile(name)
    policy = profile.get("worker_policy")
    if not isinstance(policy, dict):
        raise ValueError("sizing profile has no worker policy")
    controls = {field: policy.get(field) for field in elasticity.POLICY_FIELDS}
    if any(type(value) is not int for value in controls.values()):
        raise ValueError("sizing profile has incomplete worker policy controls")
    if controls["warm_workers"] != controls["max_workers"] or controls["max_workers"] < 1:
        raise ValueError("availability arm requires a fixed worker floor")
    return profile, controls  # type: ignore[return-value]


def _plan(profile: dict) -> dict:
    capacity = profile.get("capacity")
    if not isinstance(capacity, dict):
        raise ValueError("sizing profile has no capacity plan")
    mixes = capacity.get("mixes")
    disk = capacity.get("disk")
    if not isinstance(mixes, dict) or not isinstance(mixes.get("sdk-heavy"), dict) or not isinstance(disk, dict):
        raise ValueError("sizing profile lacks the SDK capacity mix or disk identity")
    warmup = capacity.get("warmup_s")
    if type(warmup) not in (int, float) or warmup < 0:
        raise ValueError("sizing profile has invalid warmup")
    return {**capacity, "mix": mixes["sdk-heavy"]}


def run_arm(profile_name: str, api_image: str, out: Path, rate: float, *, cluster: Cluster | None = None,
            ensure_fixture: Path) -> Path:
    """Run one fresh fixed-floor availability fault at the caller's offered SDK rate."""
    if type(rate) not in (int, float) or isinstance(rate, bool) or not math.isfinite(rate) or rate <= 0:
        raise ValueError("rate must be finite and greater than zero")
    if out.exists():
        raise FileExistsError(f"availability output must be fresh: {out}")
    if not ensure_fixture.is_file():
        raise FileNotFoundError(f"canonical bench_writes fixture is missing: {ensure_fixture}")
    profile, controls = _fixed_sizing_profile(profile_name)
    plan = _plan(profile)
    deploy.check_profile(profile)
    selected = cluster or Cluster()
    selected.verify()
    current = run.Run(selected, "hosting-availability", profile_name, profile, "fake", out, api_image)
    current.result.update({
        "api_image": api_image,
        "rate": float(rate),
        "mix_name": "sdk-heavy",
        "status": "running",
        "availability": {
            "attempts": 1,
            "policy": controls,
            "storage_endpoints": None,
            "fault": {"status": "not_started"},
        },
    })
    current.save()
    checkpoints = out / "timeline.jsonl"
    checkpoint_lock = threading.Lock()

    def persist_checkpoint(phase: str, value: dict) -> None:
        with checkpoint_lock:
            _append_checkpoint(checkpoints, phase, value)
            current.result["last_checkpoint"] = {"phase": phase}
            current.save()

    injector: ApiAvailabilityInjector | None = None
    failure: BaseException | None = None
    try:
        deploy.reset(selected)
        ids = run._prepare(selected, current, profile, "fake", api_image)
        fixture = hosting_sizing._fixture(selected, ensure_fixture)
        current.result["availability"]["table_fixture"] = fixture
        current.save()
        current.result["availability"]["policy_before"] = elasticity._assert_policy(selected, controls)
        current.result["availability"]["fixed_floor"] = elasticity.wait_for_floor(selected, controls["warm_workers"])
        target, survivor = capture_api_pair(selected)

        def checkpoint(state: dict) -> None:
            current.result["availability"]["fault"] = state
            persist_checkpoint("fault", state)

        def search_checkpoint(raw: dict) -> None:
            phase = raw.get("phase")
            if not isinstance(phase, str) or not phase:
                raise RuntimeError("SDK search checkpoint has no phase")
            current.result.setdefault("search_checkpoints", {})[phase] = copy.deepcopy(raw)
            persist_checkpoint(f"search:{phase}", raw)

        injector = ApiAvailabilityInjector(
            selected, target, survivor,
            measured_offset_s=float(plan["warmup_s"]) + MEASURED_FAULT_OFFSET_S,
            checkpoint=checkpoint,
        )
        injector.state["api_image_provenance"] = copy.deepcopy(current.result["api_image_provenance"])
        injector.start()
        step = search.run_step(
            selected, ids, float(rate), plan, plan["seed"], plan["disk"], on_event=injector.observe,
            checkpoint=search_checkpoint,
        )
        current.result["step"] = step
        current.result["availability"]["storage_endpoints"] = step.get("storage")
        current.save()
        fault = injector.join(FAULT_TIMEOUT_S)
        current.result["availability"]["fault"] = fault
        if fault.get("status") != "ready":
            raise RuntimeError("API availability fault did not prove a preserved survivor and ready replacement")
        storage_endpoints = step.get("storage")
        if not isinstance(storage_endpoints, dict) or not {"before", "after"} <= storage_endpoints.keys():
            raise RuntimeError("SDK search step did not retain storage endpoints before and after load")
        current.result["status"] = step.get("overall", "invalid")
    except BaseException as error:
        failure = error
        current.result.update({"status": "invalid", "error": f"{type(error).__name__}: {error}"})
    finally:
        if injector is not None:
            injector.cancel("measurement finished")
            current.result["availability"]["fault"] = injector.join(5)
        try:
            deploy.reset(selected)
            current.result["cleanup"] = {"verified": True}
        except BaseException as cleanup_error:
            current.result["cleanup"] = {"verified": False, "error": f"{type(cleanup_error).__name__}: {cleanup_error}"}
            failure = failure or cleanup_error
            current.result["status"] = "invalid"
        current.result["finished_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        current.save()
    if failure is not None:
        raise failure
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", required=True, help="exact fixed hosting sizing profile name")
    parser.add_argument("--owner-root", required=True, type=Path, help="worktree that owns the guarded Kind cluster")
    parser.add_argument("--api-image", required=True, help="selected local API image name:tag")
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--rate", required=True, type=float, help="one offered SDK arrival rate per second")
    parser.add_argument("--table-fixture", required=True, type=Path,
                        help="canonical fixture script that creates the empty bench_writes table")
    args = parser.parse_args()
    with host_lock():
        print(run_arm(args.profile, args.api_image, args.out, args.rate, cluster=Cluster(args.owner_root),
                      ensure_fixture=args.table_fixture), flush=True)


if __name__ == "__main__":
    main()
