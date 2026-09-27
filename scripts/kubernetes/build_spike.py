#!/usr/bin/env python3
"""Exercise real App builds in local Kind, optionally replacing their scheduler."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import time

from spike import LOCAL, NS, ROOT, kubectl

MEMORY = """import json
from pathlib import Path
p=Path('/sys/fs/cgroup')
s=dict(line.split() for line in (p/'memory.stat').read_text().splitlines())
c=int((p/'memory.current').read_text())
print(json.dumps({'current_bytes':c,'peak_bytes':int((p/'memory.peak').read_text()),'working_set_bytes':max(0,c-int(s.get('inactive_file',0)))}))
"""


def run(out: Path, restart_scheduler: bool) -> None:
    out.mkdir(parents=True, exist_ok=False)
    samples = []
    restarted = False
    started = time.monotonic()
    with (out / "workload.jsonl").open("w") as stdout, (out / "workload.stderr").open("w") as stderr, (ROOT / "api/scripts/kubernetes_build_spike.py").open() as fixture:
        process = subprocess.Popen([
            str(LOCAL), "kubectl", "--", "-n", NS, "exec", "-i", "deployment/bifrost-api",
            "--", "python", "-", "--hold-seconds", "30",
        ], stdin=fixture, stdout=stdout, stderr=stderr)
        try:
            while process.poll() is None:
                text = (out / "workload.jsonl").read_text()
                if restart_scheduler and not restarted and '"event": "builds_running"' in text:
                    kubectl("delete", "pod", "-l", "app.kubernetes.io/name=bifrost-scheduler", "--wait=false")
                    restarted = True
                for role in ("bifrost-platform-job", "bifrost-scheduler"):
                    pods = json.loads(kubectl("get", "pods", "-l", f"app.kubernetes.io/name={role}", "-o", "json"))["items"]
                    for pod in pods:
                        if pod.get("status", {}).get("phase") != "Running" or pod["metadata"].get("deletionTimestamp"):
                            continue
                        name = pod["metadata"]["name"]
                        try:
                            memory = json.loads(kubectl("exec", name, "--", "python", "-c", MEMORY))
                        except subprocess.CalledProcessError:
                            # A completed build may disappear between list and exec.
                            continue
                        samples.append({"seconds": round(time.monotonic() - started, 2), "role": role, "pod": name, **memory})
                if time.monotonic() - started > 900:
                    raise TimeoutError("Build experiment exceeded its overall deadline")
                time.sleep(2)
            if process.returncode:
                raise RuntimeError(f"Build experiment failed; inspect {out}")
            if restart_scheduler and not restarted:
                raise AssertionError("Build overlap was never observed for scheduler replacement")
        finally:
            if process.poll() is None:
                process.terminate()
                process.wait(timeout=10)
            (out / "memory.json").write_text(json.dumps(samples, indent=2))
            (out / "run.json").write_text(json.dumps({"scheduler_replaced": restarted, "elapsed_seconds": time.monotonic() - started}, indent=2))
    output = (out / "workload.jsonl").read_text()
    result = json.loads(output[output.rfind("\n{") + 1:])
    (out / "result.json").write_text(json.dumps(result, indent=2))
    print(f"PASS isolated builds: {out}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--restart-scheduler", action="store_true")
    args = parser.parse_args()
    run(args.output, args.restart_scheduler)
