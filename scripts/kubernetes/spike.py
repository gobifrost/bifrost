#!/usr/bin/env python3
"""Run repeatable local Kind experiments against the actual Bifrost worker.

Usage: ./test.sh kubernetes experiment warm|sdk|cold|burst|load|drain|deadline
Artifacts contain workload results, replica timelines and memory snapshots.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import time

ROOT = Path(__file__).resolve().parents[2]
LOCAL = ROOT / "scripts/kubernetes/local-kind.sh"
NS = "bifrost-local"
WORKER = "app.kubernetes.io/name=bifrost-worker"


def kubectl(*args: str, data: str | None = None) -> str:
    result = subprocess.run(
        [str(LOCAL), "kubectl", "--", "-n", NS, *args], input=data,
        text=True, capture_output=True, check=True, timeout=60,
    )
    return result.stdout


def patch(resource: str, body: dict) -> None:
    kubectl("patch", resource, "--type=merge", "-p", json.dumps(body))


def workers() -> list[dict]:
    return json.loads(kubectl("get", "pods", "-l", WORKER, "-o", "json"))["items"]


def wait_workers(count: int) -> None:
    end = time.monotonic() + 180
    while time.monotonic() < end:
        pods = workers()
        ready = [p for p in pods if not p["metadata"].get("deletionTimestamp") and any(
            c["type"] == "Ready" and c["status"] == "True"
            for c in p.get("status", {}).get("conditions", [])
        )]
        if len(pods) == count and len(ready) == count:
            return
        time.sleep(1)
    raise TimeoutError(f"Expected {count} ready worker pods")


def snapshot() -> dict:
    samples = []
    for pod in workers():
        name = pod["metadata"]["name"]
        if pod.get("status", {}).get("phase") != "Running" or pod["metadata"].get("deletionTimestamp"):
            continue
        code = """import json, os
from pathlib import Path
p=Path('/sys/fs/cgroup')
s=dict(line.split() for line in (p/'memory.stat').read_text().splitlines())
c=int((p/'memory.current').read_text())
processes=[]
for proc in Path('/proc').iterdir():
    if not proc.name.isdigit() or int(proc.name)==os.getpid():
        continue
    try:
        status=dict(line.split(':',1) for line in (proc/'status').read_text().splitlines())
        smaps=dict(line.split(':',1) for line in (proc/'smaps_rollup').read_text().splitlines() if ':' in line)
        processes.append({'pid':int(proc.name),'ppid':int(status['PPid']),'name':status['Name'].strip(),'pss_bytes':int(smaps['Pss'].split()[0])*1024})
    except (OSError, KeyError):
        pass
print(json.dumps({'current_bytes':c,'peak_bytes':int((p/'memory.peak').read_text()),'working_set_estimate_bytes':max(0,c-int(s.get('inactive_file',0))),'anon_bytes':int(s['anon']),'file_bytes':int(s['file']),'kernel_bytes':int(s.get('kernel',0)),'processes':processes}))
"""
        samples.append({"pod": name, **json.loads(kubectl("exec", name, "--", "python", "-c", code))})
    return {"samples": samples, "note": "cgroup includes the short measurement process; working set is current minus inactive_file"}


def experiment(mode: str, out: Path) -> None:
    out.mkdir(parents=True, exist_ok=False)
    original = json.loads(kubectl("get", "scaledobject/bifrost-worker", "-o", "json"))
    config = original["spec"]
    original_worker_env = json.loads(kubectl("get", "deployment/bifrost-worker", "-o", "json"))["spec"]["template"]["spec"]["containers"][0].get("env", [])
    source = (ROOT / "api/scripts/elastic_runtime_spike.py").read_text()
    try:
        patch("scaledobject/bifrost-worker", {"metadata": {"annotations": {"autoscaling.keda.sh/paused-replicas": "1"}}})
        wait_workers(1)
        if mode == "deadline":
            kubectl("set", "env", "deployment/bifrost-worker", "BIFROST_DRAIN_DEADLINE_SECONDS=1")
            kubectl("rollout", "status", "deployment/bifrost-worker", "--timeout=55s")
            wait_workers(1)
        # Pod readiness alone does not mean the preloaded execution template is ready.
        warmup = kubectl("exec", "-i", "deployment/bifrost-api", "--", "python", "-", "--count", "1", data=source)
        (out / "warmup.jsonl").write_text(warmup)
        (out / "before-memory.json").write_text(json.dumps(snapshot(), indent=2))
        args = ["--count", "100"]
        if mode == "sdk":
            args += ["--sdk"]
        elif mode == "cold":
            patch("scaledobject/bifrost-worker", {"spec": {"minReplicaCount": 0}, "metadata": {"annotations": {"autoscaling.keda.sh/paused-replicas": "0", "autoscaling.keda.sh/paused-scale-in": None}}})
            wait_workers(0)
            patch("scaledobject/bifrost-worker", {"metadata": {"annotations": {"autoscaling.keda.sh/paused-replicas": None}}})
            args = ["--count", "1"]
        elif mode in ("load", "burst"):
            patch("scaledobject/bifrost-worker", {"metadata": {"annotations": {"autoscaling.keda.sh/paused-replicas": None}}})
            count = "96" if mode == "load" else "32"
            args = ["--count", count, "--concurrency", count, "--delay", "4"]
        elif mode in ("drain", "deadline"):
            args = ["--count", "1", "--delay", "15", "--timeout", "45"]
            if mode == "deadline":
                args += ["--expect-error", "WorkerShutdown"]
        with (out / "workload.jsonl").open("w") as results, (out / "workload.stderr").open("w") as errors:
            process = subprocess.Popen(
                [str(LOCAL), "kubectl", "--", "-n", NS, "exec", "-i", "deployment/bifrost-api", "--", "python", "-", *args],
                stdin=subprocess.PIPE, stdout=results, stderr=errors, text=True,
            )
            assert process.stdin is not None
            process.stdin.write(source)
            process.stdin.close()
            started = time.monotonic()
            deleted = False
            timeline = []
            try:
                while process.poll() is None:
                    pods = workers()
                    timeline.append({"seconds": round(time.monotonic()-started, 2), "pods": [{"name": p['metadata']['name'], "phase": p.get('status', {}).get('phase'), "ready": any(c["type"] == "Ready" and c["status"] == "True" for c in p.get("status", {}).get("conditions", [])), "deleting": bool(p['metadata'].get('deletionTimestamp'))} for p in pods]})
                    if mode in ("drain", "deadline") and not deleted:
                        records = [json.loads(line) for line in (out / "workload.jsonl").read_text().splitlines() if line.startswith('{')]
                        submitted = next((r for r in records if r.get("event") == "submitted"), None)
                        if submitted:
                            active = kubectl("exec", "statefulset/redis", "--", "redis-cli", "EXISTS", f"bifrost:exec:{submitted['execution_id']}:active").strip()
                            if active == "1":
                                assert len(pods) == 1, "Drain experiment requires exactly one owning worker"
                                kubectl("delete", "pod", pods[0]["metadata"]["name"], "--wait=false")
                                deleted = True
                    if time.monotonic() - started > 240:
                        raise TimeoutError("Experiment exceeded 240 seconds")
                    time.sleep(1)
                if process.returncode:
                    raise RuntimeError(f"Workload failed; inspect {out}")
                if mode in ("drain", "deadline") and not deleted:
                    raise AssertionError("No active execution was observed before deletion")
                if mode == "load" and max(sum(p['ready'] and not p['deleting'] for p in t['pods']) for t in timeline) < 2:
                    raise AssertionError("Load did not trigger horizontal scale-out")
                if mode == "load":
                    completed = [json.loads(line) for line in (out / "workload.jsonl").read_text().splitlines() if line.startswith('{')]
                    owners = {r['worker'] for r in completed if r.get('event') == 'completed' and r.get('worker')}
                    if len(owners) < 2:
                        raise AssertionError("Fewer than two worker pods completed work")
            finally:
                if process.poll() is None:
                    process.terminate()
                    process.wait(timeout=10)
                (out / "replicas.json").write_text(json.dumps(timeline, indent=2))
        (out / "after-memory.json").write_text(json.dumps(snapshot(), indent=2))
    finally:
        try:
            if mode == "deadline":
                kubectl("patch", "deployment/bifrost-worker", "--type=json", "-p", json.dumps([{"op": "replace", "path": "/spec/template/spec/containers/0/env", "value": original_worker_env}]))
                kubectl("rollout", "status", "deployment/bifrost-worker", "--timeout=55s")
                wait_workers(1)
        finally:
            # Restore operator policy even if the environment rollout fails.
            annotations = original.get("metadata", {}).get("annotations", {})
            patch("scaledobject/bifrost-worker", {"spec": config, "metadata": {"annotations": {key: annotations.get(key) for key in ("autoscaling.keda.sh/paused-replicas", "autoscaling.keda.sh/paused-scale-in")}}})
    print(f"PASS {mode}: {out}", flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=["warm", "sdk", "cold", "burst", "load", "drain", "deadline"])
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    out = args.output or Path(f"/tmp/bifrost-k8s-experiment-{args.mode}-{time.time_ns()}")
    experiment(args.mode, out)


if __name__ == "__main__":
    main()
